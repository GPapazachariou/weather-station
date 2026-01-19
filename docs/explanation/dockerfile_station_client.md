# station_client/Dockerfile — Explanation

**Related docs:** [00_overview.md](00_overview.md), [docker_compose_yml.md](docker_compose_yml.md), [docker_compose_stress_yml.md](docker_compose_stress_yml.md), [client_py.md](client_py.md)

## Purpose

Builds a Docker image for weather station client. The image contains Python 3.11 and client source code (client.py). No external dependencies are needed (standard library only). When run, it connects to the weather server and continuously sends weather data batches.

## Key Responsibilities

- Use lightweight base image (python:3.11-slim)
- Copy client source code (client.py)
- Set default environment variables
- Define entrypoint (python client.py)

## Important Blocks

### Block: Base Image Selection

**Where:** Line 1 `FROM python:3.11-slim`

**What it does:**
1. Pulls official Python 3.11 slim image
2. Lightweight variant (no build tools, docs, test suites)
3. Size: ~130 MB
4. Sufficient for client.py (only uses standard library)

```dockerfile
FROM python:3.11-slim
```

**Inputs:** Docker Hub official Python images

**Outputs:** Base OS + Python 3.11 runtime

**Why it matters:**
- Python 3.11 required for `asyncio` syntax
- No external packages needed (client.py uses only socket, json, time, random, asyncio)
- Slim variant reduces image size (no bloat)

**Failure modes:** None

### Block: Working Directory & Source Copy

**Where:** Lines 3-5

**What it does:**
1. Set working directory to `/app`
2. Copy client.py from build context to container

```dockerfile
WORKDIR /app

COPY client.py .
```

**Inputs:** `client.py` from station_client/ directory

**Outputs:** Source file in `/app/client.py`

**Why it matters:**
- Minimal copying (only 1 file)
- WORKDIR simplifies relative paths

**Failure modes:**
- client.py missing → build fails

### Block: Default Environment Variables

**Where:** Lines 7-14

**What it does:**
1. Set defaults for all environment variables used by client.py
2. Values can be overridden by docker-compose.yml or runtime flags
3. Ensures client has sensible defaults if not specified

```dockerfile
ENV WEATHER_STATION_HOST=server
ENV WEATHER_STATION_PORT=12345
ENV WEATHER_STATION_ID=STATION-001
ENV WEATHER_STATION_BATCH_MIN=1
ENV WEATHER_STATION_BATCH_MAX=1
ENV WEATHER_STATION_BATCH_INTERVAL=5
ENV WEATHER_STATION_TIMEOUT=5.0
ENV WEATHER_STATION_MAX_RETRIES=3
```

**Inputs:** None (static defaults)

**Outputs:** Environment variables available to client.py

**Why it matters:**
- Allows quick testing: `docker run weather-client` works without specifying env vars
- docker-compose.yml overrides with specific values per service (e.g., STATION-001/002/003)
- Stress test overrides with faster parameters (BATCH_INTERVAL=1, BATCH_MAX=5)

**Failure modes:** None

### Block: Entrypoint Command

**Where:** Line 17

**What it does:**
1. Define default command to run when container starts
2. Executes: `python client.py`
3. client.py reads environment variables and connects to server

```dockerfile
CMD ["python", "client.py"]
```

**Inputs:** client.py (copied earlier), environment variables

**Outputs:** Running client process; connects to server

**Why it matters:**
- Automatic startup; no manual invocation needed
- client.py handles connection logic (retries, backoff, etc.)

**Failure modes:**
- Server unreachable → client loops with exponential backoff
- Invalid env vars → client.py may exit (or use defaults)

## Data & State

### Environment Variables (Defaults in Dockerfile)

| Variable | Default Value | Purpose |
|----------|---------------|---------|
| `WEATHER_STATION_HOST` | `server` | Server hostname (Docker DNS resolves to container name) |
| `WEATHER_STATION_PORT` | `12345` | Server TCP port |
| `WEATHER_STATION_ID` | `STATION-001` | Unique identifier for this client |
| `WEATHER_STATION_BATCH_MIN` | `1` | Min readings per batch |
| `WEATHER_STATION_BATCH_MAX` | `1` | Max readings per batch |
| `WEATHER_STATION_BATCH_INTERVAL` | `5` | Seconds between batch sends |
| `WEATHER_STATION_TIMEOUT` | `5.0` | Connection timeout (seconds) |
| `WEATHER_STATION_MAX_RETRIES` | `3` | Max exponential backoff attempts |

### Docker Compose Overrides

**Normal mode** (docker-compose.yml):
- STATION-001/002/003 use defaults (BATCH_INTERVAL=5)

**Stress mode** (docker-compose.stress.yml):
- STRESS-001 to STRESS-100 override:
  - BATCH_INTERVAL=1 (5x faster)
  - BATCH_MAX=5 (variable batches)
  - WEATHER_STATION_ID=STRESS-001 through STRESS-100

