# web/Dockerfile Documentation

## Purpose
- Builds the Flask web dashboard container that serves HTML/JS and APIs from `web/web.py`.
- Used by the `web` service in both normal and stress Compose files.

## High-level flow
- Base `python:3.11-slim`.
- Sets workdir `/app`, installs `Flask` from `requirements.txt`, copies `web.py`, templates, and static assets, sets default env for DB path and host/port, exposes 8000, and starts `web.py`.

## Key data structures
- Environment defaults: `WEATHER_DB_PATH=../data/weather.db` (overridden in Compose to `/app/data/weather.db`), `WEB_HOST=0.0.0.0`, `WEB_PORT=8000`.
- Exposed port: 8000.

## Classes
- None.

## Functions
- None.

## Error handling & edge cases
- Depends on pip install of Flask; otherwise straightforward. Runtime DB path provided by volume in Compose.

## Performance & scalability notes
- Slim base keeps image small; no build-time compilation needed.

## How to test this file
- Build: `docker build -t weather-web:local -f web/Dockerfile web`.
- Run with mounted DB: `docker run --rm -p 8000:8000 -v $(pwd)/data:/app/data weather-web:local` and open http://localhost:8000.