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
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
# Valid record-count ranges (for "last n values" option)
VALID_LIMITS = {
    "10": 10,
    "50": 50,
    "100": 100,
    "500": 500,
    "1000": 1000,
}
# Valid metrics - we'll detect actual column name at runtime
VALID_METRICS = {"temperature", "humidity", "windspeed", "wind_speed"}


def downsample_peak_trough(points, max_points=300):
    """Downsample time series while strictly preserving extreme peaks and troughs in each bucket.

    Preserves explicit null/gap boundaries by downsampling each contiguous valid segment
    independently and preserving gap separator points. Coalesces consecutive nulls into a single
    gap boundary. Enriches downsampled points with aggregated metadata (min, max, avg, count).
    """
    if not points or len(points) <= max_points or max_points < 2:
        return points

    # Split into contiguous valid segments and coalesce consecutive null points
    segments = []
    current_segment = []

    for p in points:
        if p.get("v") is None:
            if current_segment:
                segments.append(("valid", current_segment))
                current_segment = []
            if not segments or segments[-1][0] != "gap":
                segments.append(("gap", p))
        else:
            current_segment.append(p)
    if current_segment:
        segments.append(("valid", current_segment))

    total_valid = sum(len(seg) for tag, seg in segments if tag == "valid")
    if total_valid == 0:
        return [data for tag, data in segments if tag == "gap"]

    gap_count = sum(1 for tag, _ in segments if tag == "gap")
    available_budget = max(2, max_points - gap_count)

    result = []
    for tag, data in segments:
        if tag == "gap":
            result.append(data)
            continue

        seg_budget = max(2, int(available_budget * (len(data) / total_valid)))
        if len(data) <= seg_budget or seg_budget < 2:
            result.extend(data)
            continue

        num_buckets = max(1, seg_budget // 2)
        bucket_size = len(data) / num_buckets

        for b in range(num_buckets):
            start_idx = int(b * bucket_size)
            end_idx = int((b + 1) * bucket_size) if b < num_buckets - 1 else len(data)
            bucket = data[start_idx:end_idx]
            if not bucket:
                continue

            valid_points = [p for p in bucket if p.get("v") is not None]
            if not valid_points:
                result.append(bucket[0])
                continue

            min_pt = min(valid_points, key=lambda p: p["v"])
            max_pt = max(valid_points, key=lambda p: p["v"])
            avg_val = round(sum(p["v"] for p in valid_points) / len(valid_points), 2)
            min_val = round(min_pt["v"], 2)
            max_val = round(max_pt["v"], 2)
            count = len(valid_points)

            # Chronological order within bucket
            if min_pt["t"] <= max_pt["t"]:
                first_pt, second_pt = min_pt, max_pt
            else:
                first_pt, second_pt = max_pt, min_pt

            p1 = dict(first_pt)
            p1.update({"min": min_val, "max": max_val, "avg": avg_val, "count": count, "aggregated": True})
            result.append(p1)

            if second_pt != first_pt and count > 1:
                p2 = dict(second_pt)
                p2.update({"min": min_val, "max": max_val, "avg": avg_val, "count": count, "aggregated": True})
                result.append(p2)

    return result

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


def _compute_metric_stats(conn, station_id, metric_col, use_limit, max_records, cutoff_iso):
    """Compute latest, avg, min, max on the full selected dataset for a metric column."""
    if use_limit:
        cursor = conn.execute(
            f"""
            SELECT timestamp, {metric_col}
            FROM readings
            WHERE station_id = ? AND {metric_col} IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (station_id, max_records),
        )
        rows = cursor.fetchall()
        values = [row[metric_col] for row in rows]
        if values:
            return {
                "latest": round(values[0], 2),
                "latest_t": rows[0]["timestamp"],
                "avg": round(sum(values) / len(values), 2),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
            }
        return {"latest": None, "latest_t": None, "avg": None, "min": None, "max": None}
    else:
        where_clause = (
            f"WHERE station_id = ? AND timestamp >= ? AND {metric_col} IS NOT NULL"
            if cutoff_iso
            else f"WHERE station_id = ? AND {metric_col} IS NOT NULL"
        )
        params = (station_id, cutoff_iso) if cutoff_iso else (station_id,)
        agg_cursor = conn.execute(
            f"SELECT AVG({metric_col}), MIN({metric_col}), MAX({metric_col}), COUNT(*) FROM readings {where_clause}",
            params,
        )
        avg_val, min_val, max_val, point_count = agg_cursor.fetchone()

        if point_count > 0 and avg_val is not None:
            latest_cursor = conn.execute(
                f"SELECT timestamp, {metric_col} FROM readings {where_clause} ORDER BY timestamp DESC LIMIT 1",
                params,
            )
            latest_row = latest_cursor.fetchone()
            latest = latest_row[metric_col] if latest_row else None
            latest_t = latest_row["timestamp"] if latest_row else None
            return {
                "latest": round(latest, 2) if latest is not None else None,
                "latest_t": latest_t,
                "avg": round(avg_val, 2),
                "min": round(min_val, 2),
                "max": round(max_val, 2),
            }
        return {"latest": None, "latest_t": None, "avg": None, "min": None, "max": None}


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Get statistics for a given station, metric, and time range.

    Calculated from the FULL selected dataset.
    Query params:
      - station_id (required)
      - metric (required): temperature, humidity, windspeed, or all
      - range (optional): 1h, 6h, 24h, 7d, 30d, all
      - limit (optional): 10, 50, 100, 500, 1000
    """
    station_id = request.args.get("station_id", "").strip()
    metric = request.args.get("metric", "").strip()
    time_range = request.args.get("range", "24h").strip()
    limit = request.args.get("limit", "").strip()

    # Validation
    if not station_id or (metric not in VALID_METRICS and metric != "all"):
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
    use_limit = bool(limit and limit in VALID_LIMITS)
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

    # Calculate UTC time window (if time-based)
    delta = VALID_RANGES.get(time_range) if not use_limit else None
    cutoff_dt = (datetime.now(timezone.utc) - delta) if (not use_limit and delta is not None) else None
    cutoff_iso = cutoff_dt.isoformat() if cutoff_dt else None
    max_records = VALID_LIMITS.get(limit) if use_limit else None

    try:
        with contextlib.closing(get_db()) as conn:
            if metric == "all":
                wind_col = detect_wind_column()
                result = {
                    "temperature": _compute_metric_stats(conn, station_id, "temperature", use_limit, max_records, cutoff_iso),
                    "humidity": _compute_metric_stats(conn, station_id, "humidity", use_limit, max_records, cutoff_iso),
                    "windspeed": _compute_metric_stats(conn, station_id, wind_col, use_limit, max_records, cutoff_iso),
                }
            else:
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
                result = _compute_metric_stats(conn, station_id, db_metric, use_limit, max_records, cutoff_iso)

        range_label = f"limit={limit}" if use_limit else f"range={time_range}"
        logger.info(f"Stats for {station_id}/{metric}/{range_label} calculated from full dataset")
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
    """Get time series data for a given station, metric, and time range.

    Query params:
      - station_id (required)
      - metric (required): temperature, humidity, windspeed, or all
      - range (optional): 1h, 6h, 24h, 7d, 30d, all
      - limit (optional): 10, 50, 100, 500, 1000
      - since (optional): ISO timestamp cursor
      - downsample (optional): max point count to downsample with peak/trough preservation
    """
    station_id = request.args.get("station_id", "").strip()
    metric = request.args.get("metric", "").strip()
    time_range = request.args.get("range", "24h").strip()
    limit = request.args.get("limit", "").strip()
    since = request.args.get("since", "").strip()
    downsample_arg = request.args.get("downsample", "").strip()

    # Validation
    if not station_id or (metric not in VALID_METRICS and metric != "all"):
        return jsonify({"error": "Invalid parameters", "points": []}), 400

    # Determine if we're using time-based or record-based filtering
    use_limit = bool(limit and limit in VALID_LIMITS)
    if not use_limit and not since and time_range not in VALID_RANGES:
        return jsonify({"error": "Invalid range or limit", "points": []}), 400

    max_pts = None
    if downsample_arg:
        if downsample_arg.isdigit():
            max_pts = int(downsample_arg)
        elif downsample_arg.lower() in ("true", "1", "yes"):
            max_pts = 300

    # Calculate UTC time window (if time-based)
    delta = VALID_RANGES.get(time_range) if (not use_limit and not since) else None
    cutoff_dt = (datetime.now(timezone.utc) - delta) if (delta is not None) else None
    cutoff_iso = cutoff_dt.isoformat() if cutoff_dt else None

    try:
        with contextlib.closing(get_db()) as conn:
            wind_col = detect_wind_column()
            if metric == "all":
                cols = f"timestamp, temperature, humidity, {wind_col}"
                if since:
                    cursor = conn.execute(
                        f"SELECT {cols} FROM readings WHERE station_id = ? AND timestamp > ? ORDER BY timestamp ASC",
                        (station_id, since),
                    )
                    rows = cursor.fetchall()
                elif use_limit:
                    max_records = VALID_LIMITS[limit]
                    cursor = conn.execute(
                        f"SELECT {cols} FROM readings WHERE station_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (station_id, max_records),
                    )
                    rows = list(reversed(cursor.fetchall()))
                else:
                    where = "WHERE station_id = ? AND timestamp >= ?" if cutoff_iso else "WHERE station_id = ?"
                    params = (station_id, cutoff_iso) if cutoff_iso else (station_id,)
                    cursor = conn.execute(
                        f"SELECT {cols} FROM readings {where} ORDER BY timestamp ASC",
                        params,
                    )
                    rows = cursor.fetchall()

                temp_pts = [{"t": r["timestamp"], "v": r["temperature"]} for r in rows]
                hum_pts = [{"t": r["timestamp"], "v": r["humidity"]} for r in rows]
                wind_pts = [{"t": r["timestamp"], "v": r[wind_col]} for r in rows]

                raw_len = len(rows)
                is_aggregated = bool(max_pts and raw_len > max_pts)
                if max_pts:
                    temp_pts = downsample_peak_trough(temp_pts, max_pts)
                    hum_pts = downsample_peak_trough(hum_pts, max_pts)
                    wind_pts = downsample_peak_trough(wind_pts, max_pts)

                points = [
                    {
                        "t": r["timestamp"],
                        "temperature": r["temperature"],
                        "humidity": r["humidity"],
                        "windspeed": r[wind_col],
                    }
                    for r in rows
                ]
                result = {
                    "points": points,
                    "series": {
                        "temperature": temp_pts,
                        "humidity": hum_pts,
                        "windspeed": wind_pts,
                    },
                    "aggregated": is_aggregated,
                    "total_points": raw_len,
                }
                return jsonify(result)

            # Single metric query
            db_metric = normalize_metric(metric)
            if db_metric not in {"temperature", "humidity", "windspeed", "wind_speed"}:
                return jsonify({"error": "Invalid metric", "points": []}), 400

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
                points = [{"t": row["timestamp"], "v": row[db_metric]} for row in reversed(rows)]
            else:
                where = (
                    f"WHERE station_id = ? AND timestamp >= ? AND {db_metric} IS NOT NULL"
                    if cutoff_iso
                    else f"WHERE station_id = ? AND {db_metric} IS NOT NULL"
                )
                params = (station_id, cutoff_iso) if cutoff_iso else (station_id,)
                cursor = conn.execute(
                    f"""
                    SELECT timestamp, {db_metric}
                    FROM readings
                    {where}
                    ORDER BY timestamp ASC
                    """,
                    params,
                )
                rows = cursor.fetchall()
                points = [{"t": row["timestamp"], "v": row[db_metric]} for row in rows]

        raw_count = len(points)
        is_aggregated = bool(max_pts and raw_count > max_pts)
        if max_pts:
            points = downsample_peak_trough(points, max_pts)

        range_label = f"since={since}" if since else (f"limit={limit}" if use_limit else f"range={time_range}")
        logger.info(f"Readings for {station_id}/{metric}/{range_label}: {len(points)} points")
        response_dict = {"points": points}
        if is_aggregated:
            response_dict["aggregated"] = True
            response_dict["total_points"] = raw_count
        return jsonify(response_dict)

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
