# Weather Station Simulator: How to Run & Demo Guide

## 1. Project Overview

The **Weather Station Simulator** is a distributed, real-time data ingestion platform demonstrating socket-based networking, message validation, and persistent storage. Multiple weather station clients continuously send sensor readings (temperature, humidity, windspeed) over TCP to a central server. The server validates JSON batches, persists data in SQLite, and provides a web dashboard for live visualization. Optional consumer clients can query the server for station data using the same TCP protocol.

**Key characteristics:**
- **Multi-client architecture:** Resilient weather station clients with exponential backoff + local buffering.
- **Asyncio TCP server:** Concurrent connections, single-writer queue to avoid SQLite lock contention.
- **Newline-delimited JSON protocol:** Multiplexes producer batches and consumer read-only requests on one TCP stream.
- **Dockerized stack:** All components packaged in containers; data persists in a named volume.
- **Minimal web UI:** Flask dashboard reading SQLite for live charts and metrics.

---

## 2. Architecture at a Glance

| Component | Role | Port | Technology |
|-----------|------|------|------------|
| **TCP Server** | Listens for JSON batches/requests; validates & persists to SQLite | 12345 | Python asyncio |
| **Weather Station Client(s)** | Generate sensor readings; send newline-delimited JSON batches; auto-reconnect on failure | (connects to 12345) | Python asyncio |
| **Web UI** | Serves HTML dashboard + REST API for metrics visualization | 8000 | Flask + SQLite |
| **Consumer Client** | Test harness sending read-only requests (stations/latest/recent) | (connects to 12345) | Python asyncio |

**Data flow diagram:**

```
┌──────────────────────────────────────────────────────────────┐
│                    WEATHER STATION SIMULATOR                  │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  [STATION-001]  [STATION-002]  [STATION-003]  [STRESS-NNN]   │
│       │               │              │              │         │
│       └───────────────┴──────────────┴──────────────┘         │
│                       │                                        │
│                       │ NDJSON Batch (JSON list)              │
│                       │ TCP :12345                            │
│                       ▼                                        │
│          ┌─────────────────────────────┐                      │
│          │   TCP Server (asyncio)      │                      │
│          │ • Parse & validate batch    │                      │
│          │ • Multiplexed producer/     │                      │
│          │   consumer requests         │                      │
│          └─────────────────────────────┘                      │
│                       │                                        │
│                       │ Single Writer Queue                    │
│                       │ (avoid DB lock contention)            │
│                       ▼                                        │
│          ┌─────────────────────────────┐                      │
│          │   SQLite Database Volume    │                      │
│          │   /app/data/weather.db      │                      │
│          │ (WAL mode, PRAGMA tuned)    │                      │
│          └─────────────────────────────┘                      │
│                  ▲          │                                  │
│     SELECT       │          │ SELECT                          │
│     (read-only)  │          │ (read-only)                     │
│                  │          ▼                                  │
│          ┌─────────────────────────────┐                      │
│          │   Flask Web Dashboard       │                      │
│          │   :8000/api/stations        │                      │
│          │   :8000/api/stats           │                      │
│          │   :8000/api/readings        │                      │
│          │   :8000/ (HTML UI)          │                      │
│          └─────────────────────────────┘                      │
│                                                                │
│          ┌─────────────────────────────┐                      │
│          │   Consumer Client (test)    │                      │
│          │ • {"request":"stations"}    │                      │
│          │ • {"request":"latest"}      │                      │
│          │ • {"request":"recent"}      │                      │
│          └─────────────────────────────┘                      │
│                       │                                        │
│     JSON Request      │                                        │
│     TCP :12345        │                                        │
│                       ▼                                        │
│             TCP Server (read-only path)                       │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Requirements

### Docker + Docker Compose
- **Docker Desktop** (Windows/macOS) or **Docker Engine + Docker Compose** (Linux)
- Minimum: Docker Engine 20.10+, Docker Compose 2.0+

### Python (for running tests/clients locally, optional)
- Python 3.9+ (tested on 3.11)
- No external dependencies for basic runs; see `server/requirements.txt`, `web/requirements.txt`, `station_client/requirements.txt` for containerized dependencies

---

## 4. Quick Start (Recommended: Docker Compose)

### 4.1 Build and Run the Stack

**On Windows (PowerShell):**
```powershell
# Navigate to project directory
cd weather-station

