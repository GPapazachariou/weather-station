# Documentation Index

This folder contains comprehensive, detailed documentation for the weather station simulator project, designed for university students learning socket programming, databases, Docker, and distributed systems.

## Quick Navigation

### Start Here
- **[00_overview.md](00_overview.md)** — System architecture, data flow diagrams, key concepts

### Application Logic (5 files)
- **[app_py.md](app_py.md)** — TCP server: asyncio, multiplexing, single-writer task
- **[protocol_py.md](protocol_py.md)** — Protocol validation: all-or-nothing semantics, field validation
- **[client_py.md](client_py.md)** — Station client: resilience, buffering, exponential backoff
- **[web_web_py.md](web_web_py.md)** — Flask API: endpoints, aggregations, database queries
- **[test_consumer_py.md](test_consumer_py.md)** — Protocol test suite: 8 comprehensive tests

### Web UI (3 files)
- **[web_index_html.md](web_index_html.md)** — Dashboard HTML: structure, conditional rendering
- **[web_app_js.md](web_app_js.md)** — JavaScript frontend: API calls, Chart.js rendering, rolling average
- **[web_styles_css.md](web_styles_css.md)** — Dashboard styling: CSS Grid, responsive design

### Docker & Deployment (5 files)
- **[dockerfile_server.md](dockerfile_server.md)** — Server container image (Python 3.11, aiosqlite)
- **[dockerfile_station_client.md](dockerfile_station_client.md)** — Client container image (minimal, stdlib only)
- **[dockerfile_web.md](dockerfile_web.md)** — Web UI container image (Flask)
- **[docker_compose_yml.md](docker_compose_yml.md)** — Service orchestration: 3 clients + server + web
- **[docker_compose_stress_yml.md](docker_compose_stress_yml.md)** — Stress test: 100 clients, auto-generated

### Testing & Validation (1 file)
- **[test_api_py.md](test_api_py.md)** — API sanity check: database verification, Flask testing

---

## Key Features of This Documentation

✅ **Grounded in Code:** Every explanation references actual file paths, function names, and code blocks  
✅ **Consistent Structure:** All per-file docs follow the same template (Purpose, Blocks, Interfaces, Observability)  
✅ **Cross-Linked:** Related docs link to each other; 00_overview.md links to all 14 per-file docs  
✅ **Educational Focus:** Aimed at university networking courses; explains design decisions and trade-offs  
✅ **Copy-Paste Commands:** Terminal commands are production-ready (see README.md)  
✅ **Debugging Tips:** Each doc includes troubleshooting, common issues, and manual testing approaches  

---

## Quick Start Commands

```bash
# Run normal mode (3 clients)
docker compose up --build

# Run stress test (100 clients)
docker compose -f docker-compose.stress.yml up --build

# Run tests
python consumer_client/test_consumer.py
python consumer_client/test_api.py

# View dashboard
open http://localhost:8000
```

For detailed setup and demo instructions, see [../README.md](../README.md)

---

## Architecture at a Glance

```
Station Clients (3 or 100)
         ↓ NDJSON batch (1-5 readings)
    TCP Server (port 12345)
         ↓ Single-writer task
    SQLite Database (weather.db)
    ↙                      ↘
Consumer Clients      Web UI (port 8000)
(test script)         Dashboard + API
```

**Key Design Patterns:**
- **Single-writer:** Avoid SQLite lock contention
- **Multiplexed protocol:** Producer batches and consumer queries on same TCP stream
- **Local buffering:** Clients queue readings when disconnected; resume on reconnect
- **Exponential backoff:** Connection retries with 1s–60s jitter (prevent thundering herd)
- **All-or-nothing validation:** Batch fails entirely on first validation error

---

## Document Statistics

| Category | Count | Total Words |
|----------|-------|-------------|
| Application Logic | 5 | ~7,000 |
| Web UI | 3 | ~4,500 |
| Docker & Deployment | 5 | ~4,500 |
| Testing | 1 | ~1,500 |
| Overview | 1 | ~1,200 |
| **Total** | **15** | **~18,700** |

Each file includes:
- Detailed block-by-block explanations
- Control flow walkthroughs
- Interfaces & data structures
- Debugging & troubleshooting
- Common errors & solutions

---

## For Instructors

This documentation set is designed for **networking courses** covering:
- Socket programming (TCP/IP, asyncio)
- Protocol design (NDJSON, request/response patterns)
- Database access (SQLite, concurrency, transactions)
- Container orchestration (Docker, Docker Compose)
- Web API design (REST endpoints, JSON serialization)
- Client resilience (exponential backoff, buffering, retries)

**Use cases:**
- Assignment reference material (students can read to understand requirements)
- Debugging guide (students stuck on errors can find solutions)
- Implementation verification (students can compare their code to explained patterns)
- Architecture discussion (explain design trade-offs and constraints)

---

## File Structure

```
weather-station/
├── README.md                           ← START HERE (how to run/test)
├── docs/
│   ├── ARCHITECTURE.md                 ← Design decisions document
│   ├── explanation/                    ← This folder
│   │   ├── 00_overview.md              ← System overview
│   │   ├── app_py.md                   ← Server logic
│   │   ├── protocol_py.md              ← Validation
│   │   ├── client_py.md                ← Client logic
│   │   ├── web_web_py.md               ← Flask API
│   │   ├── web_index_html.md           ← HTML template
│   │   ├── web_app_js.md               ← JavaScript frontend
│   │   ├── web_styles_css.md           ← CSS styling
│   │   ├── dockerfile_server.md        ← Server image
│   │   ├── dockerfile_station_client.md ← Client image
│   │   ├── dockerfile_web.md           ← Web image
│   │   ├── docker_compose_yml.md       ← Compose config
│   │   ├── docker_compose_stress_yml.md ← Stress config
│   │   ├── test_api_py.md              ← API tests
│   │   └── test_consumer_py.md         ← Protocol tests
├── docker-compose.yml
├── server/
│   ├── app.py
│   ├── protocol.py
│   └── Dockerfile
├── station_client/
│   ├── client.py
│   └── Dockerfile
├── web/
│   ├── web.py
│   ├── Dockerfile
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── app.js
│       └── styles.css
└── consumer_client/
    ├── test_api.py
    └── test_consumer.py
```

---

**Generated:** January 2025  
**Audience:** University networking course students, educators, software engineers  
**License:** Educational use (same as project license)
