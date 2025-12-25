# Weather Station Docker Deployment

Complete Docker setup for the Weather Station project with persistent storage and configurable clients.

## Architecture

- **Server**: TCP server receiving weather data, storing in SQLite
- **Station Clients**: Configurable weather stations sending data
- **Web Dashboard**: Flask-based UI for visualization
- **Persistent Storage**: SQLite database in Docker volume

## Quick Start

### Build and Start All Services

```bash
docker-compose up --build
```

### Start in Background (Detached Mode)

```bash
docker-compose up -d
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f server
docker-compose logs -f station-001
docker-compose logs -f web
```

### Stop All Services

```bash
docker-compose down
```

### Stop and Remove Volumes (Delete Database)

```bash
docker-compose down -v
```

## Services

### Server (Port 12345)
- Receives weather data via TCP
- Stores data in SQLite at `/app/data/weather.db`
- Persistent volume: `weather-data`

### Station Clients (3 by default)
- **STATION-001, STATION-002, STATION-003**
- Each generates deterministic sensor data
- Configurable via environment variables

### Web Dashboard (Port 8000)
- Access at: http://localhost:8000
- Reads from shared SQLite database (read-only)
- Displays time-series charts and statistics

## Configuration

### Adding More Stations

Edit `docker-compose.yml` and add a new service:

```yaml
station-004:
  build:
    context: ./station_client
    dockerfile: Dockerfile
  container_name: weather-station-004
  environment:
    - WEATHER_STATION_HOST=server
    - WEATHER_STATION_PORT=12345
    - WEATHER_STATION_ID=STATION-004
    - WEATHER_STATION_BATCH_INTERVAL=10
  depends_on:
    server:
      condition: service_healthy
  restart: unless-stopped
  networks:
    - weather-network
```

### Environment Variables

#### Server
- `SERVER_HOST`: Bind address (default: 0.0.0.0)
- `SERVER_PORT`: TCP port (default: 12345)
- `DB_PATH`: SQLite database path (default: data/weather.db)

#### Station Client
- `WEATHER_STATION_HOST`: Server hostname (default: server)
- `WEATHER_STATION_PORT`: Server port (default: 12345)
- `WEATHER_STATION_ID`: Unique station identifier
- `WEATHER_STATION_BATCH_MIN`: Min batch size (default: 1)
- `WEATHER_STATION_BATCH_MAX`: Max batch size (default: 1)
- `WEATHER_STATION_BATCH_INTERVAL`: Seconds between batches (default: 5)
- `WEATHER_STATION_TIMEOUT`: Response timeout (default: 5.0)
- `WEATHER_STATION_MAX_RETRIES`: Max retry attempts (default: 3)

#### Web Dashboard
- `WEATHER_DB_PATH`: Database path (default: /app/data/weather.db)
- `WEB_HOST`: Bind address (default: 0.0.0.0)
- `WEB_PORT`: HTTP port (default: 8000)

## Data Persistence

The SQLite database is stored in a Docker volume named `weather-data`:

```bash
# Inspect volume
docker volume inspect weather-station_weather-data

# Backup database
docker cp weather-server:/app/data/weather.db ./backup.db

# Restore database
docker cp ./backup.db weather-server:/app/data/weather.db
```

## Development

### Rebuild Single Service

```bash
docker-compose build server
docker-compose up -d server
```

### Scale Station Clients

```bash
# Not directly supported with named containers
# Instead, add/remove services in docker-compose.yml
```

### Access Container Shell

```bash
docker exec -it weather-server sh
docker exec -it weather-station-001 sh
docker exec -it weather-web sh
```

## Troubleshooting

### Server Not Starting
```bash
docker-compose logs server
# Check if port 12345 is already in use
```

### Clients Can't Connect
```bash
docker-compose logs station-001
# Ensure server health check passes
# Check network connectivity
```

### Web Dashboard Shows No Data
```bash
# Verify database exists and has data
docker exec -it weather-server ls -lh /app/data/
docker exec -it weather-server sqlite3 /app/data/weather.db "SELECT COUNT(*) FROM readings;"
```

### Reset Everything
```bash
docker-compose down -v
docker-compose up --build
```

## Production Considerations

1. **Use specific image tags** instead of `latest`
2. **Set resource limits** in docker-compose.yml
3. **Configure log rotation** for long-running containers
4. **Use secrets** for sensitive configuration
5. **Add monitoring** (Prometheus, Grafana)
6. **Set up backups** for the database volume
7. **Use reverse proxy** (nginx) for web dashboard
8. **Enable SSL/TLS** for production deployment

## Network Architecture

```
┌─────────────────────────────────────────────┐
│         Docker Network: weather-network      │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │STATION-  │  │STATION-  │  │STATION-  │  │
│  │  001     │  │  002     │  │  003     │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │             │             │         │
│       └─────────────┼─────────────┘         │
│                     │                       │
│              ┌──────▼──────┐                │
│              │   SERVER    │                │
│              │  (Port      │                │
│              │   12345)    │                │
│              └──────┬──────┘                │
│                     │                       │
│                ┌────▼────┐                  │
│                │ SQLite  │                  │
│                │   DB    │                  │
│                │ (Volume)│                  │
│                └────┬────┘                  │
│                     │                       │
│              ┌──────▼──────┐                │
│              │     WEB     │                │
│              │  Dashboard  │                │
│              │ (Port 8000) │                │
│              └─────────────┘                │
│                     │                       │
└─────────────────────┼───────────────────────┘
                      │
                      ▼
              http://localhost:8000
```

## Health Checks

The server includes a health check that verifies TCP connectivity on port 12345. Clients wait for the server to be healthy before starting.

To check service health:
```bash
docker-compose ps
```

## Monitoring

View real-time resource usage:
```bash
docker stats
```

## License

See project LICENSE file.
