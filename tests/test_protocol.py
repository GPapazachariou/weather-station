"""Unit tests for server/protocol.py.

Tests cover:
- Timestamp format validation (ISO-8601, 'Z' normalization, timezone offsets, error cases)
- Finite number validation (finite floats, ints, NaN, Infinity, bool rejection)
- Station ID validation (types, length limits, character set whitelist, injection attempts)
- Measurement boundary ranges (temperature, humidity, windspeed)
- Batch-level validation (structure, size limits, required keys, all-or-nothing transactional guarantees)
"""

import math

import pytest
from protocol import (
    HUMIDITY_MAX,
    HUMIDITY_MIN,
    MAX_BATCH_SIZE,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    WINDSPEED_MAX,
    WINDSPEED_MIN,
    validate_batch,
    validate_finite_number,
    validate_timestamp,
)


def make_valid_reading(**kwargs):
    """Helper to create a valid reading dict with optional overrides."""
    base = {
        "station_id": "STATION-001",
        "timestamp": "2026-10-02T01:30:00Z",
        "temperature": 21.5,
        "humidity": 55.0,
        "windspeed": 12.0,
    }
    base.update(kwargs)
    return base


# ============================================================================
# 1. Timestamp Validation Tests
# ============================================================================


class TestTimestampValidation:
    """Tests for validate_timestamp()."""

    @pytest.mark.parametrize(
        "valid_ts",
        [
            "2026-10-02T01:30:00Z",
            "2026-10-02T01:30:00+00:00",
            "2026-10-02T01:30:00-05:00",
            "2026-10-02T04:30:00+03:00",
            "2026-10-02T01:30:00.123456Z",
            "2026-10-02T01:30:00.654321+02:00",
            "2026-12-31T23:59:59Z",
        ],
    )
    def test_valid_iso8601_timestamps(self, valid_ts):
        """Valid ISO-8601 timestamps should pass without exception."""
        assert validate_timestamp(valid_ts) is None

    @pytest.mark.parametrize(
        "invalid_ts",
        [
            "",
            "not-a-timestamp",
            "12:30:00",  # Time only
            "2026-02-31T12:00:00Z",  # Non-existent calendar date
            "2026/10/02 01:30:00",  # Non-standard separator
            "2026-10-02T25:00:00Z",  # Invalid hour
            "2026-13-01T00:00:00Z",  # Invalid month
        ],
    )
    def test_invalid_timestamp_strings(self, invalid_ts):
        """Malformed or out-of-range timestamp strings should raise ValueError."""
        with pytest.raises(ValueError, match="invalid_timestamp"):
            validate_timestamp(invalid_ts)

    @pytest.mark.parametrize("non_string", [None, 1727830000, 1727830000.5, ["2026-10-02T00:00:00Z"], {}])
    def test_timestamp_non_string_types(self, non_string):
        """Non-string inputs should raise ValueError indicating string requirement."""
        with pytest.raises(ValueError, match="timestamp must be a string"):
            validate_timestamp(non_string)


# ============================================================================
# 2. Finite Number Validation & Bool Rejection Tests
# ============================================================================


class TestFiniteNumberValidation:
    """Tests for validate_finite_number()."""

    @pytest.mark.parametrize("valid_num", [0, 0.0, -10, 40.5, 100, -0.0, 1e-5])
    def test_valid_numbers(self, valid_num):
        """Standard ints and floats should pass validation."""
        assert validate_finite_number(valid_num, "metric") is None

    @pytest.mark.parametrize(
        "non_finite",
        [float("nan"), float("inf"), float("-inf"), math.nan, math.inf, -math.inf],
    )
    def test_non_finite_rejection(self, non_finite):
        """NaN, Infinity, and -Infinity must be rejected."""
        with pytest.raises(ValueError, match="non_finite_number: metric="):
            validate_finite_number(non_finite, "metric")

    @pytest.mark.parametrize("bool_val", [True, False])
    def test_bool_rejection(self, bool_val):
        """Booleans must be explicitly rejected even though bool is a subclass of int."""
        with pytest.raises(ValueError, match="metric must be a number"):
            validate_finite_number(bool_val, "metric")

    @pytest.mark.parametrize("invalid_type", ["25.0", None, [], {}, (1, 2)])
    def test_invalid_types(self, invalid_type):
        """Non-numeric types must be rejected."""
        with pytest.raises(ValueError, match="metric must be a number"):
            validate_finite_number(invalid_type, "metric")


