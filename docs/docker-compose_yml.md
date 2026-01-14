# docker-compose.yml Documentation

## Purpose
- Orchestrates the normal demo stack: one TCP server, three station clients, and one web dashboard, all on a shared bridge network with a persistent SQLite volume.

## High-level flow
- `server` service builds from `server/Dockerfile`, exposes 12345, mounts `weather-data` for SQLite, sets DB path env, and includes a TCP healthcheck.
- `station-001/002/003` services build from `station_client/Dockerfile`, depend on server health, connect to `server:12345`, and stream data at 5s intervals.
- `web` service builds from `web/Dockerfile`, exposes 8000, mounts the DB volume read-only, and depends on server.
- All services join `weather-network`; volume `weather-data` persists database.

## Key data structures
- Volume: `weather-data` (named, driver local).
- Network: `weather-network` (bridge).
- Environment per service: DB paths for server/web; station IDs and batching/timeouts for clients.

## Classes
- None.

## Functions
- None (YAML specification).

## Error handling & edge cases
- Server healthcheck ensures stations wait until port 12345 is reachable.
- If volume path unwritable, server will log DB init errors at runtime.
- Port conflicts on host 12345/8000 must be resolved by changing published ports.

## Performance & scalability notes
- Single SQLite volume shared read/write (server) and read-only (web); adequate for demo-scale load.
- Three clients generate modest traffic; scale-up requires adjusting batch intervals or moving to stress compose.

## How to test this file
- From repo root: `docker compose up --build`.
- Verify: `docker compose ps` shows healthy services; open http://localhost:8000; check `docker compose logs server` for inserts; inspect DB via `docker exec weather-server sqlite3 /app/data/weather.db "SELECT COUNT(*) FROM readings;"`.