"""Tests for Flask Web Dashboard (web/web.py).

Tests cover:
- Dashboard root route (GET /)
- Stations API (GET /api/stations) with populated and empty databases
- Stats API (GET /api/stats) parameter validation, time-window aggregations, and limit aggregations
- Readings API (GET /api/readings) parameter validation
- Regression test for B2 ("Last N Values" returns newest N records in chronological order)
- Regression test for B3 (UTC cutoff time-windowing without host timezone skew)
- Dynamic schema detection (detect_wind_column for 'windspeed' vs 'wind_speed')
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

    def test_stats_time_range_aggregation(self, client, test_db_path: Path):
        """Stats with range=6h should aggregate only records within 6 hours via SQLite."""
        response = client.get("/api/stats?station_id=STATION-001&metric=temperature&range=6h")
        assert response.status_code == 200
        data = response.get_json()
        assert data["latest"] is not None
        assert data["avg"] is not None
        assert data["min"] <= data["avg"] <= data["max"]

        # Ground truth check against database
        conn = sqlite3.connect(str(test_db_path))
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        rows = conn.execute(
            """
            SELECT temperature, timestamp FROM readings
            WHERE station_id = 'STATION-001' AND timestamp >= ? AND temperature IS NOT NULL
            ORDER BY timestamp DESC
            """,
            (cutoff,),
        ).fetchall()
        conn.close()

        expected_temps = [r["temperature"] for r in rows]
        assert len(expected_temps) > 0
        assert data["latest"] == round(expected_temps[0], 2)
        assert data["latest_t"] == rows[0]["timestamp"]
        assert data["avg"] == round(sum(expected_temps) / len(expected_temps), 2)
        assert data["min"] == round(min(expected_temps), 2)
        assert data["max"] == round(max(expected_temps), 2)

    def test_stats_handles_db_error(self, client):
        """DB query failure during stats calculation should return null metrics gracefully."""
        with patch("web.get_db", side_effect=sqlite3.OperationalError("db error")):
            response = client.get("/api/stats?station_id=STATION-001&metric=temperature&range=24h")
            assert response.status_code == 200
            assert response.get_json()["latest"] is None

    def test_stats_wind_speed_metric(self, client):
        """Stats should accept 'wind_speed' as a valid metric and query normalized wind column."""
        response = client.get("/api/stats?station_id=STATION-001&metric=wind_speed&limit=10")
        assert response.status_code == 200
        data = response.get_json()
        assert data["latest"] is not None
        assert data["avg"] is not None
        assert data["min"] <= data["avg"] <= data["max"]


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

    def test_readings_wind_speed_metric(self, client):
        """Readings should accept 'wind_speed' as a valid metric and return points."""
        response = client.get("/api/readings?station_id=STATION-001&metric=wind_speed&limit=10")
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["points"]) == 10
        assert all("t" in p and "v" in p for p in data["points"])


# ============================================================================
# 5. Schema Detection & Utility Tests
# ============================================================================


class TestSchemaAndUtilities:
    """Tests for detect_wind_column() dynamic schema detection."""

    def test_detect_wind_column_standard(self, client, test_db_path: Path):
        """Default table with 'windspeed' column should be detected as 'windspeed'."""
        web._wind_column_cache = None
        assert web.detect_wind_column() == "windspeed"
        assert web.normalize_metric("windspeed") == "windspeed"
        assert web.normalize_metric("wind_speed") == "windspeed"

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
        assert web.normalize_metric("wind_speed") == "wind_speed"

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


# ============================================================================
# 6. Database Compound Index Initialization Tests (Issue #6)
# ============================================================================


class TestDatabaseIndexInitialization:
    """Tests for standalone database index initialization (Issue #6)."""

    def test_init_db_indexes_creates_indexes_on_existing_table(self, tmp_path: Path, monkeypatch):
        """init_db_indexes() should create compound indexes if readings table exists."""
        db_file = tmp_path / "unindexed.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            """
            CREATE TABLE readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT,
                timestamp TEXT,
                temperature REAL,
                humidity REAL,
                windspeed REAL
            )
            """
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(web, "DB_PATH", db_file)
        monkeypatch.setattr(web, "_indexes_checked_db", None)

        assert web.init_db_indexes() is True

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "idx_readings_station_ts" in indexes
        assert "idx_readings_station_id_desc" in indexes

    def test_init_db_indexes_is_idempotent(self, tmp_path: Path, monkeypatch):
        """Calling init_db_indexes() multiple times should succeed without error."""
        db_file = tmp_path / "idempotent.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, station_id TEXT, timestamp TEXT)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(web, "DB_PATH", db_file)
        monkeypatch.setattr(web, "_indexes_checked_db", None)

        assert web.init_db_indexes() is True
        assert web.init_db_indexes() is True

    def test_init_db_indexes_with_explicit_db_path(self, tmp_path: Path):
        """init_db_indexes(db_path) should create indexes on explicit path."""
        db_file = tmp_path / "explicit.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, station_id TEXT, timestamp TEXT)")
        conn.commit()
        conn.close()

        assert web.init_db_indexes(db_file) is True

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "idx_readings_station_ts" in indexes
        assert "idx_readings_station_id_desc" in indexes

    def test_init_db_indexes_no_db_file(self, tmp_path: Path):
        """Non-existent DB file should return False without raising an error."""
        missing_db = tmp_path / "nonexistent.db"
        assert web.init_db_indexes(missing_db) is False
        assert not missing_db.exists()

    def test_init_db_indexes_no_readings_table(self, tmp_path: Path):
        """DB without readings table should return False without creating indexes."""
        db_file = tmp_path / "other.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        assert web.init_db_indexes(db_file) is False

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = cursor.fetchall()
        conn.close()
        assert len(indexes) == 0

    def test_init_db_indexes_handles_db_exception(self, monkeypatch):
        """Database exception during initialization should log warning and return False."""
        with patch("web.get_db", side_effect=sqlite3.OperationalError("disk error")):
            with patch.object(Path, "exists", return_value=True):
                monkeypatch.setattr(web, "_indexes_checked_db", None)
                assert web.init_db_indexes() is False

    def test_before_request_initializes_indexes_automatically(self, tmp_path: Path, monkeypatch):
        """Incoming HTTP requests trigger index initialization automatically."""
        db_file = tmp_path / "auto_init.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, station_id TEXT, timestamp TEXT)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(web, "DB_PATH", db_file)
        monkeypatch.setattr(web, "_indexes_checked_db", None)

        with web.app.test_client() as client:
            res = client.get("/api/stations")
            assert res.status_code == 200

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "idx_readings_station_ts" in indexes
        assert "idx_readings_station_id_desc" in indexes

    def test_independent_web_query_uses_index_query_plan(self, tmp_path: Path):
        """Verify query plan uses index after web initialization without server init."""
        db_file = tmp_path / "query_plan.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
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
        conn.commit()
        conn.close()

        assert web.init_db_indexes(db_file) is True

        conn = sqlite3.connect(str(db_file))
        cursor = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT timestamp, temperature
            FROM readings
            WHERE station_id = 'ST-001' AND temperature IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 10
            """
        )
        plan = " ".join(row[3] for row in cursor.fetchall())
        conn.close()

        assert "USING INDEX idx_readings_station_ts" in plan
        assert "SCAN" not in plan

