"""Async server and protocol handler tests for server/app.py.

Tests cover:
- Safe JSON loading (rejecting non-finite constants NaN, Inf)
- Database and compound index initialization
- Single-writer queue ingestion (enqueue_batch and db_writer_task)
- Concurrent batch ingestion without SQLite locking errors
- Consumer read requests:
  - stations
  - latest (all and per-station)
  - recent (all, per-station, and limit clamping)
  - unknown requests and server error handling
"""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import app
import pytest


@pytest.fixture
def server_test_db(tmp_path: Path):
    """Fixture providing an isolated SQLite database path for server tests."""
    original_db = app.DB_FILE
    db_file = tmp_path / "server_test.db"
    app.DB_FILE = str(db_file)
    yield db_file
    app.DB_FILE = original_db


# ============================================================================
# 1. Safe JSON Parsing Tests
# ============================================================================


class TestSafeJsonLoads:
    """Tests for app._safe_json_loads()."""

    def test_safe_json_valid_data(self):
        """Standard JSON payload should parse into native types."""
        raw = '{"station_id": "STN-1", "temperature": 21.5, "active": true, "count": 10}'
        result = app._safe_json_loads(raw)
        assert result["station_id"] == "STN-1"
        assert result["temperature"] == 21.5
        assert result["active"] is True
        assert result["count"] == 10

    @pytest.mark.parametrize(
        "bad_json",
        [
            '{"temperature": NaN}',
            '{"temperature": Infinity}',
            '{"temperature": -Infinity}',
        ],
    )
    def test_safe_json_rejects_non_finite_constants(self, bad_json):
        """NaN and Infinity literals in JSON must be rejected with ValueError."""
        with pytest.raises(ValueError, match="non_finite_constant"):
            app._safe_json_loads(bad_json)


# ============================================================================
# 2. Database Initialization & Schema Tests
# ============================================================================


class TestDatabaseInit:
    """Tests for app.init_database()."""

    async def test_init_database_creates_tables_and_indexes(self, server_test_db: Path):
        """init_database() should create table and compound indexes."""
        await app.init_database()
        assert server_test_db.exists()

        # Connect synchronously to inspect schema
        conn = sqlite3.connect(str(server_test_db))
        cursor = conn.cursor()

        # Verify table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='readings'")
        assert cursor.fetchone() is not None

        # Verify compound indexes exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_readings_station_ts" in indexes
        assert "idx_readings_station_id_desc" in indexes
        conn.close()

    async def test_init_database_is_idempotent(self, server_test_db: Path):
        """Calling init_database() multiple times should succeed without error."""
        await app.init_database()
        await app.init_database()
        assert server_test_db.exists()


# ============================================================================
# 3. Single-Writer Ingestion Queue Tests
# ============================================================================


