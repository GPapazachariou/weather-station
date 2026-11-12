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

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

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
    assert b"live-indicator" in response.data
    assert b"last-updated" in response.data
    assert response.data.count(b'id="stats-container"') == 1
    assert response.data.count(b'id="stat-latest"') == 1


class TestAccessibilityAndUX:
    """Focused regression tests for dashboard UX, accessibility landmarks, and controls."""

    def test_skip_link_present(self, client):
        """Skip to main content link should be present and point to #main-content."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert '<a href="#main-content" class="skip-link">Skip to main content</a>' in html
        assert 'id="main-content"' in html

    def test_accessibility_landmarks(self, client):
        """Semantic landmarks and aria roles should be present for screen readers."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert 'role="banner"' in html
        assert '<main id="main-content"' in html
        assert 'role="region"' in html
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html

    def test_connection_and_freshness_controls(self, client):
        """Live stream indicator, reconnect button, freshness badge, and refresh action should exist."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert 'id="live-indicator"' in html
        assert 'id="btn-reconnect"' in html
        assert 'id="freshness-badge"' in html
        assert 'id="freshness-text"' in html
        assert 'id="btn-refresh-now"' in html

    def test_display_mode_switcher(self, client):
        """Segmented control allowing user to switch between charts and accessible table."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert 'id="btn-view-charts"' in html
        assert 'id="btn-view-table"' in html
        assert 'class="segmented-control"' in html

    def test_empty_and_loading_states(self, client):
        """Global empty state, station empty state with recovery actions, and loading chip."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert 'id="empty-state"' in html
        assert 'id="station-empty-state"' in html
        assert 'id="meta-loading"' in html
        assert 'onclick="setRangeAndRefresh(\'all\')"' in html
        assert 'onclick="setRangeAndRefresh(\'30d\')"' in html

    def test_accessible_table_alternative(self, client):
        """Accessible data table alternative should have scrollable region, caption, and headers."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert 'id="table-container"' in html
        assert 'id="telemetry-data-table"' in html
        assert 'id="table-accessible-caption"' in html
        assert 'id="btn-copy-csv"' in html
        assert 'tabindex="0"' in html
        assert 'role="region"' in html

    def test_labeled_controls(self, client):
        """All dropdowns and switches should have associated labels."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert 'for="station-select"' in html
        assert 'for="metric-select"' in html
        assert 'for="range-select"' in html
        assert 'for="limit-select"' in html
        assert 'for="rollingWindow"' in html
        assert 'id="toggleRaw"' in html
        assert 'id="toggleRolling"' in html

    def test_reconnect_button_wcag_contrast(self, client):
        """Reconnect button background must provide >= 4.5:1 WCAG AA contrast against white text."""
        response = client.get("/static/styles.css")
        assert response.status_code == 200
        css = response.data.decode("utf-8")
        assert ".btn-reconnect {" in css
        assert "background: #92400e;" in css

    def test_app_js_sol_fixes(self, client):
        """app.js must include UTC table formatter, live stats arguments, and derived freshness."""
        response = client.get("/static/app.js")
        assert response.status_code == 200
        js = response.data.decode("utf-8")
        assert "formatUtcTimestamp" in js
        assert "scheduleCoalescedStatsRefresh" in js
        assert "latestTelemetryTime" in js
        assert "fetchStats(q, null, fetchId, station, metric, range)" in js

    def test_dark_theme_is_default(self, client):
        """Dashboard HTML must default to data-theme='dark' and provide dark theme controls."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert '<html lang="en" data-theme="dark">' in html
        assert 'id="btn-theme-toggle"' in html
        assert 'aria-pressed="true"' in html
        assert 'Switch to light theme' in html
        assert 'id="theme-toggle-label">Light</span>' in html

    def test_dark_theme_styles_and_tokens(self, client):
        """styles.css must default to dark color-scheme and provide dark theme root tokens."""
        response = client.get("/static/styles.css")
        assert response.status_code == 200
        css = response.data.decode("utf-8")
        assert ':root:not([data-theme="light"])' in css
        assert ':root[data-theme="dark"]' in css
        assert 'color-scheme: dark;' in css

    def test_app_js_defaults_to_dark_theme(self, client):
        """app.js must default currentTheme and preference loading to dark theme."""
        response = client.get("/static/app.js")
        assert response.status_code == 200
        js = response.data.decode("utf-8")
        assert "document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'" in js
        assert "applyTheme('dark', false)" in js



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
            "sample_mean": None, "time_weighted_mean": None,
            "count": 0, "sample_count": 0, "reading_age_seconds": None, "change": None,
            "change_details": {
                "available": False, "reason": "No readings in preceding period",
                "mean_change": None, "latest_change": None,
                "prev_mean": None, "prev_latest": None, "prev_count": 0,
            },
            "unit": "\u00b0C", "time_window": "range=24h", "is_calculated": False,
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


