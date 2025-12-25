# Docker Deployment Fixes

## Issues Resolved

### 1. Database Path Mismatches
**Problem**: Server and web app were using different database path configurations, causing data inconsistency.

**Solution**:
- **Server** (`server/app.py`):
  - Changed from `DB_FILE = "data/weather.db"` (relative path)
  - To `DB_FILE = os.getenv("DB_PATH", "/app/data/weather.db")` (absolute path from env)
  - Added directory creation: `db_path.parent.mkdir(parents=True, exist_ok=True)`
  
- **Web App** (`web/web.py`):
  - Changed from `DB_PATH = Path(__file__).parent.parent / "data" / "weather.db"` (hardcoded)
  - To `DB_PATH = Path(os.getenv("WEATHER_DB_PATH", str(DEFAULT_DB_PATH)))` (env-aware)
  - Added logging: `logger.info(f"Database exists: {DB_PATH.exists()}")`

- **Docker Compose**:
  - Updated server environment: `DB_PATH=/app/data/weather.db`
  - Both server and web now use `/app/data/weather.db` inside containers

### 2. Metrics Column Name Compatibility
**Problem**: Database might use either "windspeed" or "wind_speed" column name.

**Solution**:
- Added `detect_wind_column()` function in `web/web.py`:
  - Queries `PRAGMA table_info(readings)` to detect actual column name
  - Caches result globally to avoid repeated queries
  - Falls back to "windspeed" if detection fails

- Added `normalize_metric(metric)` function:
  - Converts UI metric name to actual DB column name
  - Used in all SQL queries: `/api/stats`, `/api/readings`

- Added startup logging:
  - Logs detected wind column name on startup
  - Helps troubleshooting if column name issues occur

## Environment Variables

### Server Container
```yaml
environment:
  - SERVER_HOST=0.0.0.0
  - SERVER_PORT=12345
  - DB_PATH=/app/data/weather.db    # NEW: Absolute path to DB
```

### Web Container
```yaml
environment:
  - WEB_HOST=0.0.0.0
  - WEB_PORT=8000
  - WEATHER_DB_PATH=/app/data/weather.db  # Uses same path as server
```

## Volume Mounting
Both server and web containers mount the same volume:
```yaml
volumes:
  - weather-data:/app/data
```

This ensures both containers access the same database file.

## Database Schema
Current schema (confirmed):
```sql
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT,
    timestamp TEXT,
    temperature REAL,
    humidity REAL,
    windspeed REAL    -- Column name: "windspeed" (no underscore)
)
```

## Testing the Fixes

### 1. Rebuild Containers
```bash
cd weather-station
docker-compose down
docker-compose up --build -d
```

### 2. Check Logs
```bash
# Server logs - should show "Database initialized: /app/data/weather.db"
docker-compose logs server

# Web logs - should show "Database exists: True" and "Using wind column name: windspeed"
docker-compose logs web

# Station logs - should show successful connections
docker-compose logs station-001
```

### 3. Verify Database
```bash
# Access server container
docker exec -it weather-server sh

# Check database file
ls -lh /app/data/weather.db

# Query readings
python -c "import sqlite3; conn = sqlite3.connect('/app/data/weather.db'); cursor = conn.execute('SELECT COUNT(*) FROM readings'); print('Readings:', cursor.fetchone()[0])"
```

### 4. Test Web Dashboard
```bash
# Open browser to http://localhost:8000
# Should see:
# - Station dropdown populated with STATION-001, STATION-002, STATION-003
# - Stats cards showing latest values
# - Charts displaying time series data
```

## Rollback Plan
If issues occur:
```bash
# Stop containers
docker-compose down

# Remove volume (WARNING: deletes all data)
docker volume rm weather-station_weather-data

# Revert code changes
git checkout server/app.py web/web.py docker-compose.yml

# Rebuild
docker-compose up --build
```

## Future Improvements
1. Add health checks for web container
2. Add database backup/restore scripts
3. Add metrics endpoint for monitoring
4. Consider PostgreSQL for production deployments
