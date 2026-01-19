# docker-compose.yml — Explanation

**Related docs:** [00_overview.md](00_overview.md), [docker_compose_stress_yml.md](docker_compose_stress_yml.md), [dockerfile_server.md](dockerfile_server.md), [dockerfile_station_client.md](dockerfile_station_client.md), [dockerfile_web.md](dockerfile_web.md)

## Purpose

The Docker Compose configuration orchestrates four services: a TCP server (stores data), three weather station clients (send data), and a Flask web UI (visualizes data). It manages networking, persistent storage, environment variables, health checks, and service dependencies. This is the standard/production setup for the weather station system; use `docker-compose.stress.yml` for load testing.

## Key Responsibilities

- Define four container services (server, station-001/002/003, web)
- Create named volume for SQLite database persistence
- Create bridge network for inter-service communication
- Set environment variables for each service
- Configure health checks for dependency ordering
- Map ports (12345 for TCP server, 8000 for web)
- Define startup dependencies (`depends_on` conditions)
- Mount volumes (database shared between server and web)

## Important Blocks

### Block: Version & Overall Structure

**Where:** First line `version: '3.8'`

**What it does:**
- Specifies Docker Compose API version 3.8 (supports depends_on conditions, health checks, etc.)
- Modern version; compatible with Docker Engine 18.06+

```yaml
version: '3.8'

services:
  # Service definitions here
  ...

volumes:
  # Named volumes here
  ...

networks:
  # Network definitions here
  ...
```

**Inputs:** Docker Compose CLI

**Outputs:** Parsed configuration by Docker Compose runtime

**Why it matters:**
- Version 3.8 is stable and supports all features used
- Version 2 would not support `healthcheck`; version 3.0-3.7 do not support `condition: service_healthy`
- Ensures predictable behavior across different Docker installations

**Failure modes:** None

### Block: TCP Server Service

**Where:** `services.server { ... }`

**What it does:**
1. Builds from `./server/Dockerfile`
2. Names container `weather-server`
3. Exposes port 12345 (TCP server listen port)
4. Mounts `weather-data` volume at `/app/data` (SQLite database directory)
5. Sets environment variables:
   - `SERVER_HOST=0.0.0.0` (listen on all interfaces)
   - `SERVER_PORT=12345` (TCP listen port)
   - `DB_PATH=/app/data/weather.db` (SQLite file path)
6. Restart policy: `unless-stopped` (restart if crashes, but not if manually stopped)
7. Connects to `weather-network`
8. Health check: TCP socket connection to port 12345

```yaml
server:
  build:
    context: ./server
    dockerfile: Dockerfile
  container_name: weather-server
  ports:
    - "12345:12345"
  volumes:
    - weather-data:/app/data
  environment:
    - SERVER_HOST=0.0.0.0
    - SERVER_PORT=12345
    - DB_PATH=/app/data/weather.db
  restart: unless-stopped
  networks:
    - weather-network
  healthcheck:
    test: ["CMD", "python", "-c", "import socket; s=socket.socket(); s.connect(('localhost',12345)); s.close()"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 10s
```

**Inputs:** Dockerfile, entrypoint code (app.py, protocol.py), network

**Outputs:** Running TCP server accepting connections on 12345

**Why it matters:**
- Port 12345 exposed so station clients (inside and outside Docker) can connect
- Volume `weather-data` persists SQLite database across container restarts
- Health check polls TCP port to verify server is ready; `start_period: 10s` gives server time to start before health checks begin
- `depends_on: condition: service_healthy` in client services waits for this health check to pass

**Failure modes:**
- Port 12345 in use → container fails to start
- Volume missing → container can create it, but data lost on container removal
- Health check timeout → clients remain in "waiting" state
- Disk full at `/app/data` → database write fails

### Block: Station Client Services (1-3)

**Where:** `services.station-001 { ... }`, `services.station-002 { ... }`, `services.station-003 { ... }`

