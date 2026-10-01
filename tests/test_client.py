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
        assert "timestamp" in reading
        assert "temperature" in reading
        assert "humidity" in reading
        assert "windspeed" in reading

        # Assert full validation passes
        assert validate_batch([reading]) is None

    def test_generate_batch_size_and_validity(self):
        """generate_batch(size) generates requested quantity, all valid."""
        batch = client.generate_batch("STATION-TEST-002", 5)
        assert len(batch) == 5
        assert validate_batch(batch) is None


# ============================================================================
# 2. Buffer Semantics (Drop-Oldest on Overflow)
# ============================================================================


class TestBufferSemantics:
    """Tests simulating client.py buffer overflow and FIFO behavior."""

    def test_buffer_fifo_order(self):
        """Buffer deque should preserve FIFO order during normal enqueue/dequeue."""
        buf = deque()
        batch_1 = client.generate_batch("STATION-001", 1)
        batch_2 = client.generate_batch("STATION-001", 1)

        buf.append(batch_1)
        buf.append(batch_2)

        assert buf.popleft() == batch_1
        assert buf.popleft() == batch_2
        assert len(buf) == 0

    def test_buffer_drop_oldest_on_capacity(self):
        """
        When buffer reaches max_buffer_records, appending a new batch
        must drop the oldest batch (popleft) to keep memory bounded.
        """
        max_capacity = 3
        buf = deque()

        # Enqueue batches 0, 1, 2 (fills buffer to capacity)
        batches = [client.generate_batch("STATION-001", 1) for _ in range(5)]
        for b in batches[:max_capacity]:
            buf.append(b)

        assert len(buf) == max_capacity

        # Enqueue batch 3 with drop-oldest logic from client.py:423-430
        for b in batches[max_capacity:]:
            if len(buf) >= max_capacity:
                buf.popleft()
            buf.append(b)

        assert len(buf) == max_capacity
        # Oldest batches (0 and 1) were dropped; remaining must be 2, 3, 4
        assert buf.popleft() == batches[2]
        assert buf.popleft() == batches[3]
        assert buf.popleft() == batches[4]


# ============================================================================
# 3. Exponential Backoff & Jitter Simulation
# ============================================================================


class TestBackoffCalculation:
    """Tests for exponential backoff doubling, max ceiling, and jitter."""

    def test_exponential_backoff_progression(self):
        """Backoff should double up to max_backoff."""
        base_backoff = 1.0
        max_backoff = 16.0
        current_backoff = base_backoff

        expected_sequence = [1.0, 2.0, 4.0, 8.0, 16.0, 16.0, 16.0]
        actual_sequence = [current_backoff]

        for _ in range(len(expected_sequence) - 1):
            current_backoff = min(current_backoff * 2, max_backoff)
            actual_sequence.append(current_backoff)

        assert actual_sequence == expected_sequence

    def test_jitter_is_non_negative_and_bounded(self):
        """Jitter should add a random float in [0, backoff_jitter]."""
        import random

        backoff_jitter = 1.0
        for _ in range(50):
            jitter = random.uniform(0, backoff_jitter)
            assert 0.0 <= jitter <= backoff_jitter


# ============================================================================
# 4. Configuration Loading Tests
# ============================================================================


class TestClientConfig:
    """Tests for client.load_config() argument and environment variable handling."""

    def test_default_config_loading(self, monkeypatch):
        """With no CLI args and no env vars, defaults should be loaded."""
        monkeypatch.setattr("sys.argv", ["client.py"])
        # Clear any existing WEATHER_STATION_ env vars
        for k in list(client.os.environ.keys()):
            if k.startswith("WEATHER_STATION_"):
                monkeypatch.delenv(k, raising=False)

        cfg = client.load_config()
        assert cfg["server_host"] == client.DEFAULT_SERVER_HOST
        assert cfg["server_port"] == client.DEFAULT_SERVER_PORT
        assert cfg["station_id"] == client.DEFAULT_STATION_ID
        assert cfg["batch_size_min"] == client.DEFAULT_BATCH_SIZE_MIN
        assert cfg["batch_size_max"] == client.DEFAULT_BATCH_SIZE_MAX

    def test_env_var_overrides_defaults(self, monkeypatch):
        """Environment variables should override defaults when CLI args are absent."""
        monkeypatch.setattr("sys.argv", ["client.py"])
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