# ============================================================================
# 3. Measurement Range Boundaries Tests
# ============================================================================


class TestMeasurementBoundaries:
    """Tests for temperature, humidity, and windspeed range boundaries."""

    @pytest.mark.parametrize(
        "temp",
        [
            TEMPERATURE_MIN,  # -10
            TEMPERATURE_MIN + 0.001,
            0.0,
            25.5,
            TEMPERATURE_MAX - 0.001,
            TEMPERATURE_MAX,  # 40
        ],
    )
    def test_temperature_valid_boundaries(self, temp):
        reading = make_valid_reading(temperature=temp)
        assert validate_batch([reading]) is None

    @pytest.mark.parametrize(
        "temp",
        [
            TEMPERATURE_MIN - 0.001,
            TEMPERATURE_MIN - 1.0,
            -100.0,
            TEMPERATURE_MAX + 0.001,
            TEMPERATURE_MAX + 10.0,
            150.0,
        ],
    )
    def test_temperature_out_of_range(self, temp):
        reading = make_valid_reading(temperature=temp)
        with pytest.raises(ValueError, match=f"invalid temperature at index 0 \\(expected {TEMPERATURE_MIN}..{TEMPERATURE_MAX}\\)"):
            validate_batch([reading])

    @pytest.mark.parametrize(
        "humidity",
        [
            HUMIDITY_MIN,  # 0
            HUMIDITY_MIN + 0.01,
            50.0,
            HUMIDITY_MAX - 0.01,
            HUMIDITY_MAX,  # 100
        ],
    )
    def test_humidity_valid_boundaries(self, humidity):
        reading = make_valid_reading(humidity=humidity)
        assert validate_batch([reading]) is None

    @pytest.mark.parametrize(
        "humidity",
        [
            HUMIDITY_MIN - 0.001,
            -10.0,
            HUMIDITY_MAX + 0.001,
            105.0,
            200.0,
        ],
    )
    def test_humidity_out_of_range(self, humidity):
        reading = make_valid_reading(humidity=humidity)
        with pytest.raises(ValueError, match=f"invalid humidity at index 0 \\(expected {HUMIDITY_MIN}..{HUMIDITY_MAX}\\)"):
            validate_batch([reading])

    @pytest.mark.parametrize(
        "windspeed",
        [
            WINDSPEED_MIN,  # 0
            WINDSPEED_MIN + 0.01,
            25.0,
            WINDSPEED_MAX - 0.01,
            WINDSPEED_MAX,  # 50
        ],
    )
    def test_windspeed_valid_boundaries(self, windspeed):
        reading = make_valid_reading(windspeed=windspeed)
        assert validate_batch([reading]) is None

    @pytest.mark.parametrize(
        "windspeed",
        [
            WINDSPEED_MIN - 0.001,
            -5.0,
            WINDSPEED_MAX + 0.001,
            55.0,
            100.0,
        ],
    )
    def test_windspeed_out_of_range(self, windspeed):
        reading = make_valid_reading(windspeed=windspeed)
        with pytest.raises(ValueError, match=f"invalid windspeed at index 0 \\(expected {WINDSPEED_MIN}..{WINDSPEED_MAX}\\)"):
            validate_batch([reading])


# ============================================================================
# 4. Station ID Sanitization & Format Tests
# ============================================================================


class TestStationIdValidation:
    """Tests for station_id type, format, length, and injection prevention."""

    @pytest.mark.parametrize(
        "valid_id",
        [
            "STATION-001",
            "stn_alpha_99",
            "A",
            "a" * 64,  # Exact 64 character boundary
            "Weather-Node_123-ABC",
        ],
    )
    def test_valid_station_ids(self, valid_id):
        reading = make_valid_reading(station_id=valid_id)
        assert validate_batch([reading]) is None

    def test_empty_station_id(self):
        reading = make_valid_reading(station_id="")
        with pytest.raises(ValueError, match="empty station_id at index 0"):
            validate_batch([reading])

    @pytest.mark.parametrize("non_string", [None, 101, ["STATION-1"], {}])
    def test_station_id_non_string(self, non_string):
        reading = make_valid_reading(station_id=non_string)
        with pytest.raises(ValueError, match="invalid station_id at index 0 \\(expected string\\)"):
            validate_batch([reading])

    def test_station_id_exceeds_max_length(self):
        oversized = "A" * 65  # 65 chars exceeds 64 limit
        reading = make_valid_reading(station_id=oversized)
        with pytest.raises(ValueError, match="invalid station_id format at index 0"):
            validate_batch([reading])

    @pytest.mark.parametrize(
        "malicious_id",
        [
            "<script>alert(1)</script>",
            "STATION 001",  # Space not allowed
            "STATION#001",
            "STATION/001",
            "STATION;DROP TABLE readings;",
            "STN\n001",
            "STN\t001",
            "stn.001",
            "STN-αβγ",  # Non-ASCII Unicode characters
            "STATION_©",
        ],
    )
    def test_station_id_invalid_characters_rejected(self, malicious_id):
        """Characters outside [A-Za-z0-9_-] must be rejected to prevent injection."""
        reading = make_valid_reading(station_id=malicious_id)
        with pytest.raises(ValueError, match="invalid station_id format at index 0"):
            validate_batch([reading])


