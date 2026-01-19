# Demo Runbook (Weather Station Simulator)

Copy/paste commands + what to show during the presentation.

## Assumptions
- Docker + Docker Compose installed.
- Ports available: TCP **12345**, HTTP **8000**.
- Service names may differ in your `docker-compose.yml`. If a command fails, run `docker compose ps` and replace the service name (e.g., `server`, `web`, `station`).

---

## Demo: Simple (5–7 minutes)

### 1) Start the stack
```bash
# Build + start everything
docker compose up -d --build

# Confirm services are running
docker compose ps

# Watch logs live (pick one)
docker compose logs -f
# or (if your TCP server service is called "server")
docker compose logs -f server
```

**What to check/show**
- Services are **Up** in `docker compose ps`.
- Server logs show it is **listening** and later acknowledges inserts (e.g., OK / inserted=N).

### 2) Start / scale station producers
```bash
# If your producer service is called "station":
docker compose up -d --scale station=3

# Optional: watch station logs
docker compose logs -f station
```

**What to check/show**
- Multiple stations connect.
- Server receives batches and responds with OK/inserted.

### 3) Open the dashboard
- Open in browser: `http://localhost:8000`

**What to check/show**
- Station list populates.
- Chart updates as new data arrives.
- Switching station changes the displayed series/stats.

### 4) Run consumer queries (outside Docker)
Run from your host machine in the project folder (adapt flags to your actual consumer script):
```bash
# Examples
python client.py --mode consumer --request stations
python client.py --mode consumer --request latest --station-id STATION-001
python client.py --mode consumer --request recent --limit 20
```

**What to check/show**
- Output is valid JSON.
- Unknown request returns an error JSON.

### 5) Resilience moment (restart the server)
```bash
# Restart just the server container
docker compose restart server

# Tail logs to show recovery
docker compose logs -f server
```

**What to check/show**
- Stations keep running and reconnect (backoff) if the connection drops.
- System resumes inserts after the server is back.
- Dashboard continues updating.

---

## Demo: Stress (optional, 2–3 minutes)

### 1) Scale producers higher
```bash
# Increase the number of station clients
docker compose up -d --scale station=20

# Watch the server under load
docker compose logs -f server
```

**What to check/show**
- Server stays responsive (continues to acknowledge inserts).
- No SQLite write contention errors (e.g., no "database is locked").
- Dashboard still loads and updates during load.
- Consumer queries still work while producers are sending.

---

## Quick troubleshooting

### See service names
```bash
docker compose ps
```

### Show last logs quickly
```bash
docker compose logs --tail=100
# or per service
docker compose logs --tail=100 server
```

### Port already in use
- Stop old runs: `docker compose down`
- Or change ports in the compose file.

---

## Cleanup
```bash
# Stop containers (keeps volumes)
docker compose down

# Stop and remove volumes (wipes the SQLite DB history)
docker compose down -v
```
