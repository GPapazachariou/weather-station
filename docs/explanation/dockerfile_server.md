# server/Dockerfile — Explanation

**Related docs:** [00_overview.md](00_overview.md), [docker_compose_yml.md](docker_compose_yml.md), [app_py.md](app_py.md), [protocol_py.md](protocol_py.md)

## Purpose

Builds a Docker image for the TCP weather server. The image contains Python 3.11, required dependencies (aiosqlite, asyncio), and server source code (app.py, protocol.py). When run, it starts an asyncio TCP server listening on port 12345 for client connections and consumer requests.

## Key Responsibilities

- Use lightweight base image (python:3.11-slim)
- Install dependencies from requirements.txt (aiosqlite)
- Copy server source code (app.py, protocol.py)
- Create data directory for SQLite database
- Expose TCP port 12345
- Define entrypoint (python app.py)

## Important Blocks

### Block: Base Image Selection

**Where:** Line 1 `FROM python:3.11-slim`

**What it does:**
1. Pulls official Python 3.11 slim image
2. Slim variant excludes build tools, docs, test suites
3. Size: ~130 MB (vs. ~900 MB for full python:3.11)
4. Still includes pip for package installation

```dockerfile
FROM python:3.11-slim
```

**Inputs:** Docker Hub official Python images

**Outputs:** Base OS + Python 3.11 runtime

**Why it matters:**
- 3.11 is required by asyncio syntax used in app.py (match requirements)
- Slim variant reduces image size and attack surface
- Official image is well-maintained and audited

**Failure modes:** None (standard practice)

### Block: Working Directory & Dependencies

**Where:** Lines 3-6

**What it does:**
1. Set working directory to `/app` (all subsequent paths relative)
2. Copy `requirements.txt` from build context to container
3. Install pip packages with `--no-cache-dir` flag (reduces layer size)

```dockerfile
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

**Inputs:** `requirements.txt` (from server/ directory during build)

**Outputs:** Installed packages (aiosqlite, asyncio)

**Why it matters:**
- `WORKDIR` simplifies all subsequent COPY/RUN commands
- `--no-cache-dir` avoids storing pip cache in image (~20 MB saved)
- Separates dependency layer from code layer (caching: if requirements unchanged, layer reused)

**Failure modes:**
- requirements.txt missing → build fails with "COPY failed: file not found"
- Package not found on PyPI → `pip install` fails
- Version conflict → import fails at runtime

### Block: Source Code Copy

**Where:** Lines 8-9

**What it does:**
1. Copy app.py (main server code) from build context to container
2. Copy protocol.py (validation logic) from build context to container

```dockerfile
COPY app.py .
COPY protocol.py .
```

**Inputs:** `app.py`, `protocol.py` from server/ directory

**Outputs:** Source files in `/app/` inside container

**Why it matters:**
- Both files required for app.py to run (`import protocol` at top)
- Copying separately allows layer caching (if only app.py changes, protocol layer is reused)

**Failure modes:**
- Files missing → build fails
- protocol.py not copied → import error at runtime

### Block: Data Directory Creation

**Where:** Line 11

**What it does:**
1. Create `/app/data` directory inside container
2. Directory will hold SQLite database file (weather.db)
3. Run at build time (not runtime)

```dockerfile
RUN mkdir -p /app/data
```

**Inputs:** None

**Outputs:** `/app/data/` directory (owned by root user)

**Why it matters:**
- SQLite database path: `/app/data/weather.db` (set via DB_PATH env var)
- Directory exists before container starts; avoids "file not found" errors
- `-p` flag means "create parents if needed" (safe if directory already exists)

**Failure modes:**
- Permissions issue → container cannot write to directory (unlikely; running as root)

### Block: Port Exposure

**Where:** Line 14

**What it does:**
1. Document that container listens on TCP port 12345
2. Does NOT actually publish/map port (that's docker-compose.yml responsibility)
3. Informs users and image consumers which ports are used

```dockerfile
EXPOSE 12345
```

**Inputs:** None

**Outputs:** Metadata in image (accessible via `docker inspect`)

**Why it matters:**
- EXPOSE is documentation + default for port mapping
- Docker Compose ports section actually exposes it to host
- Helps with auto-discovery of exposed services

**Failure modes:** None (informational only)

### Block: Entrypoint Command

**Where:** Line 17

**What it does:**
1. Define default command to run when container starts
2. Executes: `python app.py`
3. Runs in foreground (doesn't daemonize); Docker captures stdout/stderr

```dockerfile
CMD ["python", "app.py"]
```

**Inputs:** app.py (copied earlier)

**Outputs:** Running TCP server process

**Why it matters:**
- Container runs the server automatically
- Can be overridden with `docker run ... myimage python -c "..."` or similar
- Foreground execution allows Docker to monitor container health

**Failure modes:**
- app.py imports fail → container exits immediately
- Port 12345 in use → server binds fail, container exits

## Data & State

### Environment Variables (Set at Runtime)

These are set in docker-compose.yml, not in Dockerfile:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SERVER_HOST` | `0.0.0.0` | Listen on all interfaces |
| `SERVER_PORT` | `12345` | TCP listen port |
| `DB_PATH` | `/app/data/weather.db` | SQLite database path |

