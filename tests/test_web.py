"""Tests for Flask Web Dashboard (web/web.py).

Tests cover:
- Dashboard root route (GET /)
- Stations API (GET /api/stations) with populated and empty databases
- Stats API (GET /api/stats) parameter validation, time-window aggregations, and limit aggregations
- Readings API (GET /api/readings) parameter validation
- Regression test for B2 ("Last N Values" returns newest N records in chronological order)
- Regression test for B3 (UTC cutoff time-windowing without host timezone skew)
- Dynamic schema detection (detect_wind_column for 'windspeed' vs 'wind_speed')
- parse_timestamp utility behavior
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import web


@pytest.fixture
def test_db_path(tmp_path: Path):
    """Fixture to set up an isolated SQLite database with test schema and data."""
    db_file = tmp_path / "test_weather.db"
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Create tables and indexes
    cursor.execute(
        """
        CREATE TABLE readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            temperature REAL NOT NULL,
            humidity REAL NOT NULL,
            windspeed REAL NOT NULL
        )
        """
    )
    cursor.execute("CREATE INDEX idx_readings_station_ts ON readings(station_id, timestamp)")
    cursor.execute("CREATE INDEX idx_readings_station_id_desc ON readings(station_id, id DESC)")

    # Seed 60 records for STATION-001 spaced over 30 hours
    # from now - 30h to now
    now = datetime.now(timezone.utc)
    records = []
    total_records = 60
    interval_seconds = (30 * 3600) / total_records

    for i in range(total_records):
        ts = now - timedelta(seconds=(total_records - 1 - i) * interval_seconds)
        # Deterministic temperature between 20.0 and 25.9
        temp = 20.0 + (i % 60) * 0.1
        hum = 40.0 + (i % 20) * 1.0
        wind = 5.0 + (i % 10) * 0.5
        records.append(("STATION-001", ts.isoformat(), round(temp, 2), round(hum, 2), round(wind, 2)))

    # Seed 5 records for STATION-002
    for i in range(5):
        ts = now - timedelta(minutes=(5 - i) * 10)
        records.append(("STATION-002", ts.isoformat(), 15.0 + i, 70.0 + i, 3.0 + i))

    cursor.executemany(
        """
        INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed)
        VALUES (?, ?, ?, ?, ?)
        """,
        records,
    )
    conn.commit()
    conn.close()

    return db_file


@pytest.fixture
def client(test_db_path: Path):
    """Flask test client fixture configured with temporary test database."""
    original_db = web.DB_PATH
    web.DB_PATH = test_db_path
    web._wind_column_cache = None

    web.app.config["TESTING"] = True
    with web.app.test_client() as test_client:
        yield test_client

    web.DB_PATH = original_db
    web._wind_column_cache = None


# ============================================================================
# 1. Root & Template Tests
# ============================================================================


def test_index_page(client):
    """GET / should serve index.html with HTTP 200."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Weather Station Dashboard" in response.data


# ============================================================================
# 2. Stations API Tests
# ============================================================================


class TestStationsApi:
    """Tests for /api/stations."""

    def test_get_stations_success(self, client):
        """Should return unique sorted list of station IDs."""
        response = client.get("/api/stations")
        assert response.status_code == 200
        data = response.get_json()
        assert data["stations"] == ["STATION-001", "STATION-002"]

    def test_get_stations_empty_db(self, client, tmp_path: Path):
        """Should return empty list if no readings exist."""
        empty_db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(empty_db))
        conn.execute("CREATE TABLE readings (station_id TEXT)")
        conn.commit()
        conn.close()

        web.DB_PATH = empty_db
        response = client.get("/api/stations")
        assert response.status_code == 200
        data = response.get_json()
        assert data["stations"] == []

    def test_get_stations_handles_db_error(self, client):
        """Should return empty stations list on database error."""
        with patch("web.get_db", side_effect=sqlite3.OperationalError("db error")):
            response = client.get("/api/stations")
            assert response.status_code == 200
            assert response.get_json() == {"stations": []}


# ============================================================================
# 3. Stats API Tests
# ============================================================================


