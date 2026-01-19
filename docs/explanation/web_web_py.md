# web/web.py — Explanation

**Related docs:** [00_overview.md](00_overview.md), [web_index_html.md](web_index_html.md), [web_app_js.md](web_app_js.md)

## Purpose

The Flask web application provides a REST API and HTML dashboard for visualizing real-time weather data from the SQLite database. It reads data that the TCP server writes, allowing operators and students to monitor live metrics, view historical trends, and explore station data without modifying the database. The dashboard is a thin client: most filtering and aggregation happens server-side in Flask, while JavaScript handles UI interactivity and Chart.js rendering.

## Key Responsibilities

- Serve HTTP REST endpoints for stations, stats, and readings queries
- Connect to SQLite database (read-only for web, shared volume with server)
- Serve HTML dashboard + static assets (JavaScript, CSS)
- Parse query parameters (station_id, metric, time range, limit)
- Validate inputs to prevent SQL injection and resource exhaustion
- Aggregate statistics: latest, average, min, max for selected metrics
- Handle time-range queries (6h, 24h) or record-count limits (10–500)
- Auto-detect wind column name (windspeed vs wind_speed) for compatibility

## Important Blocks

### Block: Database Connection & Configuration

**Where:** `get_db()`, `DB_PATH`, `WEATHER_DB_PATH` env var, `WEB_HOST`, `WEB_PORT`

**What it does:**
1. Read `WEATHER_DB_PATH` env var (default: `../data/weather.db`)
2. Convert to Path object
3. On each request, open fresh SQLite connection with:
   - `check_same_thread=False` (Flask thread pool may differ from DB open thread)
   - `timeout=5.0` (wait 5 seconds if DB is locked)
   - `row_factory=sqlite3.Row` (return dict-like rows, not tuples)

**Inputs:** Environment variable (or default); request context

**Outputs:** SQLite connection object; rows as dict-like objects

**Why it matters:**
- Fresh connection per request avoids connection pool complexity
- Timeout handles brief write lock from server's writer task
- Row factory makes JSON serialization natural (dict keys)
- Stateless: each request is independent

**Failure modes:**
- DB file not found → sqlite3.OperationalError (logged as 500)
- DB locked for > 5s → sqlite3.OperationalError (logged as 500)
- Permission denied → OSError (logged as 500)

### Block: Wind Column Detection

**Where:** `detect_wind_column()`, `_wind_column_cache` global

**What it does:**
1. Query `PRAGMA table_info(readings)` to get column list
2. Check if "windspeed" or "wind_speed" exists in schema
3. Cache result in `_wind_column_cache` to avoid repeated queries
4. Default to "windspeed" if query fails

**Inputs:** SQLite connection; schema state

**Outputs:** Detected column name ("windspeed" or "wind_speed"); cached

**Why it matters:**
- Provides flexibility if schema changes between deployments
- Caching avoids repeated PRAGMA queries (small optimization)
- Graceful fallback to default if detection fails

**Failure modes:** 
- Neither column exists → defaults to "windspeed" (might fail on SELECT, but unlikely)
- DB connection fails → logged as warning; uses "windspeed"

### Block: Timestamp Parsing & Comparison

**Where:** `parse_timestamp(ts)`, time-range filtering in API endpoints

**What it does:**
1. Convert ISO 8601 string (with Z or +00:00) to Python datetime
2. Strip timezone info to avoid comparison issues (all treated as UTC)
3. Fall back to Unix timestamp parsing if ISO fails
4. Return naive datetime for range queries

**Inputs:** ISO 8601 or Unix timestamp string

**Outputs:** datetime object (naive, UTC)

**Why it matters:**
- Server sends ISO 8601 strings; web parses for time-range logic
- Naive datetime simplifies comparison (no tzinfo mismatches)
- Fallback handles edge cases

**Failure modes:**
- Invalid timestamp format → ValueError → return None (handled by caller)
- Mixed timezone formats → comparison still works (lexicographic string order)

### Block: Stations Endpoint

**Where:** `@app.route("/api/stations", methods=["GET"])`

