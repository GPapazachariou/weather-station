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

    async def test_enqueue_batch_normalizes_timestamps_to_utc_iso8601(self, server_test_db: Path):
        """Batch ingestion normalizes timestamps to UTC ISO-8601 and fixes SQLite string filtering."""
        await app.init_database()

        app.write_queue = asyncio.Queue()
        writer = asyncio.create_task(app.db_writer_task())

        batch = [
            # 1. 'Z' suffix (1 AM UTC)
            {
                "station_id": "NORM-STN",
                "timestamp": "2026-10-02T01:00:00Z",
                "temperature": 20.0,
                "humidity": 50.0,
                "windspeed": 5.0,
            },
            # 2. +03:00 timezone offset (4 AM +03:00 == 1 AM UTC)
            {
                "station_id": "NORM-STN",
                "timestamp": "2026-10-02T04:00:00+03:00",
                "temperature": 21.0,
                "humidity": 55.0,
                "windspeed": 6.0,
            },
            # 3. -05:00 timezone offset (1 AM -05:00 == 6 AM UTC)
            {
                "station_id": "NORM-STN",
                "timestamp": "2026-10-02T01:00:00-05:00",
                "temperature": 22.0,
                "humidity": 60.0,
                "windspeed": 7.0,
            },
            # 4. Naive timestamp (assumed UTC)
            {
                "station_id": "NORM-STN",
                "timestamp": "2026-10-02T03:00:00",
                "temperature": 23.0,
                "humidity": 65.0,
                "windspeed": 8.0,
            },
        ]

        try:
            inserted = await app.enqueue_batch(batch)
            assert inserted == 4
        finally:
            await app.write_queue.put(app._WRITER_SENTINEL)
            await writer

        async with aiosqlite.connect(str(server_test_db)) as db:
            cursor = await db.execute(
                "SELECT id, timestamp FROM readings WHERE station_id = 'NORM-STN' ORDER BY id ASC"
            )
            rows = await cursor.fetchall()
            timestamps = [row[1] for row in rows]

            # Verify all stored timestamps are canonical UTC ISO-8601 strings
            assert timestamps == [
                "2026-10-02T01:00:00+00:00",
                "2026-10-02T01:00:00+00:00",
                "2026-10-02T06:00:00+00:00",
                "2026-10-02T03:00:00+00:00",
            ]

            # Test SQLite lexicographical filtering:
            # 1. Subsecond cutoff anomaly:
            # Cutoff is 01:00:00.123456+00:00.
            # Readings at 01:00:00+00:00 should NOT match.
            # (Without normalization, 'Z' > '.' caused raw '...Z' to wrongly match).
            cursor = await db.execute(
                "SELECT COUNT(*) FROM readings WHERE station_id = 'NORM-STN' AND timestamp >= ?",
                ("2026-10-02T01:00:00.123456+00:00",),
            )
            count = (await cursor.fetchone())[0]
            # Only the 03:00:00 and 06:00:00 readings should match (2 readings)
            assert count == 2

            # 2. Non-UTC offset anomaly:
            # Cutoff is 02:00:00 UTC.
            # Reading with raw timestamp "2026-10-02T04:00:00+03:00" is 01:00:00 UTC.
            # It should NOT match cutoff >= 02:00:00.
            cursor = await db.execute(
                "SELECT COUNT(*) FROM readings WHERE station_id = 'NORM-STN' AND timestamp >= ?",
                ("2026-10-02T02:00:00+00:00",),
            )
            count = (await cursor.fetchone())[0]
            assert count == 2


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


# ============================================================================
# 6. Bounded Queue, Backpressure, and ACK Timeout Tests
# ============================================================================