### File Structure Inside Container

```
/app/
  client.py       (main client code)
```

### No External Dependencies

- No requirements.txt (standard library only)
- Imports: socket, json, time, random, asyncio (all built-in to Python)

## Control Flow Walkthrough

### Build Time

```
1. $ docker build -f station_client/Dockerfile -t weather-client .

2. Docker daemon processes:

   FROM python:3.11-slim
   → Pull base image

   WORKDIR /app
   → Set working directory

   COPY client.py .
   → Copy client.py (minimal image)

   ENV WEATHER_STATION_HOST=server
   ENV WEATHER_STATION_PORT=12345
   ... (7 more ENV lines)
   → Set defaults (metadata only, no layer)

   CMD ["python", "client.py"]
   → Set entrypoint (metadata, no layer)

3. Image created and tagged as "weather-client"

4. Image size: ~130 MB (Python 3.11-slim + 1 file)
```

### Runtime (Normal Mode)

```
$ docker compose up

1. Docker Compose starts 3 station client containers:
   - station-001 with env: WEATHER_STATION_ID=STATION-001
   - station-002 with env: WEATHER_STATION_ID=STATION-002
   - station-003 with env: WEATHER_STATION_ID=STATION-003
   
2. Each container executes: python client.py

3. client.py runs (simplified):

   a) load_config() reads env vars
   b) Attempts connect to 'server:12345'
   c) On success:
      - Loop: generate reading
      - Batch 1 reading every 5 seconds
      - Send NDJSON array to server
   d) On connection failure:
      - Exponential backoff: 1s → 2s → 4s → ... → 60s
      - Buffer reading in local deque (max 1000)
      - Retry after backoff

4. Containers run continuously until:
   - Manual stop (docker stop)
   - Host shutdown
   - Fatal error (rare)
```

### Runtime (Stress Mode)

```
$ docker compose -f docker-compose.stress.yml up

1. Docker Compose starts 100 station client containers:
   - station-stress-001 to station-stress-100
   - Each with BATCH_INTERVAL=1, BATCH_MAX=5

2. Each container executes: python client.py

3. client.py runs with stress parameters:
   - 100 concurrent clients
   - Each sends every 1 second (vs. 5)
   - Each batch has 1-5 readings (vs. fixed 1)
   - Server handles ~300 readings/sec (vs. 0.6)

4. System under load test
```

## Interfaces

### Build Arguments

None (no ARG directives)

### Exposed Ports

None (client doesn't listen; only connects outbound)

### Volumes

None (client doesn't persist state)

### Environment Variables (Configurable at Runtime)

All 8 env vars can be overridden in docker-compose.yml or via `docker run -e VAR=value`:

```bash
docker run -e WEATHER_STATION_HOST=192.168.1.100 \
           -e WEATHER_STATION_PORT=12345 \
           -e WEATHER_STATION_ID=MY-STATION \
           weather-client
```

## Observability & Debugging

### Build Debugging

```bash
# Build with verbose output
docker build -f station_client/Dockerfile -t weather-client . --progress=plain

# Build without cache
docker build -f station_client/Dockerfile -t weather-client . --no-cache

# View layers
docker history weather-client
```

### Runtime Debugging

```bash
# Run container interactively with bash
docker run -it weather-client bash

# Inside container, test imports
python -c "import socket, json, asyncio; print('OK')"

# Run client with custom env var
docker run -e WEATHER_STATION_ID=DEBUG-STATION weather-client

# View container logs
docker logs weather-client

# Follow logs in real-time
docker logs -f weather-client
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "Connection refused" | Server not running or wrong port | Check server is listening; verify PORT |
| "Cannot resolve 'server'" | Network isolation or DNS issue | Ensure container on same docker network |
| "Timeout" | Server unresponsive or network slow | Check server logs; increase TIMEOUT |
| Endless backoff loop | Server permanently unreachable | Verify server HOST/PORT; check network |

### Inspect Image

```bash
# View image metadata
docker inspect weather-client

# View environment defaults
docker inspect weather-client | grep -A 10 '"Env"'

# View entrypoint
docker inspect weather-client | grep -A 2 '"Cmd"'
```

### Manual Connection Test

```bash
# Start container with bash
docker run -it weather-client bash

# Inside container, test TCP connection to server
python -c "
import socket
s = socket.socket()
s.connect(('server', 12345))
print('Connected!')
s.close()
"
```

---

**Key Takeaway:** The client Dockerfile is extremely minimal: Python 3.11-slim base, copy client.py (single file), set 8 default environment variables, run the client. No external dependencies, no build args. Image size: ~130 MB. All configuration is via environment variables (set in docker-compose.yml at runtime), making it flexible for normal mode (3 clients) and stress mode (100 clients).
