# web/Dockerfile — Explanation

**Related docs:** [00_overview.md](00_overview.md), [docker_compose_yml.md](docker_compose_yml.md), [web_web_py.md](web_web_py.md), [web_index_html.md](web_index_html.md), [web_app_js.md](web_app_js.md), [web_styles_css.md](web_styles_css.md)

## Purpose

Builds a Docker image for the Flask web dashboard. The image contains Python 3.11, Flask dependency, and web application code (web.py, HTML templates, CSS/JavaScript static files). When run, it starts a Flask HTTP server on port 8000 serving an interactive dashboard for visualizing weather data.

## Key Responsibilities

- Use lightweight base image (python:3.11-slim)
- Install Flask dependency from requirements.txt
- Copy web application code (web.py, templates/, static/)
- Set default environment variables
- Expose HTTP port 8000
- Define entrypoint (python web.py)

## Important Blocks

### Block: Base Image Selection

**Where:** Line 1 `FROM python:3.11-slim`

**What it does:**
1. Pulls official Python 3.11 slim image
2. Lightweight variant (no build tools, test suites)
3. Size: ~130 MB
4. Sufficient for Flask web app

```dockerfile
FROM python:3.11-slim
```

**Inputs:** Docker Hub official Python images

**Outputs:** Base OS + Python 3.11 runtime

**Why it matters:**
- Python 3.11 required for asyncio patterns (used in web.py)
- Slim variant reduces size
- Official image is maintained and secure

**Failure modes:** None

### Block: Working Directory & Dependencies

**Where:** Lines 3-6

**What it does:**
1. Set working directory to `/app`
2. Copy `requirements.txt` from build context
3. Install pip packages with `--no-cache-dir`

```dockerfile
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

**Inputs:** `requirements.txt` (from web/ directory during build)

**Outputs:** Installed packages (Flask)

**Why it matters:**
- Separate dependency layer enables caching (if requirements unchanged, layer reused)
- `--no-cache-dir` reduces image size (~5 MB saved)

**Failure modes:**
- requirements.txt missing → build fails
- Flask version conflict → import fails at runtime

### Block: Web Application Source Code

**Where:** Lines 8-10

**What it does:**
1. Copy web.py (Flask application) to container
2. Copy templates/ directory (HTML files) to container
3. Copy static/ directory (CSS, JavaScript, Chart.js library) to container

```dockerfile
COPY web.py .
COPY templates/ templates/
COPY static/ static/
```

**Inputs:**
- `web.py` (Flask server)
- `templates/index.html` (single HTML file)
- `static/app.js`, `static/styles.css` (frontend assets)

**Outputs:** Web application files in `/app/`

**Why it matters:**
- Flask requires templates/ and static/ subdirectories
- Each path exists relative to WORKDIR (/app)

**Failure modes:**
- Files missing → Flask fails to load templates/static
- Directory structure wrong → Flask returns 404 errors

### Block: Default Environment Variables

**Where:** Lines 12-14

**What it does:**
1. Set `WEATHER_DB_PATH=../data/weather.db` (SQLite database path)
2. Set `WEB_HOST=0.0.0.0` (listen on all interfaces)
3. Set `WEB_PORT=8000` (HTTP listen port)
4. Values can be overridden by docker-compose.yml

```dockerfile
ENV WEATHER_DB_PATH=../data/weather.db
ENV WEB_HOST=0.0.0.0
ENV WEB_PORT=8000
```

**Inputs:** None (static defaults)

**Outputs:** Environment variables for web.py

**Why it matters:**
- `WEATHER_DB_PATH=../data/weather.db` assumes relative path; overridden in docker-compose to `/app/data/weather.db`
- `WEB_HOST=0.0.0.0` allows external access
- Defaults work for simple cases

**Failure modes:**
- Path misconfiguration → database not found

### Block: Port Exposure

**Where:** Line 17

**What it does:**
1. Document that container listens on HTTP port 8000
2. Does NOT publish port (docker-compose.yml responsibility)
3. Informs image consumers of port usage

```dockerfile
EXPOSE 8000
```

**Inputs:** None

**Outputs:** Metadata in image

**Why it matters:**
- Alerts users that this is a web service
- Port 8000 is non-standard (vs. 80); documented for reference

**Failure modes:** None (informational)

### Block: Entrypoint Command

**Where:** Line 20

**What it does:**
1. Define default command to run when container starts
2. Executes: `python web.py`
3. web.py starts Flask development server

```dockerfile
CMD ["python", "web.py"]
```

**Inputs:** web.py (copied earlier)

**Outputs:** Running Flask HTTP server

**Why it matters:**
- Container automatically starts web server
- Flask serves HTML, static files, and API endpoints

**Failure modes:**
- web.py import fails → container exits
- Port 8000 in use → Flask fails to bind

## Data & State

### Environment Variables (Default in Dockerfile)

| Variable | Default | Overridden In | Purpose |
|----------|---------|---------------|---------|
| `WEATHER_DB_PATH` | `../data/weather.db` | docker-compose.yml | SQLite database path |
| `WEB_HOST` | `0.0.0.0` | docker-compose.yml | Listen on all interfaces |
| `WEB_PORT` | `8000` | docker-compose.yml | HTTP listen port |

### Docker Compose Overrides

(from docker-compose.yml):
```yaml
environment:
  - WEATHER_DB_PATH=/app/data/weather.db
  - WEB_HOST=0.0.0.0
  - WEB_PORT=8000
