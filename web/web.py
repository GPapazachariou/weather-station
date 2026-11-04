"""
Weather Station Web UI Dashboard
Minimal Flask app for visualizing weather data from SQLite database.
"""

import contextlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "weather.db"
DB_PATH = Path(os.getenv("WEATHER_DB_PATH", str(DEFAULT_DB_PATH)))
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

# Valid time ranges and their durations
VALID_RANGES = {
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
}
# Valid record-count ranges (for "last n values" option)
VALID_LIMITS = {
    "10": 10,
    "50": 50,
    "100": 100,
    "500": 500,
}
# Valid metrics - we'll detect actual column name at runtime
VALID_METRICS = {"temperature", "humidity", "windspeed", "wind_speed"}

# Cache for detected wind column name
_wind_column_cache = None

# Cache for database index verification
_indexes_checked_db = None

app = Flask(__name__, template_folder="templates", static_folder="static")


def init_db_indexes(db_path=None):
    """Ensure database compound indexes are initialized if readings table exists.

    Creates idx_readings_station_ts and idx_readings_station_id_desc idempotently
    to prevent full table scans when web service is executed independently.
    """
    global _indexes_checked_db
    target_path = Path(db_path) if db_path is not None else DB_PATH
    if not target_path.exists():
        return False

    try:
        if db_path is not None:
            conn = sqlite3.connect(str(target_path), check_same_thread=False, timeout=5.0)
        else:
            conn = get_db()
        with contextlib.closing(conn) as c:
            cursor = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='readings'"
            )
            if cursor.fetchone() is None:
                return False

            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_readings_station_ts
                ON readings(station_id, timestamp DESC)
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_readings_station_id_desc
                ON readings(station_id, id DESC)
                """
            )
            c.commit()
            if db_path is None or str(target_path) == str(DB_PATH):
                _indexes_checked_db = str(DB_PATH)
            logger.info(f"Database compound indexes initialized for {target_path}")
            return True
    except Exception as e:
        logger.warning(f"Could not verify or initialize database indexes: {e}")
        return False


@app.before_request
def _ensure_indexes_before_request():
    """Ensure indexes on request if not already verified for the current DB."""
    global _indexes_checked_db
    if _indexes_checked_db != str(DB_PATH):
        init_db_indexes()


def detect_wind_column():
    """Detect whether DB uses 'windspeed' or 'wind_speed' column."""
    global _wind_column_cache
    if _wind_column_cache is not None:
        return _wind_column_cache
    
    try:
        with contextlib.closing(get_db()) as conn:
            cursor = conn.execute("PRAGMA table_info(readings)")
            columns = [row[1] for row in cursor.fetchall()]
        
        if "windspeed" in columns:
            _wind_column_cache = "windspeed"
        elif "wind_speed" in columns:
            _wind_column_cache = "wind_speed"
        else:
            _wind_column_cache = "windspeed"  # default fallback
        
        logger.info(f"Detected wind column name: {_wind_column_cache}")
        return _wind_column_cache
    except Exception as e:
        logger.warning(f"Could not detect wind column, using 'windspeed': {e}")
        _wind_column_cache = "windspeed"
        return _wind_column_cache


def normalize_metric(metric):
    """Convert UI metric name to actual DB column name."""
    if metric in ("windspeed", "wind_speed"):
        return detect_wind_column()
    return metric


def get_db():
    """Get database connection with optimized settings."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    """Serve main dashboard."""
    return render_template("index.html")


@app.route("/api/stations", methods=["GET"])
def api_stations():
    """Get list of unique station IDs."""
    try:
        with contextlib.closing(get_db()) as conn:
            cursor = conn.execute(
                "SELECT DISTINCT station_id FROM readings ORDER BY station_id"
            )
            stations = [row[0] for row in cursor.fetchall()]
        logger.info(f"Fetched {len(stations)} stations")
        return jsonify({"stations": stations})
    except Exception as e:
        logger.error(f"Error fetching stations: {e}")
        return jsonify({"stations": []})


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """
    Get statistics for a given station, metric, and time range.
    Query params:
      - station_id (required)
      - metric (required): temperature, humidity, or windspeed
      - range (optional): 6h, 24h (time-based) or limit param for record-based
      - limit (optional): 10, 50, 100, 500 (for last n values)
    
    Returns:
      {"latest": 23.5, "latest_t": "2025-12-25T12:00:00", "avg": 22.1, "min": 20.0, "max": 25.5}
    """
    station_id = request.args.get("station_id", "").strip()
    metric = request.args.get("metric", "").strip()
    time_range = request.args.get("range", "24h").strip()
    limit = request.args.get("limit", "").strip()

    # Validation
    if not station_id or metric not in VALID_METRICS:
        return (
            jsonify(
                {
                    "error": "Invalid parameters",
                    "latest": None,
                    "latest_t": None,
                    "avg": None,
                    "min": None,
                    "max": None,
                }
            ),
            400,
        )

    # Determine if we're using time-based or record-based filtering
    use_limit = limit and limit in VALID_LIMITS
    if not use_limit and time_range not in VALID_RANGES:
        return (
            jsonify(
                {
                    "error": "Invalid range or limit",
                    "latest": None,
                    "latest_t": None,
                    "avg": None,
                    "min": None,
                    "max": None,
                }
            ),
            400,
        )

    # Whitelist safety check for dynamic metric column
    db_metric = normalize_metric(metric)
    if db_metric not in {"temperature", "humidity", "windspeed", "wind_speed"}:
        return (
            jsonify(
                {
                    "error": "Invalid metric",
                    "latest": None,
                    "latest_t": None,
                    "avg": None,
                    "min": None,
                    "max": None,
                }
            ),
            400,
        )

    # Calculate UTC time window (if time-based)
    cutoff_dt = datetime.now(timezone.utc) - VALID_RANGES[time_range] if not use_limit else None
    cutoff_iso = cutoff_dt.isoformat() if cutoff_dt else None

    try:
        with contextlib.closing(get_db()) as conn:
            if use_limit:
                max_records = VALID_LIMITS[limit]
                cursor = conn.execute(
                    f"""
                    SELECT timestamp, {db_metric}
                    FROM readings
                    WHERE station_id = ? AND {db_metric} IS NOT NULL
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (station_id, max_records),
                )
                rows = cursor.fetchall()
                values = [row[db_metric] for row in rows]
                latest = values[0] if values else None
                latest_t = rows[0]["timestamp"] if rows else None
                point_count = len(values)

                # Calculate stats
                if values:
                    result = {
                        "latest": round(latest, 2),
                        "latest_t": latest_t,
                        "avg": round(sum(values) / len(values), 2),
                        "min": round(min(values), 2),
                        "max": round(max(values), 2),
                    }
                else:
                    result = {
                        "latest": None,
                        "latest_t": None,
                        "avg": None,
                        "min": None,
                        "max": None,
                    }
            else:
                agg_cursor = conn.execute(
                    f"""
                    SELECT AVG({db_metric}), MIN({db_metric}), MAX({db_metric}), COUNT(*)
                    FROM readings
                    WHERE station_id = ? AND timestamp >= ? AND {db_metric} IS NOT NULL
                    """,
                    (station_id, cutoff_iso),
                )
                avg_val, min_val, max_val, point_count = agg_cursor.fetchone()

                if point_count > 0 and avg_val is not None:
                    latest_cursor = conn.execute(
                        f"""
                        SELECT timestamp, {db_metric}
                        FROM readings
                        WHERE station_id = ? AND timestamp >= ? AND {db_metric} IS NOT NULL
                        ORDER BY timestamp DESC
                        LIMIT 1
                        """,
                        (station_id, cutoff_iso),
                    )
                    latest_row = latest_cursor.fetchone()
                    latest = latest_row[db_metric] if latest_row else None
                    latest_t = latest_row["timestamp"] if latest_row else None
                    result = {
                        "latest": round(latest, 2) if latest is not None else None,
                        "latest_t": latest_t,
                        "avg": round(avg_val, 2),
                        "min": round(min_val, 2),
                        "max": round(max_val, 2),
                    }
                else:
                    result = {
                        "latest": None,
                        "latest_t": None,
                        "avg": None,
                        "min": None,
                        "max": None,
                    }

        range_label = f"limit={limit}" if use_limit else f"range={time_range}"
        logger.info(f"Stats for {station_id}/{metric}/{range_label}: {point_count} points")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return jsonify(
            {
                "latest": None,
                "latest_t": None,
                "avg": None,
                "min": None,
                "max": None,
            }
        )