# Start all services (server + 3 station clients + web UI)
docker compose up --build
```

**On macOS/Linux:**
```bash
cd weather-station
docker compose up --build
```

What happens:
1. Docker builds images for server, station clients, and web UI.
2. Services start: TCP server (port 12345), 3 station clients, web UI (port 8000).
3. Weather data flows: stations send batches every 5 seconds → server validates & writes → SQLite.
4. Logs stream to terminal.

### 4.2 View Logs

**Watch server activity:**
```bash
docker compose logs -f server
```

**Watch a specific station:**
```bash
docker compose logs -f station-001
```

**Watch all services:**
```bash
docker compose logs -f
```

### 4.3 Open the Web Dashboard

1. Wait 10–15 seconds for server health check to pass and data to start flowing.
2. Open browser: **http://localhost:8000**
3. You should see:
   - List of active stations (STATION-001, STATION-002, STATION-003)
   - Charts showing temperature, humidity, windspeed over time
   - Stats: latest, average, min, max for selected metrics

### 4.4 Stop and Clean Up

**Stop all services (keeps data/volume):**
```bash
docker compose down
```

**Stop all services and delete volume (fresh start next time):**
```bash
docker compose down -v
```

---

## 5. Live Demo Script (5–10 minutes)

Follow this sequence in a live presentation:

### Step 1: Start services
```bash
docker compose up --build
```
Wait ~15 seconds for health checks and first batches to be inserted.

### Step 2: Monitor server logs
```bash
# In a new terminal
docker compose logs -f server
```
You should see entries like:
```
Database initialized: /app/data/weather.db
Client connected: <IP>
Processed producer batch from <IP>: station-001, 1 readings inserted
...
```

### Step 3: Open web dashboard
- Navigate to **http://localhost:8000**
- Demonstrate:
  - Stations list is populated (STATION-001, STATION-002, STATION-003)
  - Select a station and metric (e.g., STATION-001, Temperature)
  - Charts update in real-time every 5 seconds (station clients send batches every 5s)
  - Switch between 6-hour and 24-hour time ranges
  - Stats panel shows latest, average, min, max

### Step 4: Run consumer protocol tests
```bash
# In a new terminal, navigate to project directory
python consumer_client/test_consumer.py
```

Expected output (tests will pass):
```
==================================================
Weather Station Consumer Tests
==================================================
Server: localhost:12345

=== Test 1: Producer Batch Ingest ===
  Response: {"status": "ok", "inserted": 2}
  ✓ Producer batch ingest successful: 2 readings inserted

=== Test 2: Consumer Request - Stations ===
  Response: {"status": "ok", "stations": ["STATION-001", "STATION-002", "STATION-003"]}
  ✓ Consumer stations request successful: ['STATION-001', 'STATION-002', 'STATION-003']

=== Test 3: Consumer Request - Latest (All Stations) ===
  Response: {"status": "ok", "reading": {...}}
  ✓ Consumer latest request successful

=== Test 4: Consumer Request - Latest (Specific Station) ===
  Response: {"status": "ok", "reading": {...}}
  ✓ Consumer latest station request successful

...

==================================================
Results: 8/8 tests passed
==================================================
```

### Step 5: Show stress test config (optional)
```bash
# Show auto-generated stress compose (100 stations, fast intervals)
cat docker-compose.stress.yml | head -50
```

---

## 6. How to Run Without Docker (Optional)

If you want to run components locally (not recommended for production, but useful for development):

### 6.1 Prerequisites
- Python 3.9+
- SQLite (included with Python)

### 6.2 Setup Environment

**Windows (PowerShell):**
```powershell
# Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install aiosqlite flask
```

**macOS/Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install aiosqlite flask
```

### 6.3 Run Components

**Terminal 1: Start the server**
```bash
cd server
python app.py
```
Expected output:
```
Database initialized: /app/data/weather.db
Server listening on 0.0.0.0:12345
```