# ============================================================================
# 7. Server-Sent Events (SSE) Live Streaming Tests (Issue #8)
# ============================================================================


class TestStreamApi:
    """Tests for GET /api/stream Server-Sent Events endpoint."""

    def test_stream_headers_and_mimetype(self, client):
        """Endpoint should return 200 with text/event-stream and streaming headers."""
        response = client.get("/api/stream?max_events=1&poll_interval=0.01")
        assert response.status_code == 200
        assert "text/event-stream" in response.content_type
        assert response.headers.get("Cache-Control") == "no-cache, no-transform"
        assert response.headers.get("X-Accel-Buffering") == "no"
        assert response.headers.get("Connection") == "keep-alive"

    def test_stream_yields_events_with_since_id(self, client):
        """Stream should yield readings with id > since_id in SSE format."""
        response = client.get("/api/stream?since_id=0&limit=3&poll_interval=0.01")
        assert response.status_code == 200
        text = response.data.decode("utf-8")

        assert "id:" in text
        assert "data:" in text

        # Parse SSE messages
        events = [chunk for chunk in text.split("\n\n") if chunk.strip()]
        assert len(events) >= 1
        first_event = events[0]
        assert "id: 1" in first_event

        data_line = [line for line in first_event.split("\n") if line.startswith("data: ")][0]
        json_str = data_line[len("data: ") :]
        reading = json.loads(json_str)

        assert reading["id"] == 1
        assert "station_id" in reading
        assert "timestamp" in reading
        assert "temperature" in reading
        assert "humidity" in reading
        assert "windspeed" in reading
        assert "wind_speed" in reading

    def test_stream_filters_by_station_id(self, client):
        """Stream should only yield readings matching station_id when specified."""
        response = client.get("/api/stream?station_id=STATION-002&since_id=0&limit=5&poll_interval=0.01")
        assert response.status_code == 200
        text = response.data.decode("utf-8")

        events = [chunk for chunk in text.split("\n\n") if chunk.strip() and not chunk.startswith(":")]
        for ev in events:
            data_line = [line for line in ev.split("\n") if line.startswith("data: ")][0]
            reading = json.loads(data_line[len("data: ") :])
            assert reading["station_id"] == "STATION-002"

    def test_stream_supports_last_event_id_header(self, client):
        """Stream should resume from Last-Event-ID header cursor."""
        response = client.get(
            "/api/stream?station_id=STATION-001&limit=2&poll_interval=0.01",
            headers={"Last-Event-ID": "58"},
        )
        assert response.status_code == 200
        text = response.data.decode("utf-8")
        events = [chunk for chunk in text.split("\n\n") if chunk.strip() and not chunk.startswith(":")]
        assert len(events) == 2

        first_data = json.loads([line for line in events[0].split("\n") if line.startswith("data: ")][0][6:])
        second_data = json.loads([line for line in events[1].split("\n") if line.startswith("data: ")][0][6:])
        assert first_data["id"] == 59
        assert second_data["id"] == 60

    def test_stream_supports_since_timestamp(self, client, test_db_path: Path):
        """Stream should accept since ISO timestamp cursor."""
        conn = sqlite3.connect(str(test_db_path))
        conn.row_factory = sqlite3.Row
        target_row = conn.execute("SELECT timestamp, id FROM readings WHERE id = 55").fetchone()
        conn.close()

        target_ts = target_row["timestamp"]
        response = client.get(
            f"/api/stream?since={quote(target_ts)}&limit=3&poll_interval=0.01"
        )
        assert response.status_code == 200
        text = response.data.decode("utf-8")
        events = [chunk for chunk in text.split("\n\n") if chunk.strip() and not chunk.startswith(":")]
        assert len(events) >= 1
        first_data = json.loads([line for line in events[0].split("\n") if line.startswith("data: ")][0][6:])
        assert first_data["id"] >= 55

    def test_stream_since_future_timestamp_does_not_stream_history(self, client):
        """Stream with future since timestamp should default to MAX(id) and not replay history."""
        future_ts = "2099-01-01T00:00:00+00:00"
        response = client.get(
            f"/api/stream?station_id=STATION-001&since={quote(future_ts)}&max_events=1&poll_interval=0.01"
        )
        assert response.status_code == 200
        text = response.data.decode("utf-8")
        # Should have returned no data events (only empty or keepalive)
        events = [chunk for chunk in text.split("\n\n") if chunk.strip() and not chunk.startswith(":")]
        assert len(events) == 0

    def test_stream_handles_empty_db(self, client, tmp_path: Path):
        """Stream should handle empty database gracefully without raising exceptions."""
        empty_db = tmp_path / "empty_stream.db"
        conn = sqlite3.connect(str(empty_db))
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

        web.DB_PATH = empty_db
        response = client.get("/api/stream?max_events=1&poll_interval=0.01")
        assert response.status_code == 200
        assert "text/event-stream" in response.content_type