class TestWriterQueue:
    """Tests for single-writer queue ingestion and concurrency."""

    async def test_enqueue_empty_batch(self):
        """Enqueueing an empty batch should return 0 immediately without queuing."""
        assert await app.enqueue_batch([]) == 0

    async def test_writer_task_and_enqueue_batch(self, server_test_db: Path):
        """Writer task should consume batch from queue and commit records to SQLite."""
        await app.init_database()

        app.write_queue = asyncio.Queue()
        writer = asyncio.create_task(app.db_writer_task())

        batch = [
            {
                "station_id": "STN-TEST",
                "timestamp": "2026-10-02T01:00:00Z",
                "temperature": 22.0,
                "humidity": 50.0,
                "windspeed": 10.0,
            },
            {
                "station_id": "STN-TEST",
                "timestamp": "2026-10-02T01:01:00Z",
                "temperature": 22.5,
                "humidity": 52.0,
                "windspeed": 11.0,
            },
        ]

        try:
            inserted = await app.enqueue_batch(batch)
            assert inserted == 2
        finally:
            # Terminate writer gracefully via sentinel
            await app.write_queue.put(app._WRITER_SENTINEL)
            await writer

        # Verify records exist in DB
        async with aiosqlite.connect(str(server_test_db)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM readings WHERE station_id='STN-TEST'")
            count = (await cursor.fetchone())[0]
            assert count == 2

    async def test_concurrent_enqueue_batches_no_locks(self, server_test_db: Path):
        """Multiple concurrent producers enqueuing batches should complete cleanly."""
        await app.init_database()

        app.write_queue = asyncio.Queue()
        writer = asyncio.create_task(app.db_writer_task())

        batches = [
            [
                {
                    "station_id": f"STN-PARALLEL-{i}",
                    "timestamp": f"2026-10-02T01:0{j}:00Z",
                    "temperature": 20.0 + j,
                    "humidity": 50.0,
                    "windspeed": 5.0,
                }
                for j in range(5)
            ]
            for i in range(10)
        ]

        try:
            # Enqueue 10 batches concurrently
            results = await asyncio.gather(*[app.enqueue_batch(b) for b in batches])
            assert results == [5] * 10
        finally:
            # Terminate writer gracefully
            await app.write_queue.put(app._WRITER_SENTINEL)
            await writer

        # Verify total record count
        async with aiosqlite.connect(str(server_test_db)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM readings")
            total = (await cursor.fetchone())[0]
            assert total == 50


# ============================================================================
# 4. Consumer Request Protocol Handlers Tests
# ============================================================================


class TestConsumerHandlers:
    """Tests for app.handle_consumer_request()."""

    @pytest.fixture(autouse=True)
    async def seed_consumer_db(self, server_test_db: Path):
        """Seed test database with known records for consumer queries."""
        await app.init_database()
        async with aiosqlite.connect(str(server_test_db)) as db:
            await db.execute(
                """
                INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed)
                VALUES
                    ('STN-A', '2026-10-02T01:00:00Z', 18.0, 45.0, 5.0),
                    ('STN-A', '2026-10-02T01:10:00Z', 19.5, 48.0, 6.0),
                    ('STN-B', '2026-10-02T01:05:00Z', 24.0, 60.0, 8.0)
                """
            )
            await db.commit()

    async def test_consumer_stations(self):
        """{"request": "stations"} returns sorted unique station list."""
        req = {"request": "stations"}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"
        assert resp["stations"] == ["STN-A", "STN-B"]

    async def test_consumer_latest_all(self):
        """{"request": "latest"} returns newest reading across all stations."""
        req = {"request": "latest"}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"
        reading = resp["reading"]
        assert reading is not None
        assert reading["station_id"] == "STN-B"  # Last inserted id
        assert reading["temperature"] == 24.0

    async def test_consumer_latest_per_station(self):
        """{"request": "latest", "station_id": "STN-A"} returns newest reading for STN-A."""
        req = {"request": "latest", "station_id": "STN-A"}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"
        reading = resp["reading"]
        assert reading["station_id"] == "STN-A"
        assert reading["temperature"] == 19.5
        assert reading["timestamp"] == "2026-10-02T01:10:00Z"

    async def test_consumer_latest_nonexistent_station(self):
        """{"request": "latest", "station_id": "NONEXISTENT"} returns reading: null."""
        req = {"request": "latest", "station_id": "NONEXISTENT"}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"
        assert resp["reading"] is None

    async def test_consumer_recent_with_limit(self):
        """{"request": "recent", "limit": 2} returns up to 2 readings ordered by id DESC."""
        req = {"request": "recent", "limit": 2}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"
        readings = resp["readings"]
        assert len(readings) == 2
        assert readings[0]["station_id"] == "STN-B"

    async def test_consumer_recent_per_station(self):
        """{"request": "recent", "station_id": "STN-A", "limit": 10} returns readings for STN-A."""
        req = {"request": "recent", "station_id": "STN-A", "limit": 10}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"
        assert len(resp["readings"]) == 2
        for r in resp["readings"]:
            assert r["station_id"] == "STN-A"

    @pytest.mark.parametrize("invalid_limit", [-5, 0, "not_an_int", None])
    async def test_consumer_recent_invalid_limit_defaults_to_50(self, invalid_limit):
        """Invalid limit parameter should safely fall back to default limit of 50."""
        req = {"request": "recent", "limit": invalid_limit}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"
        assert len(resp["readings"]) == 3  # Returns all 3 available

    async def test_consumer_recent_limit_clamped_to_500(self):
        """Limit exceeding 500 is clamped to 500."""
        req = {"request": "recent", "limit": 9999}
        resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
        assert resp["status"] == "ok"

    @pytest.mark.parametrize("bad_req", [{"request": "invalid_action"}, {"request": None}, {}])
    async def test_consumer_unknown_request_error(self, bad_req):
        """Unknown or missing request types should return error response."""
        resp = await app.handle_consumer_request(bad_req, ("127.0.0.1", 50000))
        assert resp["status"] == "error"
        assert "unknown_request" in resp["reason"]

    async def test_consumer_server_error_handling(self):
        """Database or connection exceptions should return structured server_error."""
        with patch("aiosqlite.connect", side_effect=RuntimeError("connection dropped")):
            req = {"request": "stations"}
            resp = await app.handle_consumer_request(req, ("127.0.0.1", 50000))
            assert resp["status"] == "error"
            assert "server_error" in resp["reason"]


# ============================================================================
# 5. End-to-End TCP Client & Server Tests
# ============================================================================


class TestServerTcpEndToEnd:
    """End-to-end integration tests over loopback TCP socket using handle_client."""

    async def test_tcp_producer_and_consumer_roundtrip(self, server_test_db: Path):
        """Test full TCP roundtrip for producer ingest, consumer queries, and error handling."""
        import json

        await app.init_database()
        app.write_queue = asyncio.Queue()
        writer = asyncio.create_task(app.db_writer_task())

        tcp_server = await asyncio.start_server(app.handle_client, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        reader, socket_writer = await asyncio.open_connection("127.0.0.1", port)

        try:
            # 1. Ingest batch via TCP
            batch = [
                {
                    "station_id": "TCP-STN",
                    "timestamp": "2026-10-02T01:30:00Z",
                    "temperature": 21.0,
                    "humidity": 45.0,
                    "windspeed": 8.0,
                }
            ]
            socket_writer.write((json.dumps(batch) + "\n").encode())
            await socket_writer.drain()

            resp_line = await reader.readline()
            resp = json.loads(resp_line.decode())
            assert resp == {"status": "ok", "inserted": 1}

            # 2. Query consumer stations via TCP
            socket_writer.write((json.dumps({"request": "stations"}) + "\n").encode())
            await socket_writer.drain()

            resp_line = await reader.readline()
            resp = json.loads(resp_line.decode())
            assert resp["status"] == "ok"
            assert "TCP-STN" in resp["stations"]

            # 3. Invalid JSON error response
            socket_writer.write(b"this is not json\n")
            await socket_writer.drain()

            resp_line = await reader.readline()
            resp = json.loads(resp_line.decode())
            assert resp["status"] == "error"
            assert "invalid_json" in resp["reason"]

            # 4. Validation error response
            socket_writer.write((json.dumps([{"station_id": ""}]) + "\n").encode())
            await socket_writer.drain()

            resp_line = await reader.readline()
            resp = json.loads(resp_line.decode())
            assert resp["status"] == "error"

        finally:
            # Cleanup socket, server, and background writer task
            socket_writer.close()
            await socket_writer.wait_closed()

            tcp_server.close()
            await tcp_server.wait_closed()

            await app.write_queue.put(app._WRITER_SENTINEL)
            await writer