```

### File Structure Inside Container

```
/app/
  web.py                 (Flask application)
  requirements.txt       (dependencies; for reference)
  templates/
    index.html           (dashboard HTML)
  static/
    app.js               (JavaScript frontend)
    styles.css           (CSS styling)
  data/                  (mounted from volume)
    weather.db           (SQLite database; read-only mount)
```

### Dependencies

From `web/requirements.txt`:
- `Flask>=2.3.0` (HTTP framework)

### Mounted Volumes

- `/app/data/` mounted from named volume `weather-data` (read-only)
- SQLite database at `/app/data/weather.db`

## Control Flow Walkthrough

### Build Time

```
1. $ docker build -f web/Dockerfile -t weather-web .

2. Docker daemon processes:

   FROM python:3.11-slim
   → Pull base image

   WORKDIR /app
   → Set working directory

   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   → Install Flask (creates layer)

   COPY web.py .
   COPY templates/ templates/
   COPY static/ static/
   → Copy all web files (creates layer)

   ENV WEATHER_DB_PATH=../data/weather.db
   ENV WEB_HOST=0.0.0.0
   ENV WEB_PORT=8000
   → Set environment defaults (metadata)

   EXPOSE 8000
   → Document port (metadata)

   CMD ["python", "web.py"]
   → Set entrypoint (metadata)

3. Image created: "weather-web"

4. Image size: ~150 MB (Flask + source files)
```

### Runtime

```
$ docker compose up

1. Docker Compose starts web container:

   docker run -v weather-data:/app/data:ro \
             -e WEATHER_DB_PATH=/app/data/weather.db \
             -e WEB_HOST=0.0.0.0 \
             -e WEB_PORT=8000 \
             -p 8000:8000 \
             weather-web

2. Container startup:

   a) Mount volume weather-data (read-only) at /app/data
   b) Set environment variables
   c) Execute CMD: python web.py
   d) web.py runs:
      - Load_config() reads env vars
      - Create Flask app
      - Register routes: /api/stations, /api/stats, /api/readings, /
      - Flask listens on 0.0.0.0:8000
   e) Ready to accept HTTP requests

