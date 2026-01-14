# station_client/Dockerfile Documentation

## Purpose
- Builds the weather station client container that runs `station_client/client.py` to generate and send readings.
- Used by Compose services `station-001`, `station-002`, `station-003`, and stress test stations.

## High-level flow
- Base image `python:3.11-slim`.
- Sets workdir `/app`, copies `client.py`, sets environment defaults for host/port/station/batching/timeouts, and sets CMD to run the client.

## Key data structures
- Environment defaults baked into image: `WEATHER_STATION_HOST`, `WEATHER_STATION_PORT`, `WEATHER_STATION_ID`, `WEATHER_STATION_BATCH_MIN/MAX`, `WEATHER_STATION_BATCH_INTERVAL`, `WEATHER_STATION_TIMEOUT`, `WEATHER_STATION_MAX_RETRIES`.
- Exposed ports: none (client initiates outbound connections only).

## Classes
- None.

## Functions
- None.

## Error handling & edge cases
- No pip install step (no external deps); build is minimal. Runtime behavior handled by `client.py`.

## Performance & scalability notes
- Small image footprint; many instances can be started for stress testing without heavy build overhead.

## How to test this file
- Build: `docker build -t weather-station:local -f station_client/Dockerfile station_client`.
- Run: `docker run --rm weather-station:local --host <server> --port 12345 --station-id TEST-DOCKER` (assuming server reachable) and observe logs.