@app.route("/api/readings", methods=["GET"])
def api_readings():
    """
    Get time series data for a given station, metric, and time range.
    Query params:
      - station_id (required)
      - metric (required): temperature, humidity, or windspeed
      - range (optional): 6h, 24h (time-based) or limit param for record-based
      - limit (optional): 10, 50, 100, 500 (for last n values)
      - since (optional): ISO timestamp cursor (fetches points strictly after timestamp)
    
    Returns:
      {"points": [{"t": "2025-12-25T10:00:00", "v": 23.1}, ...]}
      (ordered oldest -> newest for Chart.js)
    """
    station_id = request.args.get("station_id", "").strip()
    metric = request.args.get("metric", "").strip()
    time_range = request.args.get("range", "24h").strip()
    limit = request.args.get("limit", "").strip()
    since = request.args.get("since", "").strip()

    # Validation
    if not station_id or metric not in VALID_METRICS:
        return jsonify({"error": "Invalid parameters", "points": []}), 400

    # Determine if we're using time-based or record-based filtering
    use_limit = limit and limit in VALID_LIMITS
    if not use_limit and not since and time_range not in VALID_RANGES:
        return jsonify({"error": "Invalid range or limit", "points": []}), 400

    # Whitelist safety check for dynamic metric column
    db_metric = normalize_metric(metric)
    if db_metric not in {"temperature", "humidity", "windspeed", "wind_speed"}:
        return jsonify({"error": "Invalid metric", "points": []}), 400

    # Calculate UTC time window (if time-based)
    cutoff_dt = datetime.now(timezone.utc) - VALID_RANGES[time_range] if not use_limit and not since else None
    cutoff_iso = cutoff_dt.isoformat() if cutoff_dt else None

    try:
        with contextlib.closing(get_db()) as conn:
            if since:
                cursor = conn.execute(
                    f"""
                    SELECT timestamp, {db_metric}
                    FROM readings
                    WHERE station_id = ? AND timestamp > ? AND {db_metric} IS NOT NULL
                    ORDER BY timestamp ASC
                    """,
                    (station_id, since),
                )
                rows = cursor.fetchall()
                points = [{"t": row["timestamp"], "v": row[db_metric]} for row in rows]
            elif use_limit:
                max_records = VALID_LIMITS[limit]
                cursor = conn.execute(
                    f"""
                    SELECT timestamp, {db_metric}
                    FROM readings
                    WHERE station_id = ? AND {db_metric} IS NOT NULL
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (station_id, max_records),
                )
                rows = cursor.fetchall()
                # Reverse newest N rows so Chart.js receives chronological order (oldest -> newest)
                points = [{"t": row["timestamp"], "v": row[db_metric]} for row in reversed(rows)]
            else:
                cursor = conn.execute(
                    f"""
                    SELECT timestamp, {db_metric}
                    FROM readings
                    WHERE station_id = ? AND timestamp >= ? AND {db_metric} IS NOT NULL
                    ORDER BY timestamp ASC
                    """,
                    (station_id, cutoff_iso),
                )
                rows = cursor.fetchall()
                points = [{"t": row["timestamp"], "v": row[db_metric]} for row in rows]

        range_label = f"since={since}" if since else (f"limit={limit}" if use_limit else f"range={time_range}")
        logger.info(
            f"Readings for {station_id}/{metric}/{range_label}: {len(points)} points"
        )
        return jsonify({"points": points})

    except Exception as e:
        logger.error(f"Error fetching readings: {e}")
        return jsonify({"points": []})


@app.route("/api/stream", methods=["GET"])
def api_stream():
    """Server-Sent Events (SSE) live telemetry stream.

    Query params:
      - station_id (optional): Filter readings by station ID.
      - since_id (optional): Fetch records with id > since_id.
      - since (optional): Timestamp cursor if since_id not provided.
      - limit / max_events (optional): Stop generator after N events (useful for tests).
      - poll_interval (optional): Polling frequency in seconds (default: 1.0s).

    Headers:
      - Last-Event-ID (optional): Resume stream from last received row id.
    """
    station_id = request.args.get("station_id", "").strip()

    # Cursor handling: header 'Last-Event-ID' or query param 'since_id' / 'last_id'
    last_id_param = request.headers.get("Last-Event-ID") or request.args.get("since_id") or request.args.get("last_id")
    since_timestamp = request.args.get("since", "").strip()
    max_events_param = request.args.get("limit") or request.args.get("max_events")
    poll_interval_param = request.args.get("poll_interval") or request.args.get("interval")

    max_events = int(max_events_param) if max_events_param and max_events_param.isdigit() else None
    try:
        poll_interval = float(poll_interval_param) if poll_interval_param is not None else 1.0
    except ValueError:
        poll_interval = 1.0

    initial_last_id = None
    if last_id_param is not None:
        try:
            initial_last_id = int(last_id_param)
        except ValueError:
            initial_last_id = None

    def event_stream():
        nonlocal initial_last_id
        last_id = initial_last_id
        wind_col = detect_wind_column()
        events_yielded = 0

        conn = get_db()
        try:
            # If no cursor provided, default to current MAX(id) so client only receives live updates
            if last_id is None:
                if since_timestamp:
                    st_filter = " AND station_id = ?" if station_id else ""
                    st_args = (since_timestamp, station_id) if station_id else (since_timestamp,)
                    cursor = conn.execute(
                        f"SELECT MIN(id) FROM readings WHERE timestamp >= ?{st_filter}",
                        st_args,
                    )
                    row = cursor.fetchone()
                    if row and row[0] is not None:
                        last_id = max(0, row[0] - 1)
                    else:
                        st_clause = " WHERE station_id = ?" if station_id else ""
                        max_args = (station_id,) if station_id else ()
                        cursor = conn.execute(
                            f"SELECT COALESCE(MAX(id), 0) FROM readings{st_clause}",
                            max_args,
                        )
                        max_row = cursor.fetchone()
                        last_id = max_row[0] if max_row and max_row[0] is not None else 0
                else:
                    if station_id:
                        cursor = conn.execute(
                            "SELECT COALESCE(MAX(id), 0) FROM readings WHERE station_id = ?",
                            (station_id,),
                        )
                    else:
                        cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM readings")
                    row = cursor.fetchone()
                    last_id = row[0] if row and row[0] is not None else 0

            keepalive_counter = 0
            while True:
                try:
                    query = f"""
                        SELECT id, station_id, timestamp, temperature, humidity, {wind_col}
                        FROM readings
                        WHERE id > ?
                    """
                    params = [last_id]
                    if station_id:
                        query += " AND station_id = ?"
                        params.append(station_id)
                    query += " ORDER BY id ASC LIMIT 50"

                    cursor = conn.execute(query, tuple(params))
                    rows = cursor.fetchall()

                    if rows:
                        for row in rows:
                            last_id = row["id"]
                            data = {
                                "id": row["id"],
                                "station_id": row["station_id"],
                                "timestamp": row["timestamp"],
                                "temperature": row["temperature"],
                                "humidity": row["humidity"],
                                "windspeed": row[wind_col],
                                "wind_speed": row[wind_col],
                            }
                            yield f"id: {last_id}\ndata: {json.dumps(data)}\n\n"
                            events_yielded += 1
                            if max_events is not None and events_yielded >= max_events:
                                return
                        keepalive_counter = 0
                    else:
                        keepalive_counter += 1
                        if keepalive_counter >= 15:
                            yield ": keepalive\n\n"
                            keepalive_counter = 0
                        if max_events is not None and events_yielded >= max_events:
                            return

                    if max_events is not None and not rows:
                        return

                    time.sleep(poll_interval)
                except sqlite3.OperationalError as e:
                    logger.warning(f"Database error in event stream: {e}")
                    time.sleep(poll_interval)
                except GeneratorExit:
                    logger.info("Client disconnected from event stream")
                    break
        finally:
            conn.close()

    response = Response(
        event_stream(),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Weather Station Web UI Dashboard")
    logger.info("=" * 60)
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"Database exists: {DB_PATH.exists()}")
    logger.info(f"Server: http://{WEB_HOST}:{WEB_PORT}")
    logger.info("=" * 60)

    if not DB_PATH.exists():
        logger.warning(f"Database not found: {DB_PATH}")
        logger.warning("Dashboard will start but show no data until server populates the DB.")
    else:
        # Detect and log wind column name
        wind_col = detect_wind_column()
        logger.info(f"Using wind column name: {wind_col}")
        init_db_indexes()

    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)