**Terminal 2: Start the web UI**
```bash
cd web
python web.py
```
Expected output:
```
 * Running on http://127.0.0.1:8000
```

**Terminal 3: Start a weather station client**
```bash
cd station_client
python client.py --station-id STATION-001 --host localhost --port 12345
```

**Terminal 4 (optional): Start another client**
```bash
cd station_client
python client.py --station-id STATION-002 --host localhost --port 12345
```

Then open http://localhost:8000 in your browser.

---

## 7. Testing

### 7.1 Consumer Protocol Tests

The `consumer_client/test_consumer.py` script validates the producer batch ingest and consumer request paths.

**Run tests (server must be running):**
```bash
# Via Docker Compose stack
docker compose up -d  # Start in background
python consumer_client/test_consumer.py

# Or, via local Python
python consumer_client/test_consumer.py
```

**What it tests:**
1. Producer batch ingest: send 2 readings, verify `{"status": "ok", "inserted": 2}`
2. Consumer stations request: list all unique station IDs
3. Consumer latest request: get newest reading (all stations or by station_id)
4. Consumer recent request: get last N readings with optional limit
5. Invalid request handling: verify unknown request types are rejected

**Success:** All 8 tests pass (✓ checkmarks in output).

### 7.2 API Sanity Check

**Test API endpoints directly (via curl or browser):**

```bash
# List stations
curl http://localhost:8000/api/stations

# Expected response:
# {"stations": ["STATION-001", "STATION-002", "STATION-003"]}

# Get stats for a station
curl "http://localhost:8000/api/stats?station_id=STATION-001&metric=temperature&range=24h"

# Expected response:
# {"latest": 22.5, "latest_t": "2025-01-17T14:30:45Z", "avg": 22.1, "min": 20.0, "max": 25.5}
```

### 7.3 Common Failure Checklist

| Issue | Cause | Solution |
|-------|-------|----------|
| Connection refused (12345) | Server not running | Run `docker compose up` or `python server/app.py` |
| Port 12345 already in use | Another service on same port | Stop other services or change port in compose |
| Port 8000 already in use | Another service on same port | Stop Flask or change `WEB_PORT` env var |
| Web dashboard shows no stations | No clients running or DB is empty | Run `docker compose up` to start clients; wait 15–30s for first batches |
| DB not persisting across restarts | Volume not mounted or wrong `DB_PATH` | Verify `weather-data` volume in `docker compose ps` |
| "Database is locked" errors | Multiple writers (SQLite contention) | Normal for stress test; single-writer queue handles this; if frequent, check WAL pragma settings |
| Consumer tests fail: no response | Server not listening on correct host/port | Verify `HOST=localhost`, `PORT=12345` in test script; run `docker ps` to confirm container is running |

---

## 8. Configuration

### 8.1 Environment Variables

