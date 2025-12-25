"""
Weather Station Web UI
Flask app that reads from the SQLite DB and provides a clean dashboard
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request

# Configuration
DB_PATH = Path(__file__).parent / "data" / "weather.db"
ALLOWED_METRICS = ["temperature", "humidity", "windspeed"]
MAX_LIMIT = 500

app = Flask(__name__, template_folder="templates", static_folder="static")


def get_db_connection():
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def dict_from_row(row):
    """Convert sqlite3.Row to dict."""
    if row is None:
        return None
    return dict(row)


@app.route("/")
def index():
    """Render the main dashboard."""
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Health check failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/stations", methods=["GET"])
def get_stations():
    """Get list of unique station IDs."""
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            "SELECT DISTINCT station_id FROM readings ORDER BY station_id"
        )
        stations = [row[0] for row in cursor.fetchall()]
        conn.close()
        print(f"[API] GET /api/stations -> {len(stations)} stations")
        return jsonify({"stations": stations})
    except Exception as e:
        print(f"[API] Error in /api/stations: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    """Get list of available metrics."""
    print(f"[API] GET /api/metrics -> {ALLOWED_METRICS}")
    return jsonify({"metrics": ALLOWED_METRICS})


@app.route("/api/readings", methods=["GET"])
def get_readings():
    """
    Get time series data for a specific station and metric.
    Query params:
      - station_id (required): e.g., "STATION-001"
      - metric (required): one of "temperature", "humidity", "windspeed"
      - limit (optional): max rows to return, default 100, max 500
    Returns chronologically ordered (oldest -> newest) for Chart.js.
    """
    station_id = request.args.get("station_id", "").strip()
    metric = request.args.get("metric", "").strip()
    limit_str = request.args.get("limit", "100").strip()

    # Validation
    if not station_id:
        return jsonify({"error": "Missing station_id"}), 400
    if metric not in ALLOWED_METRICS:
        return jsonify({"error": f"Invalid metric. Must be one of {ALLOWED_METRICS}"}), 400

    try:
        limit = int(limit_str)
        limit = min(max(limit, 1), MAX_LIMIT)
    except ValueError:
        limit = 100

    try:
        conn = get_db_connection()
        cursor = conn.execute(
            f"""
            SELECT timestamp, {metric} as value FROM readings
            WHERE station_id = ? AND {metric} IS NOT NULL
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (station_id, limit),
        )
        rows = cursor.fetchall()
        conn.close()

        data = [
            {"timestamp": row["timestamp"], "value": row["value"]}
            for row in rows
        ]

        print(
            f"[API] GET /api/readings station={station_id} metric={metric} -> {len(data)} points"
        )
        return jsonify({"data": data})
    except Exception as e:
        print(f"[API] Error in /api/readings: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/latest", methods=["GET"])
def get_latest():
    """
    Get the latest readings for a station (or all stations).
    Query params:
      - station_id (optional): filter by station
      - limit (optional): max rows, default 50, max 500
    Returns newest readings first.
    """
    station_id = request.args.get("station_id", "").strip()
    limit_str = request.args.get("limit", "50").strip()

    try:
        limit = int(limit_str)
        limit = min(max(limit, 1), MAX_LIMIT)
    except ValueError:
        limit = 50

    try:
        conn = get_db_connection()

        if station_id:
            cursor = conn.execute(
                """
                SELECT station_id, timestamp, temperature, humidity, windspeed
                FROM readings
                WHERE station_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (station_id, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT station_id, timestamp, temperature, humidity, windspeed
                FROM readings
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )

        rows = cursor.fetchall()
        conn.close()

        data = [dict_from_row(row) for row in rows]
        print(
            f"[API] GET /api/latest station={station_id or 'all'} -> {len(data)} rows"
        )
        return jsonify({"data": data})
    except Exception as e:
        print(f"[API] Error in /api/latest: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print(f"Starting Weather Station Web UI")
    print(f"DB path: {DB_PATH}")
    print(f"Listening on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=True)