**What it does:**
1. Open DB connection
2. Execute: `SELECT DISTINCT station_id FROM readings ORDER BY station_id`
3. Extract station_id values as list
4. Return JSON: `{"stations": [...]}`
5. On error: return `{"stations": []}`

**Inputs:** None (no query params)

**Outputs:** JSON with list of station IDs

**Why it matters:**
- First endpoint dashboard calls to populate station dropdown
- Simple query; fast even with many readings
- Empty list on error is safe (UI shows empty dropdown)

**Failure modes:**
- DB error → caught, logged, returns empty list
- No readings yet → empty list (expected initial state)

### Block: Stats Endpoint

**Where:** `@app.route("/api/stats", methods=["GET"])`

**What it does:**
1. Validate query params: `station_id` (required), `metric` (required), `range` or `limit` (optional)
2. Normalize metric name (e.g., "windspeed" → actual column)
3. Build time-range constraint: now - timedelta(hours) or LIMIT clause
4. Execute SELECT and compute: latest, latest_t, avg, min, max
5. Return JSON with stats

**Inputs:** Query params: `station_id`, `metric`, `range` (6h/24h) or `limit` (10/50/100/500)

**Outputs:** JSON: `{"latest": <float>, "latest_t": <timestamp>, "avg": <float>, "min": <float>, "max": <float>}`

**Why it matters:**
- Provides quick summary stats for UI stat cards
- Server-side aggregation reduces data transfer
- Time-range filtering prevents loading entire history

**Failure modes:**
- Invalid metric → 400 error (caught by validation)
- No data for station → stats are null
- DB error → logged, returns default stats

### Block: Readings Endpoint

**Where:** `@app.route("/api/readings", methods=["GET"])`

**What it does:**
1. Validate query params (same as stats endpoint)
2. Build WHERE clause: filter by station, time range, or limit
3. Execute: `SELECT timestamp, <metric> FROM readings WHERE ... ORDER BY timestamp DESC LIMIT <limit>`
4. Reverse order (DESC → ASC for chart rendering)
5. Return JSON: `{"points": [{"t": <timestamp>, "v": <value>}, ...]}`

**Inputs:** Query params: `station_id`, `metric`, `range` or `limit`

**Outputs:** JSON array of {timestamp, value} pairs

**Why it matters:**
- Provides raw data points for Chart.js rendering
- Time-series format (x=timestamp, y=value) matches chart expectations
- DESC then reverse ensures oldest-to-newest for chart (left-to-right time progression)

**Failure modes:**
- No data → empty points array
- DB error → logged, returns empty points

### Block: Query Parameter Validation

**Where:** Per endpoint (station_id, metric, range, limit checks)

**What it does:**
1. Extract params with `.get()` (returns None if missing)
2. Validate presence (required fields)
3. Validate against whitelist (VALID_RANGES, VALID_LIMITS, VALID_METRICS)
4. Clamp or default (e.g., limit defaults to 50, clamped to 1–500)
5. Return error response if invalid

**Inputs:** Query string parameters (user-provided)

**Outputs:** Validated values or 400 error

**Why it matters:**
- Prevents SQL injection (whitelist approach)
- Prevents resource exhaustion (limit clamping)
- Provides clear error feedback

**Failure modes:**
- Invalid metric → 400 error
- Invalid station_id → query returns no results (not an error; valid response)
- Huge limit → clamped to 500

### Block: HTML Dashboard Serving

**Where:** `@app.route("/")`

**What it does:**
1. Return `render_template("index.html")`
2. Flask loads template, serves with static asset URLs

**Inputs:** None (default route)

**Outputs:** HTML document with embedded links to CSS, JavaScript, Chart.js CDN

**Why it matters:**
- Entry point for browser; serves the UI
- Jinja2 template for `url_for()` dynamic asset URLs

**Failure modes:**
- Template not found → Flask 500 error
- Static assets not found → browser 404 (but page still loads)

## Data & State

### Global Configuration

```python
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "weather.db"
DB_PATH = Path(os.getenv("WEATHER_DB_PATH", str(DEFAULT_DB_PATH)))
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

VALID_RANGES = {"6h": timedelta(hours=6), "24h": timedelta(hours=24)}
VALID_LIMITS = {"10": 10, "50": 50, "100": 100, "500": 500}
VALID_METRICS = {"temperature", "humidity", "windspeed"}
```