class TestReadingsSinceCursor:
    """Tests for api_readings since timestamp cursor."""

    def test_readings_since_timestamp_cursor(self, client, test_db_path: Path):
        """Readings with since parameter should return only records strictly newer than timestamp."""
        conn = sqlite3.connect(str(test_db_path))
        conn.row_factory = sqlite3.Row
        sample = conn.execute(
            "SELECT timestamp FROM readings WHERE station_id = 'STATION-001' ORDER BY timestamp ASC LIMIT 1 OFFSET 20"
        ).fetchone()
        conn.close()

        cursor_ts = sample["timestamp"]
        res = client.get(f"/api/readings?station_id=STATION-001&metric=temperature&since={quote(cursor_ts)}")
        assert res.status_code == 200
        points = res.get_json()["points"]
        assert len(points) > 0
        for p in points:
            assert p["t"] > cursor_ts


# ============================================================================
# 8. Charting & Downsampling Enhancement Tests
# ============================================================================


class TestGraphAndDownsamplingEnhancements:
    """Tests for peak/trough-preserving downsampling, multi-metric queries, and expanded ranges."""

    def test_downsample_peak_trough_preserves_extrema(self):
        """Downsampling must preserve local peaks and troughs and keep chronological order."""
        points = []
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        for i in range(100):
            ts = (base + timedelta(minutes=i * 5)).isoformat()
            val = 20.0 + (i % 10) * 0.5
            points.append({"t": ts, "v": val})

        # Inject extreme trough and peak
        points[35]["v"] = -15.5
        points[75]["v"] = 88.8

        downsampled = web.downsample_peak_trough(points, max_points=20)
        assert len(downsampled) <= 20
        # Extrema must be preserved
        vals = [p["v"] for p in downsampled]
        assert -15.5 in vals
        assert 88.8 in vals

        # Timestamps must remain strictly monotonically increasing
        ts_list = [p["t"] for p in downsampled]
        assert ts_list == sorted(ts_list)

        # Aggregation metadata attached
        for p in downsampled:
            assert "min" in p
            assert "max" in p
            assert "avg" in p
            assert "count" in p
            assert p["aggregated"] is True

    def test_downsample_peak_trough_noop_for_small_dataset(self):
        """Datasets with fewer points than max_points should remain unmodified."""
        points = [{"t": f"2026-01-01T00:0{i}:00+00:00", "v": 10.0 + i} for i in range(5)]
        downsampled = web.downsample_peak_trough(points, max_points=20)
        assert downsampled == points

    def test_api_stats_all_metrics(self, client):
        """Stats query with metric=all should return full dataset stats for all 3 metrics."""
        res = client.get("/api/stats?station_id=STATION-001&metric=all&range=24h")
        assert res.status_code == 200
        data = res.get_json()
        assert "temperature" in data
        assert "humidity" in data
        assert "windspeed" in data

        for metric in ["temperature", "humidity", "windspeed"]:
            m_stats = data[metric]
            assert m_stats["latest"] is not None
            assert m_stats["avg"] is not None
            assert m_stats["min"] <= m_stats["avg"] <= m_stats["max"]

    def test_api_stats_extended_ranges_and_limits(self, client):
        """Stats query should accept 1h, 7d, 30d, all, and limit 1000."""
        for r in ["1h", "7d", "30d", "all"]:
            res = client.get(f"/api/stats?station_id=STATION-001&metric=temperature&range={r}")
            assert res.status_code == 200
            assert "latest" in res.get_json()

        res_limit = client.get("/api/stats?station_id=STATION-001&metric=temperature&limit=1000")
        assert res_limit.status_code == 200
        assert res_limit.get_json()["latest"] is not None

    def test_api_readings_all_metrics(self, client):
        """Readings query with metric=all should return aligned points and per-metric series."""
        res = client.get("/api/readings?station_id=STATION-001&metric=all&range=24h")
        assert res.status_code == 200
        data = res.get_json()
        assert "points" in data
        assert "series" in data
        assert "temperature" in data["series"]
        assert "humidity" in data["series"]
        assert "windspeed" in data["series"]
        assert len(data["series"]["temperature"]) > 0

    def test_api_readings_downsampling(self, client):
        """Readings with downsample parameter should reduce points while marking aggregated."""
        res = client.get("/api/readings?station_id=STATION-001&metric=temperature&range=all&downsample=10")
        assert res.status_code == 200
        data = res.get_json()
        assert data.get("aggregated") is True
        assert data.get("total_points") == 60
        assert len(data["points"]) <= 12

    def test_api_readings_extended_range_all(self, client):
        """range=all should return all historical points for the station."""
        res = client.get("/api/readings?station_id=STATION-001&metric=temperature&range=all")
        assert res.status_code == 200
        points = res.get_json()["points"]
        assert len(points) == 60

    def test_downsample_peak_trough_with_gaps_and_nulls(self):
        """Downsampling should handle buckets with null/gap values gracefully and preserve gap markers."""
        points = [
            {"t": "2026-01-01T00:00:00+00:00", "v": None},
            {"t": "2026-01-01T00:01:00+00:00", "v": None},
            {"t": "2026-01-01T00:02:00+00:00", "v": 15.0},
            {"t": "2026-01-01T00:03:00+00:00", "v": 25.0},
            {"t": "2026-01-01T00:04:00+00:00", "v": 20.0},
            {"t": "2026-01-01T00:05:00+00:00", "v": None},
        ]
        downsampled = web.downsample_peak_trough(points, max_points=4)
        assert len(downsampled) <= 4
        assert any(p["v"] is None for p in downsampled)

    def test_api_readings_all_metrics_with_limit_and_since(self, client, test_db_path: Path):
        """metric=all should work with limit and since query parameters."""
        res_limit = client.get("/api/readings?station_id=STATION-001&metric=all&limit=10")
        assert res_limit.status_code == 200
        data_limit = res_limit.get_json()
        assert len(data_limit["points"]) == 10
        assert len(data_limit["series"]["temperature"]) == 10

        # Test since query
        conn = sqlite3.connect(str(test_db_path))
        conn.row_factory = sqlite3.Row
        sample = conn.execute("SELECT timestamp FROM readings WHERE station_id = 'STATION-001' LIMIT 1 OFFSET 20").fetchone()
        conn.close()
        since_ts = sample["timestamp"]

        res_since = client.get(f"/api/readings?station_id=STATION-001&metric=all&since={quote(since_ts)}")
        assert res_since.status_code == 200
        data_since = res_since.get_json()
        assert len(data_since["points"]) > 0
        for p in data_since["points"]:
            assert p["t"] > since_ts

    def test_api_stats_all_metrics_with_limit(self, client):
        """metric=all with limit should compute aggregates over newest N records for each metric."""
        res = client.get("/api/stats?station_id=STATION-001&metric=all&limit=10")
        assert res.status_code == 200
        data = res.get_json()
        assert data["temperature"]["latest"] is not None
        assert data["humidity"]["latest"] is not None
        assert data["windspeed"]["latest"] is not None


