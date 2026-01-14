
# Weather Station Simulator (Dockerized)

## 1. Project Overview
- TCP weather station simulator that sends JSON batches over sockets to a central server.
- Goals: demonstrate socket-based ingestion, strict validation, durable storage, and a minimal web view packaged in Docker.
- Key features: asyncio TCP server with backpressure, concurrent clients, newline-delimited JSON batches (up to 50 records), SQLite persistence, Docker Compose orchestration, and a Flask dashboard that adapts to the stored metrics.

## 2. System Architecture
- **TCP Server** ([server/app.py](server/app.py)): asyncio listener on port 12345; validates batches and enqueues writes to SQLite through a single writer task.
- **Weather Station Clients** ([station_client/client.py](station_client/client.py)): autonomous generators that create deterministic-but-noisy temperature, humidity, and windspeed readings and push newline-delimited JSON batches over TCP with retry/backoff.
- **Web Dashboard** ([web/web.py](web/web.py)): Flask app exposing `/api/stations`, `/api/stats`, `/api/readings` plus HTML/JS dashboard on port 8000 reading the same SQLite DB (read-only mount).
- **Optional Consumer Client** ([consumer_client/consumer.py](consumer_client/consumer.py)): placeholder for downstream consumers; not wired by default.
- **Data flow**: Stations → TCP server (validate) → enqueue → SQLite volume → Web dashboard queries and renders. All services share Docker bridge network `weather-network`; server exposes 12345, web exposes 8000 to host.

## 3. Data Protocol
- Transport: plain TCP with newline-delimited JSON; one line = one batch.
- Batch format: JSON array of records (max 50). Each record:
	```json
	{
		"station_id": "STATION-001",
		"timestamp": "2025-01-01T12:30:45Z",  // ISO 8601, Z or offset
		"temperature": 21.5,                   // -10..40, finite
		"humidity": 55.2,                      // 0..100, finite
		"windspeed": 5.8                       // 0..50, finite
	}
	```
- Variable metrics: the three metrics above are mandatory per record; stations may vary batch size and send at different intervals.
- Server response: JSON per batch. Success: `{"status":"ok","inserted":<count>}`. Failure: `{"status":"error","reason":"<message>"}`; nothing is inserted on failure.
- All-or-nothing: validation rejects the first error in the batch; no partial inserts, keeping DB consistent.

## 4. Validation & Error Handling
- Validations (in [server/protocol.py](server/protocol.py)): batch must be list, size ≤ 50, each item dict with required fields, ISO 8601 timestamps, finite numbers, value ranges for temperature/humidity/windspeed, and newline size capped at 65536 bytes.
- Safe JSON parse rejects NaN/Infinity (`_safe_json_loads`). Oversized line triggers `line_too_long` and the connection closes.
- On invalid data: server replies with `status:error` and reason; batch is discarded.
- Network issues: client catches timeouts, connection resets, and broken pipes; failed batches are buffered (up to 1000 readings) and retried with exponential backoff + jitter.
- Timeouts: client connect timeout 10s (default), response timeout 5s, writer drain timeout 5s; server read limit prevents unbounded buffering.

## 5. Concurrency Model
- Server uses asyncio with one handler per connection plus a dedicated single writer task.
- Enqueued batches flow through an asyncio `Queue`; the writer performs `executemany` transactions sequentially to avoid SQLite lock contention.
- This model prevents race conditions on the database while still allowing many concurrent socket clients.

## 6. Persistence Layer
- SQLite chosen for simplicity and zero external dependencies; file path defaults to `/app/data/weather.db` (configurable via `DB_PATH`).
- Stored in Docker named volume `weather-data`, mounted read-only to the web container and read/write to the server.
- SQLite pragmas set: WAL mode, `synchronous=NORMAL`, `busy_timeout=3000ms` to reduce locks under load.
- Persistence verified by keeping the volume across restarts (`docker compose down` leaves volume; use `down -v` to remove).

## 7. Docker & Containerization
- Dockerfiles: server ([server/Dockerfile](server/Dockerfile)) installs `aiosqlite`; client ([station_client/Dockerfile](station_client/Dockerfile)) is dependency-free; web ([web/Dockerfile](web/Dockerfile)) installs Flask.
- Docker Compose ([docker-compose.yml](docker-compose.yml)): brings up server, three stations, and web UI; shares `weather-data` volume and `weather-network` bridge; server health check ensures stations wait for readiness.
- Stress Compose ([docker-compose.stress.yml](docker-compose.stress.yml)): auto-generated, scaled to many stations (100 by default) with faster intervals.
- Health: server healthcheck opens TCP 12345; `depends_on:condition:service_healthy` gates stations in both compose files.

## 8. Web Dashboard
- Purpose: minimal read-only view of recent data with stats (latest/avg/min/max) and chart/table for a selected station/metric/time window.
- Data shown: station list, per-metric stats, time series for last 6h or 24h (configurable ranges in code: 6h/24h).
- Variable metrics: handles temperature, humidity, windspeed; auto-detects DB wind column name (`windspeed` vs `wind_speed`).
- Performance: uses lightweight queries and server-side filtering; Chart.js renders client-side; for very large datasets, prefer narrower time windows or table fallback.

## 9. Normal Demo (Functional Demo)
1. Build and start all services:
	 ```bash
	 docker compose up --build
	 ```
2. Services started: TCP server (12345), three station containers emitting data, web UI (8000).
3. Open http://localhost:8000 to see stations, stats, and charts. Logs show batch inserts and API calls.
4. This proves end-to-end requirements: socket ingestion → validation → SQLite persistence → web visualization over shared volume.

## 10. Stress Demo (Load Testing)
- Purpose: observe behavior under many clients and higher write rates.
- Configuration: 1 server, 1 web, and 100 stations (IDs STRESS-001..100) sending every 1s with batch size 1–5.
- Run:
	```bash
	docker compose -f docker-compose.stress.yml up --build
	```
- Success criteria: server stays healthy, no `database is locked` errors, web continues to read DB, and container resources remain within limits (`docker stats`).
- Observations: single-writer queue plus SQLite WAL avoids lock thrash; main pressure points are server CPU and web charting with many points. Scale ranges down or reduce station count if UI lags.

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