**What it does:**
Each of 3 identical services:
1. Builds from `./station_client/Dockerfile`
2. Named containers: `weather-station-001/002/003`
3. No exposed ports (clients don't listen; only connect outbound)
4. No volumes (clients don't persist state)
5. Sets environment variables:
   - `WEATHER_STATION_HOST=server` (DNS name of server container)
   - `WEATHER_STATION_PORT=12345` (server TCP port)
   - `WEATHER_STATION_ID=STATION-001` (unique identifier for readings)
   - `WEATHER_STATION_BATCH_MIN=1`, `WEATHER_STATION_BATCH_MAX=1` (batch size: 1 reading per batch)
   - `WEATHER_STATION_BATCH_INTERVAL=5` (send batch every 5 seconds)
   - `WEATHER_STATION_TIMEOUT=5.0` (connection timeout)
   - `WEATHER_STATION_MAX_RETRIES=3` (exponential backoff up to 3 retries)
6. Restart: `unless-stopped`
7. `depends_on: server: condition: service_healthy` (wait for server health check)
8. Network: `weather-network` (to reach `server` hostname)

```yaml
station-001:
  build:
    context: ./station_client
    dockerfile: Dockerfile
  container_name: weather-station-001
  environment:
    - WEATHER_STATION_HOST=server
    - WEATHER_STATION_PORT=12345
    - WEATHER_STATION_ID=STATION-001
    - WEATHER_STATION_BATCH_MIN=1
    - WEATHER_STATION_BATCH_MAX=1
    - WEATHER_STATION_BATCH_INTERVAL=5
    - WEATHER_STATION_TIMEOUT=5.0
    - WEATHER_STATION_MAX_RETRIES=3
  depends_on:
    server:
      condition: service_healthy
  restart: unless-stopped
  networks:
    - weather-network

# station-002 and station-003 are identical except ID
```

**Inputs:** Dockerfile, entrypoint code (client.py), network

**Outputs:** Running client processes connecting to server and sending batches

**Why it matters:**
- `WEATHER_STATION_HOST=server` uses Docker DNS to resolve container name (requires bridge network)
- `depends_on: condition: service_healthy` ensures server is ready before clients attempt connection
- 3 separate services generate concurrent traffic; tests multiplexing
- Each has unique ID (STATION-001/002/003) for grouping readings

**Failure modes:**
- `server` hostname not resolvable → client fails to connect (stays in retry loop)
- Server port 12345 closed → client connection refused (backoff + retry)
- Network misconfiguration → DNS fails
- Container removed while running → `restart: unless-stopped` would restart if crash, but removal is explicit

### Block: Web UI Service

**Where:** `services.web { ... }`

**What it does:**
1. Builds from `./web/Dockerfile`
2. Named container: `weather-web`
3. Exposes port 8000 (Flask listen port)
4. Mounts `weather-data` volume at `/app/data` **read-only** (`:ro` flag)
5. Environment variables:
   - `WEATHER_DB_PATH=/app/data/weather.db` (SQLite file path)
   - `WEB_HOST=0.0.0.0` (listen on all interfaces)
   - `WEB_PORT=8000` (Flask port)
6. Restart: `unless-stopped`
7. `depends_on: server` (waits for service, not health check; just ordering)
8. Network: `weather-network`

```yaml
web:
  build:
    context: ./web
    dockerfile: Dockerfile
  container_name: weather-web
  ports:
    - "8000:8000"
  volumes:
    - weather-data:/app/data:ro
  environment:
    - WEATHER_DB_PATH=/app/data/weather.db
    - WEB_HOST=0.0.0.0
    - WEB_PORT=8000
  depends_on:
    - server
  restart: unless-stopped
  networks:
    - weather-network
```

**Inputs:** Dockerfile, Flask code (web.py, templates/, static/), network, volume

**Outputs:** Running Flask web UI on port 8000

**Why it matters:**
- Port 8000 exposed so users can access UI via `http://localhost:8000`
- Volume mounted read-only (`:ro`) so web can read database but not write (prevent race conditions with writer task)
- `depends_on: server` ensures server starts first (Docker Compose will start `server` before `web`)
- Shared volume `weather-data` allows web to read latest data from server

**Failure modes:**
- Port 8000 in use → web fails to start
- Database file not found → Flask 500 error when querying
- Database locked by server writer → web request times out (but read-only access doesn't create locks)

### Block: Persistent Volume

**Where:** `volumes.weather-data { ... }`

**What it does:**
1. Defines named volume `weather-data` with local driver
2. Stored on host machine at Docker's data directory
3. Mounted at `/app/data` in server and web services
4. Data persists across container restarts/removals
5. Shared between server (read+write) and web (read-only)

```yaml
volumes:
  weather-data:
    driver: local
```

**Inputs:** Docker volume engine

**Outputs:** Named volume managed by Docker

**Why it matters:**
- Named volumes are more portable than bind mounts (host-dependent paths)
- Persistent storage: SQLite database survives `docker compose down` (only removed with `--volumes` flag)
- Shared between services: server writes, web reads from same file

**Failure modes:**
- Host disk full → write fails
- Volume accidentally deleted → data lost (`docker volume prune`)
- Permissions issue on host → container cannot access volume

### Block: Bridge Network

**Where:** `networks.weather-network { ... }`

**What it does:**
1. Defines bridge network named `weather-network`
2. Services connected to this network can resolve each other by container name
3. Bridge networks isolate traffic (default network is global)
4. `server` is resolvable as hostname from station clients

```yaml
networks:
  weather-network:
    driver: bridge
```

**Inputs:** Docker network driver

**Outputs:** Isolated bridge network for service communication

**Why it matters:**
- `WEATHER_STATION_HOST=server` resolves to server container's IP via Docker DNS
- Bridge network isolates container traffic from host (security)
- Multiple projects can run without hostname collisions

**Failure modes:**
- Network deleted → containers cannot reach each other
- Service not connected to network → DNS resolution fails

## Data & State

### Environment Variables

#### Server

| Variable | Default | Purpose |
|----------|---------|---------|
| `SERVER_HOST` | `0.0.0.0` | Listen on all interfaces (inside container) |
| `SERVER_PORT` | `12345` | TCP port for clients |
| `DB_PATH` | `/app/data/weather.db` | SQLite database file path |

#### Station Clients (001-003)

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEATHER_STATION_HOST` | `server` | Hostname (Docker DNS resolves to server container) |
| `WEATHER_STATION_PORT` | `12345` | Server TCP port |
| `WEATHER_STATION_ID` | `STATION-001` | Unique identifier for this client |
| `WEATHER_STATION_BATCH_MIN` | `1` | Min readings per batch |
| `WEATHER_STATION_BATCH_MAX` | `1` | Max readings per batch (1 = 1 reading/batch) |
| `WEATHER_STATION_BATCH_INTERVAL` | `5` | Seconds between batch sends |
| `WEATHER_STATION_TIMEOUT` | `5.0` | Connection timeout (seconds) |
| `WEATHER_STATION_MAX_RETRIES` | `3` | Max exponential backoff attempts |

#### Web UI

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEATHER_DB_PATH` | `/app/data/weather.db` | SQLite database file path |
| `WEB_HOST` | `0.0.0.0` | Listen on all interfaces |
| `WEB_PORT` | `8000` | Flask HTTP port |

### Service Restart Policies

- **server, station-001/002/003, web:** `unless-stopped`
  - Restarts automatically if container exits abnormally
  - Stays stopped if manually stopped (`docker stop`)
  - Never restarts if manually removed

### Port Mappings

| Container | Port | Mapping | Access |
|-----------|------|---------|--------|
| server | 12345 | `"12345:12345"` | Externally accessible on host:12345 |
| web | 8000 | `"8000:8000"` | Externally accessible on host:8000 |
| station clients | N/A | None | Internal to network only |

## Control Flow Walkthrough

### Startup Sequence

```
1. $ docker compose up --build

2. Docker Compose parses docker-compose.yml

3. Builds images:
   - server/Dockerfile → weather-server image
   - station_client/Dockerfile → weather-station image (reused for 3 services)
   - web/Dockerfile → weather-web image

4. Creates named volume weather-data

5. Creates bridge network weather-network

6. Starts services:
   
   a) Start server service
      - Create container weather-server
      - Mount volume weather-data at /app/data
      - Expose port 12345
      - Run app.py (asyncio TCP server)
      - Health check: attempt TCP connect to localhost:12345 every 30s
      - Health check fails initially (server starting)
      - After ~5 sec, health check passes → server is healthy
      - start_period: 10s prevents premature failure

   b) Start station-001, station-002, station-003
      - Each waits for depends_on: server: condition: service_healthy
      - Once server is healthy, start each station
      - client.py connects to 'server' (DNS resolves)
      - Enters batch send loop (5-second interval)
      - Reads sent to server → server inserts into DB

   c) Start web service
      - Depends on: server (just ordering, no health check wait)
      - Mount volume weather-data read-only
      - Run web.py (Flask HTTP server)
      - Listen on 0.0.0.0:8000

7. All 5 containers running, network connected
   - Server receiving batches from 3 clients
   - Web serving dashboard at http://localhost:8000
   - Data persists in weather-data volume

8. $ docker compose down
   - Stop all containers
   - Remove containers, network
   - Keep volume (data survives)

9. $ docker compose down --volumes
   - Stop all containers
   - Remove containers, network, volume
   - Data deleted
```

### Health Check Flow (server)

```
Container started → start_period: 10s (delay health checks)

After 10s:
  Health check #1 (30s interval)
    → python -c "import socket; connect to localhost:12345"
    → Server not ready → FAIL
    
  Health check #2 (after 30s)
    → Server ready → PASS
    
  Station clients see: server: condition: service_healthy
    → Condition met, start station clients
```

### Data Flow During Runtime

```
station-001 (every 5s):
  1. Generate random reading (temp, humidity, windspeed)
  2. Batch 1 reading
  3. Send NDJSON array to server:12345
  4. server app.py receives → db_writer_task() queues for DB
  5. SQLite inserts into readings table (weather-data volume)

web UI (user clicks "Refresh"):
  1. Fetch /api/stats from web:8000
  2. web.py opens DB connection (weather-data volume)
  3. Query readings table
  4. Return JSON (latest values, counts, etc.)
  5. JavaScript renders chart

Persistence:
  - SQLite file in weather-data volume
  - Survives container stop/restart
  - Both server and web access same file
```

## Interfaces

### Docker Compose Commands

| Command | Purpose |
|---------|---------|
| `docker compose up` | Start services in foreground |
| `docker compose up -d` | Start services in background |
| `docker compose up --build` | Rebuild images before starting |
| `docker compose down` | Stop and remove containers |
| `docker compose down -v` | Stop, remove containers, and delete volumes |
| `docker compose ps` | List running containers |
| `docker compose logs server` | View server logs |
| `docker compose logs -f web` | View web logs, follow (tail -f) |
| `docker compose exec server bash` | Open shell in server container |

### Port Mappings from Host

| URL | Service | Purpose |
|-----|---------|---------|
| `http://localhost:8000` | web | Dashboard UI |
| `localhost:12345` (TCP) | server | Station client connections |

### Volume Persistence

- **weather-data:** Contains `weather.db` (SQLite file)
  - Created on first `up`
  - Persists across stop/restart
  - Deleted only with `docker compose down -v`

## Observability & Debugging

### Common Commands

```bash
# View all services status
docker compose ps

# Follow server logs in real-time
docker compose logs -f server

# Follow all logs
docker compose logs -f

# Check specific service health
docker compose ps server

# Inspect volume contents
docker volume inspect weather-data

# Open shell in running container
docker compose exec server bash

# Rebuild specific service
docker compose build --no-cache server
```

### Health Check Debugging

```bash
# Check server health status
docker compose ps server
# Column "STATUS" shows "(healthy)", "(starting)", or "(unhealthy)"

# View healthcheck details in inspect
docker inspect weather-server | grep -A 5 "Health"

# Manual health test
docker compose exec server python -c "import socket; s=socket.socket(); s.connect(('localhost',12345)); s.close(); print('OK')"
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "Port 12345 already in use" | Another service using port | `docker compose down` or change port mapping |
| "server: condition: service_healthy never passes" | Server crashes or port unreachable | `docker compose logs server` to see errors |
| "Cannot resolve 'server'" | Station client network misconfiguration | Verify `networks: [weather-network]` in compose |
| "Permission denied" on volume | Docker daemon permission issue | Run with sudo or add user to docker group |
| "database is locked" | Multiple writers on SQLite | (Should not happen; server has single writer task) |

### Database Inspection

```bash
# Open SQLite CLI in server container
docker compose exec server sqlite3 /app/data/weather.db

# Inside sqlite3:
sqlite> .tables
sqlite> SELECT COUNT(*) FROM readings;
sqlite> SELECT * FROM readings LIMIT 5;
sqlite> .quit
```

---

**Key Takeaway:** Docker Compose orchestrates 4 services (1 server, 3 clients, 1 web UI) with persistent storage, networking, health checks, and dependencies. The configuration is straightforward: services communicate via bridge network, data persists in named volume, and startup order is managed by `depends_on` conditions. For load testing, use `docker-compose.stress.yml` with 100 stations.