class TestStatsApi:
    """Tests for /api/stats."""

    @pytest.mark.parametrize(
        "query,expected_err",
        [
            ("", "Invalid parameters"),
            ("station_id=STATION-001", "Invalid parameters"),  # missing metric
            ("metric=temperature", "Invalid parameters"),  # missing station_id
            ("station_id=STATION-001&metric=unsupported_metric", "Invalid parameters"),
            ("station_id=STATION-001&metric=temperature&range=100h", "Invalid range or limit"),
            ("station_id=STATION-001&metric=temperature&range=bad&limit=25", "Invalid range or limit"),
        ],
    )
    def test_stats_validation_errors(self, client, query, expected_err):
        """Invalid query parameters should return 400 with descriptive error."""
        response = client.get(f"/api/stats?{query}")
        assert response.status_code == 400
        data = response.get_json()
        assert expected_err in data["error"]
        assert data["latest"] is None

    def test_stats_nonexistent_station(self, client):
        """Nonexistent station should return HTTP 200 with None values."""
        response = client.get("/api/stats?station_id=NONEXISTENT&metric=temperature&range=24h")
        assert response.status_code == 200
        data = response.get_json()
        assert data == {
            "latest": None,
            "latest_t": None,
            "avg": None,
            "min": None,
            "max": None,
        }

    def test_stats_limit_aggregation(self, client, test_db_path: Path):
        """Stats with limit=10 should compute aggregates over exactly the newest 10 records."""
        response = client.get("/api/stats?station_id=STATION-001&metric=temperature&limit=10")
        assert response.status_code == 200
        data = response.get_json()

        # Query SQLite directly for ground truth of newest 10 records
        conn = sqlite3.connect(str(test_db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT temperature, timestamp FROM readings
            WHERE station_id = 'STATION-001'
            ORDER BY timestamp DESC LIMIT 10
            """
        ).fetchall()
        conn.close()

        expected_temps = [r["temperature"] for r in rows]
        assert data["latest"] == round(expected_temps[0], 2)
        assert data["latest_t"] == rows[0]["timestamp"]
        assert data["avg"] == round(sum(expected_temps) / 10, 2)
        assert data["min"] == round(min(expected_temps), 2)
        assert data["max"] == round(max(expected_temps), 2)

    def test_stats_time_range_aggregation(self, client):
        """Stats with range=6h should aggregate only records within 6 hours."""
        response = client.get("/api/stats?station_id=STATION-001&metric=temperature&range=6h")
        assert response.status_code == 200
        data = response.get_json()
        assert data["latest"] is not None
        assert data["avg"] is not None
        assert data["min"] <= data["avg"] <= data["max"]

    def test_stats_handles_db_error(self, client):
        """DB query failure during stats calculation should return null metrics gracefully."""
        with patch("web.get_db", side_effect=sqlite3.OperationalError("db error")):
            response = client.get("/api/stats?station_id=STATION-001&metric=temperature&range=24h")
            assert response.status_code == 200
            assert response.get_json()["latest"] is None


# ============================================================================
# 4. Readings API & Regression Tests (B2 & B3)
# ============================================================================


class TestReadingsApi:
    """Tests for /api/readings and regression safeguards."""

    @pytest.mark.parametrize(
        "query,expected_err",
        [
            ("", "Invalid parameters"),
            ("station_id=STATION-001", "Invalid parameters"),
            ("metric=humidity", "Invalid parameters"),
            ("station_id=STATION-001&metric=invalid_metric", "Invalid parameters"),
            ("station_id=STATION-001&metric=temperature&range=99h", "Invalid range or limit"),
            ("station_id=STATION-001&metric=temperature&range=bad&limit=42", "Invalid range or limit"),
        ],
    )
    def test_readings_validation_errors(self, client, query, expected_err):
        """Invalid query parameters should return 400 with empty points."""
        response = client.get(f"/api/readings?{query}")
        assert response.status_code == 400
        data = response.get_json()
        assert expected_err in data["error"]
        assert data["points"] == []

    def test_readings_nonexistent_station(self, client):
        """Nonexistent station returns empty points list."""
        response = client.get("/api/readings?station_id=NONEXISTENT&metric=temperature&limit=10")
        assert response.status_code == 200
        assert response.get_json() == {"points": []}

    def test_regression_b2_limit_chronological_ordering(self, client, test_db_path: Path):
        """
        Regression Test for Finding B2:
        'Last N Values' must select the NEWEST N records from SQLite,
        but return them in strictly CHRONOLOGICAL order (oldest to newest)
        so Chart.js displays the time axis correctly from left to right.
        """
        limit = 10
        response = client.get(f"/api/readings?station_id=STATION-001&metric=temperature&limit={limit}")
        assert response.status_code == 200
        points = response.get_json()["points"]
        assert len(points) == limit

        # Verify points are in strictly ascending chronological order: t_0 < t_1 < ... < t_9
        timestamps = [p["t"] for p in points]
        assert timestamps == sorted(timestamps)

        # Ground truth check: verify these 10 points match the newest 10 records in the database
        conn = sqlite3.connect(str(test_db_path))
        conn.row_factory = sqlite3.Row
        db_newest_rows = conn.execute(
            """
            SELECT timestamp, temperature FROM readings
            WHERE station_id = 'STATION-001'
            ORDER BY timestamp DESC LIMIT 10
            """
        ).fetchall()
        conn.close()

        # The newest record in the DB must be the LAST point in the returned series
        assert points[-1]["t"] == db_newest_rows[0]["timestamp"]
        assert points[-1]["v"] == db_newest_rows[0]["temperature"]

        # The 10th newest record in the DB must be the FIRST point in the returned series
        assert points[0]["t"] == db_newest_rows[-1]["timestamp"]
        assert points[0]["v"] == db_newest_rows[-1]["temperature"]

    def test_regression_b3_utc_cutoff_no_timezone_skew(self, client):
        """
        Regression Test for Finding B3:
        Time-range filtering must use UTC cutoff to ensure consistent windowing
        regardless of host machine local timezone offset.
        """
        response = client.get("/api/readings?station_id=STATION-001&metric=temperature&range=6h")
        assert response.status_code == 200
        points = response.get_json()["points"]

        assert len(points) > 0

        # All returned points must have timestamps within the last 6 hours
        cutoff = datetime.now(timezone.utc) - timedelta(hours=6, seconds=30)  # small delta buffer
        for p in points:
            t = datetime.fromisoformat(p["t"])
            assert t >= cutoff, f"Point timestamp {p['t']} is older than 6h cutoff {cutoff.isoformat()}"

    def test_readings_handles_db_error(self, client):
        """DB query failure during readings fetch should return empty points gracefully."""
        with patch("web.get_db", side_effect=sqlite3.OperationalError("db error")):
            response = client.get("/api/readings?station_id=STATION-001&metric=temperature&range=6h")
            assert response.status_code == 200
            assert response.get_json() == {"points": []}


# ============================================================================
# 5. Schema Detection & Utility Tests
# ============================================================================


class TestSchemaAndUtilities:
    """Tests for detect_wind_column() and parse_timestamp()."""

    def test_detect_wind_column_standard(self, client, test_db_path: Path):
        """Default table with 'windspeed' column should be detected as 'windspeed'."""
        web._wind_column_cache = None
        assert web.detect_wind_column() == "windspeed"
        assert web.normalize_metric("windspeed") == "windspeed"

    def test_detect_wind_column_legacy_underscore(self, tmp_path: Path):
        """Table with 'wind_speed' column should be detected as 'wind_speed'."""
        legacy_db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(legacy_db))
        conn.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, wind_speed REAL)")
        conn.commit()
        conn.close()

        web.DB_PATH = legacy_db
        web._wind_column_cache = None
        assert web.detect_wind_column() == "wind_speed"
        assert web.normalize_metric("windspeed") == "wind_speed"

    def test_detect_wind_column_fallback_on_missing_column(self, tmp_path: Path):
        """Table without any wind column should fall back to 'windspeed'."""
        other_db = tmp_path / "other.db"
        conn = sqlite3.connect(str(other_db))
        conn.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, temperature REAL)")
        conn.commit()
        conn.close()

        web.DB_PATH = other_db
        web._wind_column_cache = None
        assert web.detect_wind_column() == "windspeed"

    def test_detect_wind_column_fallback_on_db_exception(self):
        """Database exception during detection should log warning and return fallback."""
        with patch("web.get_db", side_effect=sqlite3.OperationalError("error")):
            web._wind_column_cache = None
            assert web.detect_wind_column() == "windspeed"

    def test_parse_timestamp_valid_iso_z(self):
        """ISO timestamp ending in Z should parse to timezone-aware UTC datetime."""
        dt = web.parse_timestamp("2026-10-02T01:30:00Z")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026 and dt.month == 10 and dt.day == 2

    def test_parse_timestamp_naive_iso(self):
        """ISO timestamp without timezone offset should be set to UTC."""
        dt = web.parse_timestamp("2026-10-02T01:30:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_parse_timestamp_valid_offset(self):
        """ISO timestamp with explicit offset should convert to timezone-aware UTC datetime."""
        dt = web.parse_timestamp("2026-10-02T04:30:00+03:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 1 and dt.minute == 30

    def test_parse_timestamp_epoch_float(self):
        """Epoch timestamp as string should parse to UTC datetime."""
        dt = web.parse_timestamp("1727832600")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    @pytest.mark.parametrize("invalid_ts", [None, "", "not-a-timestamp", "2026/99/99"])
    def test_parse_timestamp_invalid_returns_none(self, invalid_ts):
        """Invalid timestamp inputs should return None."""
        assert web.parse_timestamp(invalid_ts) is None