class TestBackpressureAndQueueBounds:
    """Tests for bounded write queue, backpressure responses, and writer ACK timeouts."""

    def test_queue_and_timeout_defaults(self):
        """Verify default bounds and timeouts."""
        assert app.QUEUE_MAXSIZE == 500
        assert app.WRITE_ENQUEUE_TIMEOUT == 2.0
        assert app.WRITE_ACK_TIMEOUT == 5.0

    async def test_enqueue_batch_raises_backpressure_when_queue_full(self):
        """When write_queue is at maxsize and enqueue times out, BackpressureError is raised."""
        original_queue = app.write_queue
        original_timeout = app.WRITE_ENQUEUE_TIMEOUT
        try:
            # Set a 1-item queue and tiny timeout
            app.write_queue = asyncio.Queue(maxsize=1)
            app.WRITE_ENQUEUE_TIMEOUT = 0.05

            # Fill queue to capacity
            dummy_future = asyncio.get_running_loop().create_future()
            await app.write_queue.put(([{"station_id": "FILL"}], dummy_future))
            assert app.write_queue.full()

            batch = [
                {
                    "station_id": "BLOCKED",
                    "timestamp": "2026-10-02T01:00:00Z",
                    "temperature": 20.0,
                    "humidity": 50.0,
                    "windspeed": 5.0,
                }
            ]

            with pytest.raises(app.BackpressureError, match="server_busy"):
                await app.enqueue_batch(batch)
        finally:
            app.write_queue = original_queue
            app.WRITE_ENQUEUE_TIMEOUT = original_timeout

    async def test_enqueue_batch_ack_timeout(self):
        """When writer task hangs or fails to resolve future, enqueue_batch raises TimeoutError."""
        original_queue = app.write_queue
        original_ack_timeout = app.WRITE_ACK_TIMEOUT
        try:
            app.write_queue = asyncio.Queue(maxsize=10)
            app.WRITE_ACK_TIMEOUT = 0.05  # 50ms ACK timeout

            batch = [
                {
                    "station_id": "HANGING",
                    "timestamp": "2026-10-02T01:00:00Z",
                    "temperature": 20.0,
                    "humidity": 50.0,
                    "windspeed": 5.0,
                }
            ]

            # Enqueue without running a writer task - future will never be resolved
            with pytest.raises(TimeoutError, match="write_ack_timeout"):
                await app.enqueue_batch(batch)

            # Verify future was cancelled to avoid leaking or setting result later
            queued_batch, queued_future = await app.write_queue.get()
            assert queued_future.cancelled()
        finally:
            app.write_queue = original_queue
            app.WRITE_ACK_TIMEOUT = original_ack_timeout

    async def test_tcp_backpressure_response_structure(self, server_test_db: Path):
        """TCP server returns structured backpressure response when queue is saturated."""
        import json

        await app.init_database()
        original_queue = app.write_queue
        original_timeout = app.WRITE_ENQUEUE_TIMEOUT

        try:
            # Capacity 1 queue with short enqueue timeout
            app.write_queue = asyncio.Queue(maxsize=1)
            app.WRITE_ENQUEUE_TIMEOUT = 0.05

            # Fill queue to saturation
            dummy_future = asyncio.get_running_loop().create_future()
            await app.write_queue.put(([{"station_id": "DUMMY"}], dummy_future))

            tcp_server = await asyncio.start_server(app.handle_client, "127.0.0.1", 0)
            port = tcp_server.sockets[0].getsockname()[1]
            reader, socket_writer = await asyncio.open_connection("127.0.0.1", port)

            try:
                batch = [
                    {
                        "station_id": "STN-BP",
                        "timestamp": "2026-10-02T01:00:00Z",
                        "temperature": 22.0,
                        "humidity": 50.0,
                        "windspeed": 5.0,
                    }
                ]
                socket_writer.write((json.dumps(batch) + "\n").encode())
                await socket_writer.drain()

                resp_line = await reader.readline()
                resp = json.loads(resp_line.decode())

                assert resp == {
                    "status": "error",
                    "code": "backpressure",
                    "reason": "server_busy",
                    "retry_after": 1,
                }
            finally:
                socket_writer.close()
                await socket_writer.wait_closed()
                tcp_server.close()
                await tcp_server.wait_closed()
        finally:
            app.write_queue = original_queue
            app.WRITE_ENQUEUE_TIMEOUT = original_timeout

    async def test_writer_delay_causes_backpressure_acceptance_criteria(self, server_test_db: Path):
        """
        Acceptance Criteria: Injecting a delay in the writer task causes server to respond with
        backpressure when queue exceeds maxsize, without crashing.
        """
        import json

        await app.init_database()
        original_queue = app.write_queue
        original_enqueue_timeout = app.WRITE_ENQUEUE_TIMEOUT

        try:
            # Set a small queue (maxsize=1) and short enqueue timeout
            app.write_queue = asyncio.Queue(maxsize=1)
            app.WRITE_ENQUEUE_TIMEOUT = 0.05

            # Slow writer task that sleeps 0.5s per batch
            async def slow_writer_task():
                async with aiosqlite.connect(app.DB_FILE):
                    while True:
                        item = await app.write_queue.get()
                        if item == app._WRITER_SENTINEL:
                            break
                        batch, fut = item
                        await asyncio.sleep(0.5)
                        if not fut.cancelled() and not fut.done():
                            fut.set_result(len(batch))

            writer = asyncio.create_task(slow_writer_task())

            tcp_server = await asyncio.start_server(app.handle_client, "127.0.0.1", 0)
            port = tcp_server.sockets[0].getsockname()[1]

            try:
                # 3 concurrent client connections to the server
                r1, w1 = await asyncio.open_connection("127.0.0.1", port)
                r2, w2 = await asyncio.open_connection("127.0.0.1", port)
                r3, w3 = await asyncio.open_connection("127.0.0.1", port)

                try:
                    batch_1 = [{"station_id": "SLOW-1", "timestamp": "2026-10-02T01:00:00Z", "temperature": 20.0, "humidity": 50.0, "windspeed": 5.0}]
                    batch_2 = [{"station_id": "SLOW-2", "timestamp": "2026-10-02T01:01:00Z", "temperature": 21.0, "humidity": 51.0, "windspeed": 6.0}]
                    batch_3 = [{"station_id": "SLOW-3", "timestamp": "2026-10-02T01:02:00Z", "temperature": 22.0, "humidity": 52.0, "windspeed": 7.0}]

                    # Client 1 sends batch 1 (writer starts processing and sleeps 0.5s)
                    w1.write((json.dumps(batch_1) + "\n").encode())
                    await w1.drain()
                    await asyncio.sleep(0.02)

                    # Client 2 sends batch 2 (fills the 1-slot queue while writer works on batch 1)
                    w2.write((json.dumps(batch_2) + "\n").encode())
                    await w2.drain()
                    await asyncio.sleep(0.02)

                    # Client 3 sends batch 3 (queue is full, times out after 0.05s -> backpressure!)
                    w3.write((json.dumps(batch_3) + "\n").encode())
                    await w3.drain()

                    # Client 3 receives backpressure error response
                    resp_3 = json.loads((await r3.readline()).decode())
                    assert resp_3["status"] == "error"
                    assert resp_3["code"] == "backpressure"
                    assert resp_3["reason"] == "server_busy"
                    assert resp_3["retry_after"] == 1

                    # Client 1 and 2 complete successfully once writer finishes
                    resp_1 = json.loads((await r1.readline()).decode())
                    assert resp_1["status"] == "ok"

                    resp_2 = json.loads((await r2.readline()).decode())
                    assert resp_2["status"] == "ok"
                finally:
                    w1.close()
                    await w1.wait_closed()
                    w2.close()
                    await w2.wait_closed()
                    w3.close()
                    await w3.wait_closed()
            finally:
                tcp_server.close()
                await tcp_server.wait_closed()
                await app.write_queue.put(app._WRITER_SENTINEL)
                await writer
        finally:
            app.write_queue = original_queue
            app.WRITE_ENQUEUE_TIMEOUT = original_enqueue_timeout