**Server** (`server/app.py`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_PATH` | `/app/data/weather.db` | Path to SQLite database file. Inside Docker, set to the mounted volume. |
| `SERVER_HOST` | `0.0.0.0` (implicit) | Bind address; not configurable in current code. Listens on all interfaces. |
| `SERVER_PORT` | `12345` (implicit) | TCP port; not configurable in current code. |

**Weather Station Client** (`station_client/client.py`):

| Variable | Default | Purpose | CLI Override |
|----------|---------|---------|---|
| `WEATHER_STATION_HOST` | `localhost` | Server host | `--host` |
| `WEATHER_STATION_PORT` | `12345` | Server port | `--port` |
| `WEATHER_STATION_ID` | `STATION-001` | Station identifier | `--station-id` |
| `WEATHER_STATION_BATCH_MIN` | `1` | Minimum batch size (readings) | `--batch-min` |
| `WEATHER_STATION_BATCH_MAX` | `1` | Maximum batch size (readings) | `--batch-max` |
| `WEATHER_STATION_BATCH_INTERVAL` | `5` | Seconds between batches | `--batch-interval` |
| `WEATHER_STATION_TIMEOUT` | `5.0` | Response timeout (seconds) | `--timeout` |
| `WEATHER_STATION_CONNECT_TIMEOUT` | `10.0` | Connection timeout (seconds) | `--connect-timeout` |

**Web UI** (`web/web.py`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEATHER_DB_PATH` | `../data/weather.db` | Path to SQLite database. Inside Docker: `/app/data/weather.db` |
| `WEB_HOST` | `127.0.0.1` | Flask bind address; set to `0.0.0.0` to expose externally |
| `WEB_PORT` | `8000` | Flask port |

### 8.2 Examples

**Example 1: Run a client with custom station ID and batch size**

```bash
# Via Docker (in compose, modify environment section for the station service)
# Or via local Python:
python station_client/client.py --station-id WEATHER-LAB-01 --batch-min 5 --batch-max 10 --batch-interval 2
```

**Example 2: Run web UI on a different port**

```bash
# Locally:
export WEB_PORT=9000  # or set WEB_PORT=9000 on Windows
python web/web.py
# Then visit http://localhost:9000

# Via Docker Compose (modify `docker-compose.yml`):
# web:
#   environment:
#     - WEB_PORT=9000
#   ports:
#     - "9000:9000"
```

---

## 9. Protocol Specification (Producer + Consumer)

### 9.1 Producer Batch (Client → Server)

**Format:** Newline-delimited JSON (NDJSON). Each line is a JSON array of reading objects.

**Message size:** Max 65536 bytes per line.

**Batch size:** 1–50 readings per batch.

**Example batch:**
```json
[
  {
    "station_id": "STATION-001",
    "timestamp": "2025-01-17T14:30:45.123Z",
    "temperature": 22.5,
    "humidity": 58.3,
    "windspeed": 4.2
  },
  {
    "station_id": "STATION-001",
    "timestamp": "2025-01-17T14:30:50.456Z",
    "temperature": 22.6,
    "humidity": 58.1,
    "windspeed": 4.3
  }
]
```

**Field constraints:**
- `station_id`: Non-empty string (e.g., "STATION-001")
- `timestamp`: ISO 8601 format with Z or timezone offset (e.g., "2025-01-17T14:30:45Z", "2025-01-17T14:30:45+00:00")
- `temperature`: Finite number, –10 to 40 °C
- `humidity`: Finite number, 0 to 100 %
- `windspeed`: Finite number, 0 to 50 m/s

**Server response (success):**
```json
{"status": "ok", "inserted": 2}
```

**Server response (error):**
```json
{"status": "error", "reason": "invalid_temperature at index 0 (expected -10..40)"}
```

---

### 9.2 Consumer Request (Client → Server)

**Format:** Newline-delimited JSON. Each line is a JSON object with a "request" key.

**Supported request types:**

#### 9.2.1 Stations
List all unique station IDs in the database.

**Request:**
```json
{"request": "stations"}
```

**Response:**
```json
{"status": "ok", "stations": ["STATION-001", "STATION-002", "STATION-003"]}
```

#### 9.2.2 Latest
Get the newest reading (global or for a specific station).

**Request (all stations):**
```json
{"request": "latest"}
```

**Request (specific station):**
```json
{"request": "latest", "station_id": "STATION-001"}
```

**Response:**
```json
{
  "status": "ok",
  "reading": {
    "station_id": "STATION-001",
    "timestamp": "2025-01-17T14:30:50Z",
    "temperature": 22.6,
    "humidity": 58.1,
    "windspeed": 4.3
  }
}
```

**Response (no data):**
```json
{"status": "ok", "reading": null}
```

#### 9.2.3 Recent
Get the last N readings (global or for a specific station).

**Request (default limit 50):**
```json
{"request": "recent"}
```

**Request (custom limit):**
```json
{"request": "recent", "limit": 10}
```

**Request (specific station):**
```json
{"request": "recent", "station_id": "STATION-001", "limit": 5}
```

**Response:**
```json
{
  "status": "ok",
  "readings": [
    {"station_id": "STATION-001", "timestamp": "2025-01-17T14:30:50Z", "temperature": 22.6, "humidity": 58.1, "windspeed": 4.3},
    {"station_id": "STATION-001", "timestamp": "2025-01-17T14:30:45Z", "temperature": 22.5, "humidity": 58.3, "windspeed": 4.2}
  ]
}
```

#### 9.2.4 Unknown Request
**Request:**
```json
{"request": "typo"}
```

**Response:**
```json
{"status": "error", "reason": "unknown_request:typo"}
```

---

## 10. Project Structure

```
weather-station/
├── README.md                          # This file
├── docker-compose.yml                 # Main Compose (3 stations, normal)
├── docker-compose.stress.yml          # Stress Compose (100 stations, fast)
│
├── server/                            # TCP Server
│   ├── Dockerfile
│   ├── app.py                         # Main server logic (asyncio)
│   ├── protocol.py                    # Validation + constants
│   └── requirements.txt                # Dependencies (aiosqlite)
│
├── station_client/                    # Weather Station Client
│   ├── Dockerfile
│   ├── client.py                      # Client logic (resilience + buffering)
│   └── requirements.txt                # (No external dependencies)
│
├── web/                               # Flask Web UI
│   ├── Dockerfile
│   ├── web.py                         # Flask app + API endpoints
│   ├── requirements.txt                # Dependencies (Flask)
│   ├── templates/
│   │   └── index.html                 # HTML dashboard
│   └── static/
│       ├── app.js                     # JavaScript logic (Chart.js)
│       └── styles.css                 # Dashboard styles
│
├── consumer_client/                   # Consumer Test Harness
│   ├── test_consumer.py               # Protocol tests (producer + consumer)
│   └── test_api.py                    # API sanity check
│
├── scripts/                           # Utilities
│   └── gen_stress_compose.py          # Auto-generates stress Compose
│
├── docs/                              # Documentation
│   ├── architecture-decisions.md      # Detailed architecture & design decisions
│   ├── QUICK_START.md
│   ├── DOCKER.md
│   ├── ARCHITECTURE.md
│   ├── app.md
│   ├── client.md
│   └── web.md
│
└── data/                              # (Local only, non-Docker)
    └── weather.db                     # SQLite database (if running locally)
```

---

## 11. Troubleshooting

### Port Conflicts
- **Error:** `bind: address already in use`
- **Solution:** Stop other services on ports 12345 or 8000, or modify `docker-compose.yml` to use different ports.

### No Stations in Dashboard
- **Error:** Web dashboard shows empty station list.
- **Cause:** Station clients haven't sent data yet, or server isn't running.
- **Solution:** Wait 15–20 seconds after `docker compose up` for health checks and initial batches; check server logs: `docker compose logs server`.

### Database Not Persisting
- **Error:** Data disappears after `docker compose down`.
- **Cause:** Volume not mounted correctly or `DB_PATH` is wrong.
- **Solution:** Verify volume config in compose file; use `docker volume ls` to check if `weather-station_weather-data` exists. To persist intentionally, run `docker compose down` (no `-v` flag).

### "Database is Locked" Errors
- **Error:** Server logs show `database is locked`.
- **Cause:** Multiple writers; SQLite contention (rare, mitigated by single-writer task).
- **Solution:** Normal under stress test. If frequent under normal load, check SQLite PRAGMA settings in `app.py` (WAL mode, busy_timeout).

### Consumer Tests Fail with Connection Refused
- **Error:** `ConnectionRefusedError: [Errno 111] Connection refused`
- **Cause:** Server not running on localhost:12345.
- **Solution:** Start server first: `docker compose up -d` or `python server/app.py`. Verify with `docker ps` or `netstat -an | grep 12345`.

### Web UI Returns 500 Errors
- **Error:** Dashboard displays server error.
- **Cause:** Flask can't read database (wrong `DB_PATH`, no read permission).
- **Solution:** Check web logs: `docker compose logs web`. Verify `WEATHER_DB_PATH` env var and that database file exists.

### Stress Test Hangs or Slows Down
- **Error:** Dashboard becomes unresponsive with 100 stations.
- **Cause:** High CPU/memory; database queries on large dataset slow down UI rendering.
- **Solution:** Reduce station count in `docker-compose.stress.yml` or narrow time range in dashboard (6h instead of 24h).

---

## Additional Resources

- **Architecture Details:** See [docs/architecture-decisions.md](docs/architecture-decisions.md) for protocol, design decisions, and limitations.
- **Docker Setup:** See [docs/DOCKER.md](docs/DOCKER.md).
- **Quick References:** See [docs/QUICK_START.md](docs/QUICK_START.md).

---

**Last Updated:** January 17, 2026  
**Target Audience:** University students (networking, socket programming, Docker, database persistence)  
**Status:** Educational project — production deployment not recommended without additional security/observability.

## 11. Reliability & Recovery
- Server restart: volume preserves DB; on restart server recreates table if missing and resumes writes.
- Client behavior: automatic reconnect with exponential backoff; buffers unsent batches (drops oldest when buffer full) and flushes on reconnection.
- Web UI: stateless; reconnects to DB on each request and tolerates missing DB (returns empty lists/nulls rather than errors).
- Consistency: all-or-nothing batch insertion ensures no partial writes; writer flushes queue on shutdown.

## 12. Configuration Options
- Server env:
	- `DB_PATH` (default `/app/data/weather.db`)
	- `SERVER_HOST` (default `0.0.0.0` in compose)
	- `SERVER_PORT` (default `12345`)
- Station env (read in [station_client/client.py](station_client/client.py)):
	- `WEATHER_STATION_HOST` (default `localhost` / `server` in compose)
	- `WEATHER_STATION_PORT` (default `12345`)
	- `WEATHER_STATION_ID` (default `STATION-001`)
	- `WEATHER_STATION_BATCH_MIN` / `WEATHER_STATION_BATCH_MAX` (defaults 1 / 1; must be positive and min ≤ max)
	- `WEATHER_STATION_BATCH_INTERVAL` (default 5s)
	- `WEATHER_STATION_TIMEOUT` (response timeout, default 5s)
	- `WEATHER_STATION_CONNECT_TIMEOUT` (connect timeout, default 10s)
	- Backoff, jitter, buffer sizes are coded defaults (see client constants) and not environment-driven.
- Web env:
	- `WEATHER_DB_PATH` (default `../data/weather.db` in image; overridden to `/app/data/weather.db` in compose)
	- `WEB_HOST` (default `0.0.0.0` in compose)
	- `WEB_PORT` (default `8000`)

## 13. Troubleshooting
- No stations visible: ensure server healthy, clients connected; check `docker compose logs station-001` for connection errors; confirm DB has data (`docker exec weather-server sqlite3 /app/data/weather.db "SELECT COUNT(*) FROM readings;"`).
- Port conflicts: change published ports in compose (`12345:12345`, `8000:8000`) or stop conflicting services.
- Database locked errors: should be rare due to single-writer + WAL; if seen, verify only one server is writing and volume is not mounted read/write elsewhere.
- Containers restarting: inspect logs for exceptions; confirm volume path writable; check healthcheck failures.
- Web UI stuck on “loading”: verify web can read DB path, DB file exists, and station list query returns rows; reload after data arrives.

## 14. Project Requirements Mapping
- Socket ingestion: asyncio TCP server with newline-delimited JSON on 12345; clients use raw sockets.
- Concurrency: multiple async client handlers, single writer queue to avoid DB contention.
- Validation: schema, ranges, finite numbers, ISO timestamps, batch size, and line size enforced before insert.
- Persistence: SQLite in named volume `weather-data`, WAL mode, survives restarts.
- Variable metrics: temperature, humidity, windspeed carried through protocol, DB schema, and web rendering.
- Dockerized deployment: three Dockerfiles plus compose for normal and stress demos; shared network/volume and health checks.
- Web visualization: Flask + Chart.js dashboard with stats and time series backed by the same DB.

## 15. Limitations & Future Improvements
- No authentication/TLS on sockets or web UI; intended for trusted/demo environments only.
- Single SQLite writer can bottleneck at very high rates; for production scale consider PostgreSQL and async drivers.
- Metrics are fixed to three fields; extensible schema would require migration and UI changes.
- Stress UI performance may degrade with very large point counts; consider server-side aggregation or pagination.

## 16. How to Clean Up
- Stop services:
	```bash
	docker compose down
	```
- Remove containers and data volume (irreversible):
	```bash
	docker compose down -v
	```
- To reset stress run artifacts: same `down -v` after using `-f docker-compose.stress.yml`.
