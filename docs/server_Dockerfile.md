# server/Dockerfile Documentation

## Purpose
- Builds the TCP server container image that runs `server/app.py` with its protocol and storage helpers.
- Used by Docker Compose services `server` (normal and stress setups).

## High-level flow
- Based on `python:3.11-slim`.
- Sets workdir `/app`, installs requirements, copies server code, creates data directory, exposes port 12345, and sets CMD to start the server.

## Key data structures
- Files added: `requirements.txt`, `app.py`, `protocol.py`, `storage.py`.
- Directory: `/app/data` for SQLite database.
- Exposed port: 12345.

## Classes
- None (build script only).

## Functions
- None.

## Error handling & edge cases
- Relies on pip install succeeding; no health logic in Dockerfile itself. Compose healthcheck is defined separately.

## Performance & scalability notes
- Slim base image keeps size small; no build-time caching beyond pip layer.

## How to test this file
- Build locally: `docker build -t weather-server:local -f server/Dockerfile server`.
- Run: `docker run --rm -p 12345:12345 weather-server:local` and connect with a client to verify server responses.