# ============================================================================
# 5. Batch Structure & Transactional All-or-Nothing Tests
# ============================================================================


class TestBatchValidation:
    """Tests for batch structure, required keys, sizing, and all-or-nothing semantics."""

    @pytest.mark.parametrize("invalid_batch", [None, "batch", 123, {"readings": []}])
    def test_batch_not_a_list(self, invalid_batch):
        with pytest.raises(ValueError, match="batch is not a list"):
            validate_batch(invalid_batch)

    def test_empty_batch(self):
        """Empty list should pass validation (0 readings to insert)."""
        assert validate_batch([]) is None

    def test_max_batch_size_valid(self):
        """A batch of exactly MAX_BATCH_SIZE (50) readings is valid."""
        batch = [make_valid_reading(temperature=20.0 + (i * 0.1)) for i in range(MAX_BATCH_SIZE)]
        assert len(batch) == 50
        assert validate_batch(batch) is None

    def test_batch_size_exceeds_limit(self):
        """A batch of MAX_BATCH_SIZE + 1 (51) readings must be rejected."""
        batch = [make_valid_reading() for _ in range(MAX_BATCH_SIZE + 1)]
        assert len(batch) == 51
        with pytest.raises(ValueError, match="batch size exceeds limit"):
            validate_batch(batch)

    @pytest.mark.parametrize("non_dict", ["not-a-dict", 123, None, [1, 2, 3]])
    def test_item_is_not_a_dict(self, non_dict):
        batch = [make_valid_reading(), non_dict]
        with pytest.raises(ValueError, match="item at index 1 is not a dict"):
            validate_batch(batch)

    @pytest.mark.parametrize(
        "missing_key",
        ["station_id", "timestamp", "temperature", "humidity", "windspeed"],
    )
    def test_missing_required_fields(self, missing_key):
        reading = make_valid_reading()
        del reading[missing_key]
        with pytest.raises(ValueError, match=f"missing {missing_key} at index 0"):
            validate_batch([reading])

    @pytest.mark.parametrize(
        "metric_field,bad_val",
        [
            ("temperature", float("nan")),
            ("temperature", True),
            ("temperature", "cold"),
            ("humidity", float("inf")),
            ("humidity", False),
            ("humidity", None),
            ("windspeed", float("-inf")),
            ("windspeed", True),
            ("windspeed", [10]),
        ],
    )
    def test_batch_non_finite_metric_error_propagates(self, metric_field, bad_val):
        """Non-finite or invalid typed metric in a batch raises formatted error with index."""
        reading = make_valid_reading(**{metric_field: bad_val})
        with pytest.raises(ValueError) as excinfo:
            validate_batch([reading])
        assert f"invalid {metric_field} at index 0" in str(excinfo.value)

    def test_all_or_nothing_transactional_guarantee(self):
        """If item 49 of 50 has an error, the batch fails at index 49."""
        batch = [make_valid_reading(temperature=20.0) for _ in range(MAX_BATCH_SIZE)]
        # Inject an invalid temperature only into the very last item
        batch[49]["temperature"] = 999.0

        with pytest.raises(ValueError) as excinfo:
            validate_batch(batch)

        assert "index 49" in str(excinfo.value)
        assert f"expected {TEMPERATURE_MIN}..{TEMPERATURE_MAX}" in str(excinfo.value)

    def test_timestamp_failure_propagates_with_index(self):
        """Invalid timestamp in batch properly reports the item index."""
        batch = [
            make_valid_reading(),
            make_valid_reading(timestamp="invalid-date"),
        ]
        with pytest.raises(ValueError) as excinfo:
            validate_batch(batch)
        assert "invalid_timestamp at index 1" in str(excinfo.value)