(See [docker_compose_yml.md](docker_compose_yml.md#server-service) for values)

### File Structure Inside Container

```
/app/
  app.py          (main server code)
  protocol.py     (validation logic)
  requirements.txt (dependencies, for reference)
  data/           (directory for SQLite database)
    weather.db    (created at runtime by app.py)
```

### Dependencies

From `server/requirements.txt`:
- `aiosqlite>=0.17.0` (async SQLite wrapper)

### User & Permissions

- Container runs as root (default for Docker)
- Files owned by root (uid 0)
- `/app/data/` writable (can create weather.db)

## Control Flow Walkthrough

### Build Time (docker build)

```
1. $ docker build -f server/Dockerfile -t weather-server .

2. Docker daemon reads Dockerfile line by line:

   FROM python:3.11-slim
   → Pull base image

   WORKDIR /app
   → Set working directory (no layer created)

   COPY requirements.txt .
   → Copy requirements.txt to container image layer

   RUN pip install --no-cache-dir -r requirements.txt
   → Install aiosqlite (creates new image layer)

   COPY app.py .
   COPY protocol.py .
   → Copy source files (creates new layer)

   RUN mkdir -p /app/data
   → Create data directory (creates layer)

   EXPOSE 12345
   → Mark port in metadata (no layer)

   CMD ["python", "app.py"]
   → Set entrypoint metadata (no layer)

3. Docker writes image with all layers to local registry

4. Image available as "weather-server" or full hash ID
```

### Runtime (docker run / docker compose up)

```
1. $ docker compose up

2. Docker Compose starts server container:

   docker run -v weather-data:/app/data \
             -e SERVER_HOST=0.0.0.0 \
             -e SERVER_PORT=12345 \
             -e DB_PATH=/app/data/weather.db \
             -p 12345:12345 \
             weather-server

3. Container startup:

   a) Mount volume weather-data at /app/data
   b) Set environment variables
   c) Execute CMD: python app.py
   d) app.py runs → creates/opens weather.db
   e) app.py starts asyncio event loop
   f) Listens on 0.0.0.0:12345
   g) Health check begins (after start_period: 10s)
   h) Station clients connect (after server healthy)

4. Container running until:
   - Crash (fatal error in app.py)
   - Manual stop (docker stop)
   - Host shutdown
```

## Interfaces

### Build Arguments (None)

This Dockerfile has no build-time arguments (ARG). All configuration is environment variables set at runtime.

### Exposed Ports

- **Port 12345/TCP:** Weather station clients and consumer clients connect here

### Volumes

- `/app/data/` (mounted from docker-compose.yml as `weather-data` volume)

### Environment Variables (Runtime)

Set in docker-compose.yml:
- `SERVER_HOST=0.0.0.0`
- `SERVER_PORT=12345`
- `DB_PATH=/app/data/weather.db`

## Observability & Debugging

### Build Debugging

```bash
# Build with verbose output (see each layer)
docker build -f server/Dockerfile -t weather-server . --progress=plain

# Build without cache (force all layers to rebuild)
docker build -f server/Dockerfile -t weather-server . --no-cache

# Inspect image layers
docker history weather-server
```

### Runtime Debugging

```bash
# Run bash shell instead of app.py
docker run -it weather-server bash

# Inside container, test server startup
python app.py

# View installed packages
pip list

# Check Python version
python --version

# Check import of protocol
python -c "import protocol; print(protocol.MAX_BATCH_SIZE)"
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "No module named 'aiosqlite'" | Dependency not installed | Check requirements.txt; rebuild image |
| "port 12345 already in use" | Container port conflict | Change port mapping in docker-compose |
| "cannot open file 'weather.db'" | /app/data not writable | Check permissions; ensure volume mounted |
| "connection refused" from client | Server not listening | Check logs: `docker logs weather-server` |

### Inspect Image

```bash
# View image details
docker inspect weather-server

# View exposed ports
docker inspect weather-server | grep -A 3 "ExposedPorts"

# View entrypoint
docker inspect weather-server | grep -A 2 "Cmd"
```

---

**Key Takeaway:** The server Dockerfile is minimal and straightforward: Python 3.11 slim base, install aiosqlite dependency, copy source code (app.py + protocol.py), create data directory, expose port 12345, and run the server. No build arguments or complex setup; all configuration is environment variables at runtime. Image size: ~150 MB.