3. User accesses: http://localhost:8000

   Flask request handling:
   a) Browser GET http://localhost:8000/
   b) Flask returns index.html (templates/index.html)
   c) Browser loads HTML, JavaScript (static/app.js), CSS (static/styles.css)
   d) JavaScript calls /api/stations, /api/stats, /api/readings
   e) Flask queries SQLite database
   f) Returns JSON to JavaScript
   g) JavaScript renders chart and table

4. Container running until:
   - Manual stop (docker stop)
   - Host shutdown
```

## Interfaces

### Build Arguments

None (no ARG directives)

### Exposed Ports

- **Port 8000/TCP:** HTTP web dashboard

### Volumes

- `/app/data/` (mounted from docker-compose.yml as `weather-data:ro`)

### Environment Variables (Configurable)

```bash
# All can be overridden at runtime
docker run -e WEATHER_DB_PATH=/custom/path/weather.db \
           -e WEB_HOST=127.0.0.1 \
           -e WEB_PORT=8080 \
           weather-web
```

### Flask Routes (Provided by web.py)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serves index.html |
| `/api/stations` | GET | List unique station IDs |
| `/api/stats` | GET | Get latest readings, counts, timestamps |
| `/api/readings` | GET | Get readings with optional filters (time range, limit, station) |

## Observability & Debugging

### Build Debugging

```bash
# Build with verbose output
docker build -f web/Dockerfile -t weather-web . --progress=plain

# Build without cache (force rebuild)
docker build -f web/Dockerfile -t weather-web . --no-cache

# View layers
docker history weather-web
```

### Runtime Debugging

```bash
# Run container interactively
docker run -it weather-web bash

# Inside container, test Flask import
python -c "import flask; print(flask.__version__)"

# Run Flask with debug output
FLASK_ENV=development FLASK_DEBUG=1 python web.py

# View container logs
docker logs weather-web

# Follow logs in real-time
docker logs -f weather-web
```

### Browser Debugging

```bash
# Open dashboard
http://localhost:8000

# Open browser DevTools (F12)
# Check Network tab → see API calls to /api/stations, /api/stats, /api/readings
# Check Console tab → see JavaScript logs (station count, chart render, etc.)
# Check Application tab → Local Storage (no storage used by this app)
```

### Database Inspection

```bash
# Open shell in web container
docker compose exec web bash

# Inside container, query database
sqlite3 /app/data/weather.db

# Inside sqlite3:
sqlite> SELECT COUNT(*) FROM readings;
sqlite> SELECT DISTINCT station_id FROM readings;
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "Address already in use" on port 8000 | Another process using port | Change port mapping in docker-compose |
| "cannot open file 'weather.db'" | Volume mount issue or path wrong | Check docker-compose volume config; check logs |
| "Connection refused" from browser | Flask not listening | Check `docker ps` (container running?); check logs |
| Dashboard shows "Loading..." forever | API error or CORS issue | Check browser console for errors; check Flask logs |
| "No such file or directory: templates/index.html" | COPY failed during build | Ensure templates/ exists and build path correct |

### Manual Flask Test

```bash
# Start container with bash
docker run -it -p 8000:8000 weather-web bash

# Inside container:
python

# Inside Python:
from web import app
with app.test_client() as client:
    response = client.get('/')
    print(response.status_code)  # Should be 200

    response = client.get('/api/stations')
    print(response.json)  # Should be {"stations": [...]}
```

### Network Debugging

```bash
# Check if Flask listening on port 8000 inside container
docker compose exec web netstat -tlnp

# Check if port accessible from host
curl http://localhost:8000

# Check DNS resolution (inside container)
docker compose exec web nslookup server
```

---

**Key Takeaway:** The web Dockerfile is straightforward: Python 3.11-slim base, install Flask, copy source code (web.py, templates/, static/), set 3 environment variables (DB path, host, port), expose port 8000, run the Flask server. Image size: ~150 MB. Configuration is environment variables, making it flexible for different database paths and listen ports. The web container mounts the shared `weather-data` volume read-only, ensuring data consistency with the server's write operations.