### Per-Request State

```python
_wind_column_cache = None  # Cached column name to avoid repeated PRAGMA queries
```

### No Global DB Connection

- Each request opens fresh connection; no connection pooling
- Stateless design suitable for concurrent requests

## Control Flow Walkthrough

### Request Flow: Fetch Stats

```
GET /api/stats?station_id=STATION-001&metric=temperature&range=24h

1. @app.route handler called
2. Extract params: station_id="STATION-001", metric="temperature", range="24h"
3. Validate: metric in VALID_METRICS ✓, range in VALID_RANGES ✓
4. Compute time range: now - 24 hours
5. conn = get_db()
6. Execute:
   SELECT temperature FROM readings 
   WHERE station_id = ? AND timestamp > ?
7. Compute: latest, avg, min, max
8. conn.close()
9. Return JSON: {"latest": 22.5, "avg": 21.8, ...}
```

### Request Flow: Fetch Readings

```
GET /api/readings?station_id=STATION-001&metric=temperature&limit=50

1. Extract params: station_id, metric, limit="50"
2. Validate: metric, limit in VALID_LIMITS ✓, clamp if needed
3. conn = get_db()
4. Execute:
   SELECT timestamp, temperature FROM readings 
   WHERE station_id = ? ORDER BY timestamp DESC LIMIT 50
5. Reverse list (DESC → ASC for chart)
6. Format as: [{"t": "2025-01-17T14:30:45Z", "v": 22.5}, ...]
7. Return JSON: {"points": [...]}
```

## Interfaces

### Imports & Dependencies

```python
import os, sqlite3, logging, datetime, pathlib
from flask import Flask, render_template, jsonify, request
```

### Environment Variables

```
WEATHER_DB_PATH=/app/data/weather.db  (or relative default)
WEB_HOST=0.0.0.0                      (listen on all interfaces)
WEB_PORT=8000                         (HTTP port)
```

### Endpoints

| Method | Path | Query Params | Response |
|--------|------|--------------|----------|
| GET | `/` | — | HTML dashboard |
| GET | `/api/stations` | — | `{"stations": [...]}` |
| GET | `/api/stats` | station_id, metric, range/limit | `{"latest": 22.5, "avg": 21.8, ...}` |
| GET | `/api/readings` | station_id, metric, range/limit | `{"points": [...]}` |

### Database Queries

```sql
SELECT DISTINCT station_id FROM readings ORDER BY station_id

SELECT <metric> FROM readings 
WHERE station_id = ? AND timestamp > ? ORDER BY timestamp DESC

SELECT timestamp, <metric> FROM readings 
WHERE station_id = ? ORDER BY timestamp DESC LIMIT ?
```

## Observability & Debugging

### Logging

```python
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info(f"Detected wind column name: {_wind_column_cache}")
logger.error(f"Error fetching stations: {e}")
```

### Common Errors

| Error | Cause | Expected Response |
|-------|-------|---|
| 404 Not Found | Endpoint misspelled | Flask 404 |
| 400 Bad Request | Invalid metric or range | JSON error (though current code doesn't return explicit 400) |
| 500 Internal Server Error | DB connection failed, query failed | Logged exception; Flask default 500 |
| Empty stations list | No readings in DB yet | `{"stations": []}` (normal on startup) |
| Null stats | No data for station in time range | `{"latest": null, "avg": null, ...}` |

### Testing Endpoints

```bash
# List stations
curl http://localhost:8000/api/stations

# Get stats for past 24h
curl "http://localhost:8000/api/stats?station_id=STATION-001&metric=temperature&range=24h"

# Get last 10 readings
curl "http://localhost:8000/api/readings?station_id=STATION-001&metric=temperature&limit=10"
```

---

**Key Takeaway:** The Flask web app is a simple, stateless REST layer over SQLite. Each request opens a fresh DB connection, validates inputs strictly, and returns JSON. No caching complexity; no connection pools. This keeps the code simple and suitable for a classroom learning context.
