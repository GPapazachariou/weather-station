"""Tests for Station Client logic and resilience algorithms (station_client/client.py).

Tests cover:
- Mathematical utility functions (clamp, stable_hash_int, mean_for)
- Reading and batch generation (ranges, deterministic seeds, protocol schema compatibility)
- Buffer queue semantics (FIFO retention and drop-oldest on saturation)
- Exponential backoff and jitter calculations
- Configuration loading and argument validation
"""

from collections import deque

import client
import pytest
from protocol import validate_batch

# ============================================================================
# 1. Sensor Reading Generation & Math Helpers
# ============================================================================


class TestSensorGeneration:
    """Tests for sensor math, clamping, and reading generators."""

    def test_clamp(self):
        """clamp(x, lo, hi) should restrict values to the closed interval [lo, hi]."""
        assert client.clamp(5, 0, 10) == 5
        assert client.clamp(-1, 0, 10) == 0
        assert client.clamp(15, 0, 10) == 10
        assert client.clamp(0.0, -10.0, 40.0) == 0.0

    def test_stable_hash_int_is_deterministic(self):
        """stable_hash_int should produce the same integer for identical string inputs."""
        h1 = client.stable_hash_int("STATION-001:temperature")
        h2 = client.stable_hash_int("STATION-001:temperature")
        assert h1 == h2
        assert isinstance(h1, int)
        assert h1 > 0

    def test_mean_for_is_deterministic_and_bounded(self):
        """mean_for should return identical float within [lo, hi] for same station & metric."""
        m1 = client.mean_for("STATION-001", "temperature", -10.0, 40.0)
        m2 = client.mean_for("STATION-001", "temperature", -10.0, 40.0)
        assert m1 == m2
        assert -10.0 <= m1 <= 40.0

        # Different station or metric produces different mean
        m3 = client.mean_for("STATION-002", "temperature", -10.0, 40.0)
        assert m1 != m3

    @pytest.mark.parametrize(
        "metric,lo,hi",
        [
            ("temperature", -10.0, 40.0),
            ("humidity", 0.0, 100.0),
            ("windspeed", 0.0, 50.0),
        ],
    )
    def test_value_for_always_within_bounds(self, metric, lo, hi):
        """value_for() must always stay strictly within valid bounds over multiple samples."""
        for _ in range(50):
            val = client.value_for("STATION-001", metric)
            assert lo <= val <= hi, f"{metric} value {val} out of bounds [{lo}, {hi}]"

    def test_value_for_unknown_metric_raises_error(self):
        """Unknown metric name must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown metric"):
            client.value_for("STATION-001", "pressure")

    def test_generate_reading_conforms_to_protocol(self):
        """Generated reading must pass protocol.validate_batch without errors."""
        reading = client.generate_reading("STATION-TEST-001")
        assert reading["station_id"] == "STATION-TEST-001"
        assert "seq" in reading
        assert reading["seq"] == 0
        assert "timestamp" in reading
        assert "temperature" in reading
        assert "humidity" in reading
        assert "windspeed" in reading

        # Assert full validation passes
        assert validate_batch([reading]) is None

    def test_generate_reading_custom_seq(self):
        """generate_reading(station_id, seq) correctly sets seq."""
        reading = client.generate_reading("STATION-TEST-001", seq=42)
        assert reading["seq"] == 42
        assert validate_batch([reading]) is None

    def test_generate_batch_size_and_validity(self):
        """generate_batch(size) generates requested quantity, all valid."""
        batch = client.generate_batch("STATION-TEST-002", 5)
        assert len(batch) == 5
        assert validate_batch(batch) is None

    def test_generate_batch_monotonic_seq(self):
        """generate_batch assigns consecutive sequence numbers starting from start_seq."""
        batch = client.generate_batch("STATION-TEST-002", 5, start_seq=10)
        assert len(batch) == 5
        assert [r["seq"] for r in batch] == [10, 11, 12, 13, 14]
        assert validate_batch(batch) is None

    def test_sequence_tracker_monotonicity(self):
        """SequenceTracker correctly dispenses contiguous sequence ranges."""
        tracker = client.SequenceTracker(initial_seq=100)
        assert tracker.next_seq_range(5) == 100
        assert tracker.current_seq == 105
        assert tracker.next_seq_range(3) == 105
        assert tracker.current_seq == 108


# ============================================================================
# 2. Buffer Semantics (Drop-Oldest on Overflow)
# ============================================================================


class TestBufferSemantics:
    """Tests for client.buffer_batch() overflow and FIFO behavior."""

    def test_buffer_fifo_order(self):
        """Buffer deque should preserve FIFO order during normal enqueue/dequeue."""
        buf = deque()
        batch_1 = client.generate_batch("STATION-001", 1)
        batch_2 = client.generate_batch("STATION-001", 1)

        dropped1 = client.buffer_batch(buf, batch_1, max_capacity=5)
        dropped2 = client.buffer_batch(buf, batch_2, max_capacity=5)

        assert dropped1 is None
        assert dropped2 is None
        assert buf.popleft() == batch_1
        assert buf.popleft() == batch_2
        assert len(buf) == 0

    def test_buffer_drop_oldest_on_capacity(self):
        """
        When buffer reaches max_capacity, appending a new batch
        must drop the oldest batch (popleft) to keep memory bounded.
        """
        max_capacity = 3
        buf = deque()

        # Enqueue batches 0, 1, 2 (fills buffer to capacity)
        batches = [client.generate_batch("STATION-001", 1) for _ in range(5)]
        for b in batches[:max_capacity]:
            dropped = client.buffer_batch(buf, b, max_capacity)
            assert dropped is None

        assert len(buf) == max_capacity

        # Enqueue batch 3 with drop-oldest logic
        dropped_3 = client.buffer_batch(buf, batches[3], max_capacity)
        assert dropped_3 == batches[0]
        assert len(buf) == max_capacity

        # Enqueue batch 4 with drop-oldest logic
        dropped_4 = client.buffer_batch(buf, batches[4], max_capacity)
        assert dropped_4 == batches[1]
        assert len(buf) == max_capacity

        # Remaining in buffer must be batches 2, 3, 4
        assert buf.popleft() == batches[2]
        assert buf.popleft() == batches[3]
        assert buf.popleft() == batches[4]


# ============================================================================
# 3. Exponential Backoff & Jitter Simulation
# ============================================================================


class TestBackoffCalculation:
    """Tests for exponential backoff doubling, max ceiling, and jitter."""

    def test_exponential_backoff_progression(self):
        """Backoff should double up to max_backoff using client.calculate_backoff."""
        base_backoff = 1.0
        max_backoff = 16.0
        current_backoff = base_backoff

        expected_sequence = [1.0, 2.0, 4.0, 8.0, 16.0, 16.0, 16.0]
        actual_sequence = [current_backoff]

        for _ in range(len(expected_sequence) - 1):
            current_backoff = client.calculate_backoff(current_backoff, max_backoff)
            actual_sequence.append(current_backoff)

        assert actual_sequence == expected_sequence

    def test_jitter_is_non_negative_and_bounded(self):
        """client.calculate_jitter should return a random float in [0, backoff_jitter]."""
        backoff_jitter = 1.0
        for _ in range(50):
            jitter = client.calculate_jitter(backoff_jitter)
            assert 0.0 <= jitter <= backoff_jitter


# ============================================================================
# 4. Configuration Loading Tests
# ============================================================================


class TestClientConfig:
    """Tests for client.load_config() argument and environment variable handling."""

    def test_default_config_loading(self, monkeypatch):
        """With no CLI args, no env vars, and empty hostname, defaults should be loaded."""
        monkeypatch.setattr("sys.argv", ["client.py"])
        # Clear any existing WEATHER_STATION_ env vars and HOSTNAME
        for k in list(client.os.environ.keys()):
            if k.startswith("WEATHER_STATION_"):
                monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("HOSTNAME", raising=False)
        monkeypatch.setattr("client.socket.gethostname", lambda: "")

        cfg = client.load_config()
        assert cfg["server_host"] == client.DEFAULT_SERVER_HOST
        assert cfg["server_port"] == client.DEFAULT_SERVER_PORT
        assert cfg["station_id"] == client.DEFAULT_STATION_ID
        assert cfg["batch_size_min"] == client.DEFAULT_BATCH_SIZE_MIN
        assert cfg["batch_size_max"] == client.DEFAULT_BATCH_SIZE_MAX

    def test_hostname_env_fallback(self, monkeypatch):
        """When WEATHER_STATION_ID is unset, station_id falls back to HOSTNAME env var."""
        monkeypatch.setattr("sys.argv", ["client.py"])
        monkeypatch.delenv("WEATHER_STATION_ID", raising=False)
        monkeypatch.setenv("HOSTNAME", "a1b2c3d4e5f6")

        cfg = client.load_config()
        assert cfg["station_id"] == "a1b2c3d4e5f6"
        assert validate_batch([client.generate_reading(cfg["station_id"])]) is None

    def test_socket_gethostname_fallback_and_sanitization(self, monkeypatch):
        """When WEATHER_STATION_ID and HOSTNAME are unset, falls back to sanitized socket.gethostname()."""
        monkeypatch.setattr("sys.argv", ["client.py"])
        monkeypatch.delenv("WEATHER_STATION_ID", raising=False)
        monkeypatch.delenv("HOSTNAME", raising=False)
        monkeypatch.setattr("client.socket.gethostname", lambda: "my.station.host.local")

        cfg = client.load_config()
        assert cfg["station_id"] == "my-station-host-local"
        assert validate_batch([client.generate_reading(cfg["station_id"])]) is None

    def test_resolve_station_id_invalid_hostname_falls_back_to_default(self, monkeypatch):
        """If hostname contains only invalid characters or is empty, falls back to DEFAULT_STATION_ID."""
        monkeypatch.delenv("HOSTNAME", raising=False)
        monkeypatch.setattr("client.socket.gethostname", lambda: "...")
        assert client.resolve_station_id() == client.DEFAULT_STATION_ID

    def test_env_var_overrides_defaults(self, monkeypatch):
        """Environment variables should override defaults and HOSTNAME when CLI args are absent."""
        monkeypatch.setattr("sys.argv", ["client.py"])
        monkeypatch.setenv("HOSTNAME", "container-short-id")
        monkeypatch.setenv("WEATHER_STATION_HOST", "weather.remote.server")
        monkeypatch.setenv("WEATHER_STATION_PORT", "9999")
        monkeypatch.setenv("WEATHER_STATION_ID", "STN-ENV-01")

        cfg = client.load_config()
        assert cfg["server_host"] == "weather.remote.server"
        assert cfg["server_port"] == 9999
        assert cfg["station_id"] == "STN-ENV-01"

    def test_cli_args_override_env_vars(self, monkeypatch):
        """CLI arguments should have highest precedence over env vars and defaults."""
        monkeypatch.setenv("WEATHER_STATION_HOST", "env.host")
        monkeypatch.setenv("WEATHER_STATION_ID", "STN-ENV-01")
        monkeypatch.setenv("HOSTNAME", "container-short-id")
        monkeypatch.setattr(
            "sys.argv",
            [
                "client.py",
                "--host",
                "cli.host",
                "--port",
                "8888",
                "--station-id",
                "STN-CLI",
                "--batch-min",
                "2",
                "--batch-max",
                "4",
            ],
        )

        cfg = client.load_config()
        assert cfg["server_host"] == "cli.host"
        assert cfg["server_port"] == 8888
        assert cfg["station_id"] == "STN-CLI"
        assert cfg["batch_size_min"] == 2
        assert cfg["batch_size_max"] == 4

    def test_invalid_cli_arguments_exit(self, monkeypatch):
        """Invalid CLI parameters (e.g. batch_min > batch_max) should trigger parser error."""
        monkeypatch.setattr("sys.argv", ["client.py", "--batch-min", "10", "--batch-max", "5"])
        with pytest.raises(SystemExit):
            client.load_config()


# ============================================================================
# 5. Client Backpressure Handling & Buffering Tests
# ============================================================================


class TestClientBackpressure:
    """Tests for client backpressure detection, jittered backoff, and local buffering."""

    @pytest.mark.parametrize(
        "resp,expected",
        [
            ({"status": "error", "code": "backpressure", "reason": "server_busy", "retry_after": 1}, True),
            ({"status": "error", "reason": "server_busy"}, True),
            ({"status": "error", "code": "backpressure"}, True),
            ({"status": "ok", "inserted": 5}, False),
            ({"status": "error", "reason": "invalid_json"}, False),
            ("not a dict", False),
            (None, False),
        ],
    )
    def test_is_backpressure_response(self, resp, expected):
        """is_backpressure_response should accurately identify server busy / backpressure responses."""
        assert client.is_backpressure_response(resp) is expected

    async def test_flush_buffer_success(self):
        """flush_buffer should send all buffered batches when server responds ok."""
        import json
        from unittest.mock import AsyncMock, MagicMock

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()
        mock_writer.write = MagicMock()

        mock_reader.readline.side_effect = [
            (json.dumps({"status": "ok", "inserted": 1}) + "\n").encode(),
            (json.dumps({"status": "ok", "inserted": 1}) + "\n").encode(),
        ]

        buf = deque([
            [{"station_id": "STN-1", "temperature": 20.0}],
            [{"station_id": "STN-1", "temperature": 21.0}],
        ])

        status, resp = await client.flush_buffer(
            mock_reader, mock_writer, buf, "STN-1", 5.0, 5.0, lambda: False
        )

        assert status == "ok"
        assert resp is None
        assert len(buf) == 0

    async def test_flush_buffer_backpressure_restores_batch(self):
        """When flush encounters backpressure, the popped batch must be restored at head of buffer."""
        import json
        from unittest.mock import AsyncMock, MagicMock

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()
        mock_writer.write = MagicMock()

        bp_response = {
            "status": "error",
            "code": "backpressure",
            "reason": "server_busy",
            "retry_after": 1,
        }
        mock_reader.readline.side_effect = [
            (json.dumps(bp_response) + "\n").encode(),
        ]

        batch_1 = [{"station_id": "STN-1", "temp": 1}]
        batch_2 = [{"station_id": "STN-1", "temp": 2}]
        buf = deque([batch_1, batch_2])

        status, resp = await client.flush_buffer(
            mock_reader, mock_writer, buf, "STN-1", 5.0, 5.0, lambda: False
        )

        assert status == "backpressure"
        assert resp == bp_response
        # Must retain original order with batch_1 at the front
        assert len(buf) == 2
        assert buf[0] == batch_1
        assert buf[1] == batch_2

    async def test_flush_buffer_connection_error_restores_batch(self):
        """When flush encounters a connection drop or timeout, the popped batch must be restored at head of buffer."""
        from unittest.mock import AsyncMock, MagicMock

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()
        mock_writer.write = MagicMock()
        mock_writer.drain.side_effect = ConnectionResetError("Connection reset by peer")

        batch_1 = [{"station_id": "STN-1", "temp": 1}]
        batch_2 = [{"station_id": "STN-1", "temp": 2}]
        buf = deque([batch_1, batch_2])

        with pytest.raises(ConnectionResetError):
            await client.flush_buffer(
                mock_reader, mock_writer, buf, "STN-1", 5.0, 5.0, lambda: False
            )

        # Batch 1 must be restored at the front of the deque without data loss
        assert len(buf) == 2
        assert buf[0] == batch_1
        assert buf[1] == batch_2

    async def test_wait_backoff_period_buffers_batches(self):
        """wait_backoff_period should sleep and buffer batches periodically."""
        buf = deque()
        # Wait 0.15s with 0.05s interval -> generates batches
        await client.wait_backoff_period(
            wait_time=0.12,
            batch_interval=0.05,
            batch_size_min=1,
            batch_size_max=1,
            station_id="STN-TEST",
            buffer=buf,
            max_buffer_records=10,
            shutdown_check=lambda: False,
        )
        assert len(buf) >= 1

    async def test_connected_client_buffers_on_backpressure_and_recovers(self):
        """
        Acceptance Criteria: Connected client buffers unsent batches locally when server responds
        with backpressure, backs off, and retries once queue clears.
        """
        import asyncio
        import json

        # Simulated server that responds with backpressure on first request, ok on second
        request_count = 0

        async def mock_server_handler(reader, writer):
            nonlocal request_count
            while True:
                line = await reader.readline()
                if not line:
                    break
                request_count += 1
                if request_count == 1:
                    resp = {
                        "status": "error",
                        "code": "backpressure",
                        "reason": "server_busy",
                        "retry_after": 0.05,
                    }
                else:
                    resp = {"status": "ok", "inserted": 1}
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_server_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        buffer = deque()

        try:
            # 1. Send first batch -> server returns backpressure
            batch_1 = client.generate_batch("STN-AUTO", 1)
            writer.write((json.dumps(batch_1) + "\n").encode())
            await writer.drain()

            resp_line = await reader.readline()
            resp = json.loads(resp_line.decode())
            assert client.is_backpressure_response(resp)

            # Client buffers the unsent batch
            client.buffer_batch(buffer, batch_1, 100)
            assert len(buffer) == 1
            assert buffer[0] == batch_1

            # 2. Flush buffer now that server accepts writes
            flush_status, _ = await client.flush_buffer(
                reader, writer, buffer, "STN-AUTO", 2.0, 2.0, lambda: False
            )
            assert flush_status == "ok"
            assert len(buffer) == 0
            assert request_count == 2
        finally:
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()

    async def test_wait_backoff_period_with_sequence_tracker(self):
        """wait_backoff_period increments SequenceTracker monotonically across buffered batches."""
        buf = deque()
        tracker = client.SequenceTracker(initial_seq=50)

        await client.wait_backoff_period(
            wait_time=0.12,
            batch_interval=0.04,
            batch_size_min=2,
            batch_size_max=2,
            station_id="STN-SEQ-TEST",
            buffer=buf,
            max_buffer_records=10,
            shutdown_check=lambda: False,
            seq_tracker=tracker,
        )

        assert len(buf) >= 2
        batch0 = buf[0]
        batch1 = buf[1]
        assert batch0[0]["seq"] == 50
        assert batch0[1]["seq"] == 51
        assert batch1[0]["seq"] == 52
        assert batch1[1]["seq"] == 53
        assert tracker.current_seq >= 54

    async def test_fetch_latest_seq_roundtrip(self):
        """fetch_latest_seq queries latest_seq from server and parses response."""
        import asyncio
        import json

        async def mock_handler(reader, writer):
            line = await reader.readline()
            req = json.loads(line.decode())
            assert req["request"] == "latest_seq"
            assert req["station_id"] == "STN-TEST"
            resp = {"status": "ok", "station_id": "STN-TEST", "latest_seq": 88}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        try:
            seq = await client.fetch_latest_seq(reader, writer, "STN-TEST", timeout=2.0)
            assert seq == 88
        finally:
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()

    async def test_disconnect_and_reconnection_idempotency_simulation(self, tmp_path):
        """
        Acceptance Criteria: Client disconnect / reconnection simulations under stress
        show 0 duplicate records in database when at-least-once retries resend batches.
        """
        import asyncio
        import json

        import aiosqlite

        from server import app

        db_file = tmp_path / "idempotent_test.db"
        app.DB_FILE = str(db_file)
        await app.init_database()
        app.write_queue = asyncio.Queue()
        writer_task = asyncio.create_task(app.db_writer_task())

        tcp_server = await asyncio.start_server(app.handle_client, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        buffer = deque()
        tracker = client.SequenceTracker(initial_seq=1)

        # 1. Generate 3 batches with consecutive monotonic sequence numbers
        batch_1 = client.generate_batch("STN-RECONNECT", 2, start_seq=tracker.next_seq_range(2))
        batch_2 = client.generate_batch("STN-RECONNECT", 2, start_seq=tracker.next_seq_range(2))
        batch_3 = client.generate_batch("STN-RECONNECT", 2, start_seq=tracker.next_seq_range(2))

        try:
            # First connection: send batch 1 and batch 2
            r1, w1 = await asyncio.open_connection("127.0.0.1", port)
            w1.write((json.dumps(batch_1) + "\n").encode())
            await w1.drain()
            resp1 = json.loads((await r1.readline()).decode())
            assert resp1["status"] == "ok"
            assert resp1["inserted"] == 2
            assert resp1["duplicates"] == 0

            # Batch 2 is sent, server writes it, but connection drops abruptly before ACK is read!
            w1.write((json.dumps(batch_2) + "\n").encode())
            await w1.drain()
            # Give server a moment to ingest
            await asyncio.sleep(0.05)
            # Abruptly close socket (simulating network partition / crash before ACK)
            w1.close()
            await w1.wait_closed()

            # Client buffer recovers batch_2 (at-least-once retry policy) and also has batch_3
            buffer.append(batch_2)
            buffer.append(batch_3)

            # Reconnection: flush buffer to server
            r2, w2 = await asyncio.open_connection("127.0.0.1", port)
            status, _ = await client.flush_buffer(
                r2, w2, buffer, "STN-RECONNECT", drain_timeout=5.0, response_timeout=5.0, shutdown_check=lambda: False
            )
            assert status == "ok"
            assert len(buffer) == 0
            w2.close()
            await w2.wait_closed()

            # Verify database: exactly 6 records total (batch 1, 2, 3), ZERO duplicates!
            async with aiosqlite.connect(str(db_file)) as db:
                cursor = await db.execute(
                    "SELECT seq, COUNT(*) FROM readings WHERE station_id = 'STN-RECONNECT' GROUP BY seq"
                )
                rows = await cursor.fetchall()
                # Each seq 1..6 must appear EXACTLY once
                assert len(rows) == 6
                assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6]
                assert all(r[1] == 1 for r in rows)

                cursor = await db.execute("SELECT COUNT(*) FROM readings WHERE station_id = 'STN-RECONNECT'")
                total_count = (await cursor.fetchone())[0]
                assert total_count == 6
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()
            await app.write_queue.put(app._WRITER_SENTINEL)
            await writer_task