# ============================================================================
# 9. Enhanced Metrics & Calculated Dew Point API Tests
# ============================================================================


class TestEnhancedMetricsApi:
    """Comprehensive API tests for enhanced weather metrics and calculated dew point."""

    def test_stats_contains_all_required_enhanced_metric_fields(self, client):
        """Stats response must include latest, min, max, sample mean, time-weighted mean, count, age, and change."""
        res = client.get("/api/stats?station_id=STATION-001&metric=temperature&range=24h")
        assert res.status_code == 200
        data = res.get_json()

        # Core statistics
        assert "latest" in data and data["latest"] is not None
        assert "min" in data and data["min"] is not None
        assert "max" in data and data["max"] is not None
        assert "avg" in data and data["avg"] is not None
        assert "sample_mean" in data and data["sample_mean"] == data["avg"]
        assert "time_weighted_mean" in data and data["time_weighted_mean"] is not None
        assert "count" in data and data["count"] > 0
        assert "sample_count" in data and data["sample_count"] == data["count"]
        assert "reading_age_seconds" in data and data["reading_age_seconds"] is not None
        assert data["reading_age_seconds"] >= 0.0

        # Units and window
        assert data["unit"] == "°C"
        assert "range=24h" in data["time_window"]
        assert data["is_calculated"] is False

        # Period comparison details
        assert "change_details" in data
        assert "available" in data["change_details"]

    def test_stats_calculated_dew_point_single_and_all(self, client):
        """Dew point must be calculated from paired readings, labeled as calculated, with °C unit."""
        # Single metric query
        res_single = client.get("/api/stats?station_id=STATION-001&metric=dew_point&range=24h")
        assert res_single.status_code == 200
        dp_data = res_single.get_json()

        assert dp_data["latest"] is not None
        assert dp_data["sample_mean"] is not None
        assert dp_data["time_weighted_mean"] is not None
        assert dp_data["min"] <= dp_data["sample_mean"] <= dp_data["max"]
        assert dp_data["unit"] == "°C"
        assert dp_data["is_calculated"] is True

        # Multi-metric query (metric=all)
        res_all = client.get("/api/stats?station_id=STATION-001&metric=all&range=24h")
        assert res_all.status_code == 200
        all_data = res_all.get_json()

        assert "dew_point" in all_data
        assert all_data["dew_point"]["is_calculated"] is True
        assert all_data["dew_point"]["latest"] is not None
        assert all_data["dew_point"]["unit"] == "°C"

    def test_stats_preceding_period_change_available(self, tmp_path: Path):
        """When preceding equal-duration period has telemetry, change against preceding period is available."""
        test_db = tmp_path / "period_test.db"
        conn = sqlite3.connect(str(test_db))
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
        now = datetime.now(timezone.utc)
        # Current period: last 6 hours (now - 6h to now), constant 25.0°C
        # Preceding period: (now - 12h to now - 6h), constant 20.0°C
        rows = [
            ("STN-P", (now - timedelta(hours=9)).isoformat(), 20.0, 50.0, 5.0),
            ("STN-P", (now - timedelta(hours=8)).isoformat(), 20.0, 50.0, 5.0),
            ("STN-P", (now - timedelta(hours=3)).isoformat(), 25.0, 50.0, 5.0),
            ("STN-P", (now - timedelta(hours=1)).isoformat(), 25.0, 50.0, 5.0),
        ]
        conn.executemany(
            "INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()

        web.DB_PATH = test_db
        with web.app.test_client() as c:
            res = c.get("/api/stats?station_id=STN-P&metric=temperature&range=6h")
            assert res.status_code == 200
            data = res.get_json()
            assert data["change_details"]["available"] is True
            assert data["change_details"]["mean_change"] == 5.0  # 25.0 - 20.0
            assert data["change_details"]["prev_count"] == 2
            assert data["change"] == 5.0

    def test_stats_preceding_period_change_unavailable_all_and_limit(self, client):
        """Comparisons are explicitly unavailable for range=all and limit windows."""
        res_all = client.get("/api/stats?station_id=STATION-001&metric=temperature&range=all")
        assert res_all.status_code == 200
        data_all = res_all.get_json()
        assert data_all["change_details"]["available"] is False
        assert "all history" in data_all["change_details"]["reason"].lower()

        res_limit = client.get("/api/stats?station_id=STATION-001&metric=temperature&limit=50")
        assert res_limit.status_code == 200
        data_limit = res_limit.get_json()
        assert data_limit["change_details"]["available"] is False
        assert "record count" in data_limit["change_details"]["reason"].lower()

    def test_stats_preceding_period_change_unavailable_no_prior_readings(self, tmp_path: Path):
        """When preceding equal-duration period contains 0 readings, comparison is unavailable."""
        test_db = tmp_path / "no_prior.db"
        conn = sqlite3.connect(str(test_db))
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
        now = datetime.now(timezone.utc)
        # Only readings in last 1 hour, no readings between now - 2h and now - 1h
        conn.execute(
            "INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed) VALUES (?, ?, ?, ?, ?)",
            ("STN-SINGLE", (now - timedelta(minutes=30)).isoformat(), 21.0, 55.0, 4.0),
        )
        conn.commit()
        conn.close()

        web.DB_PATH = test_db
        with web.app.test_client() as c:
            res = c.get("/api/stats?station_id=STN-SINGLE&metric=temperature&range=1h")
            assert res.status_code == 200
            data = res.get_json()
            assert data["change_details"]["available"] is False
            assert "preceding period" in data["change_details"]["reason"].lower()

    def test_readings_all_includes_calculated_dew_point(self, client):
        """Readings with metric=all must include dew_point in points and series."""
        res = client.get("/api/readings?station_id=STATION-001&metric=all&limit=10")
        assert res.status_code == 200
        data = res.get_json()

        assert "dew_point" in data["series"]
        assert len(data["series"]["dew_point"]) == 10
        for p in data["points"]:
            assert "dew_point" in p
            assert p["dew_point"] is not None

    def test_readings_single_calculated_dew_point(self, client):
        """Readings with metric=dew_point returns calculated time series."""
        res = client.get("/api/readings?station_id=STATION-001&metric=dew_point&limit=10")
        assert res.status_code == 200
        data = res.get_json()
        assert len(data["points"]) == 10
        for p in data["points"]:
            assert "t" in p and "v" in p
            assert p["v"] is not None

    def test_stream_includes_calculated_dew_point(self, client):
        """Live SSE stream payload includes calculated dew_point."""
        res = client.get("/api/stream?station_id=STATION-001&since_id=0&limit=1&poll_interval=0.01")
        assert res.status_code == 200
        text = res.data.decode("utf-8")
        data_line = [line for line in text.split("\n") if line.startswith("data: ")][0]
        reading = json.loads(data_line[len("data: ") :])
        assert "dew_point" in reading
        assert reading["dew_point"] is not None



@pytest.mark.parametrize("metric,unit", [("temperature", "°C"), ("humidity", "%"), ("windspeed", "m/s"), ("dew_point", "°C")])
@pytest.mark.parametrize("window,reason", [("range=24h", "No readings in preceding period"), ("range=all", "Comparison unavailable for all history"), ("limit=10", "Comparison unavailable for record count window")])
def test_empty_metric_metadata(client, metric, unit, window, reason):
    data = client.get(f"/api/stats?station_id=NONEXISTENT&metric={metric}&{window}").get_json()
    assert data["sample_count"] == data["count"] == 0
    assert data["unit"] == unit
    assert data["is_calculated"] == (metric == "dew_point")
    assert data["sample_mean"] is data["time_weighted_mean"] is None
    assert data["reading_age_seconds"] is data["change"] is None
    assert data["change_details"]["available"] is False
    assert data["change_details"]["reason"] == reason
    assert data["time_window"] == window
