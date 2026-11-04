# Documentation Index

This folder contains comprehensive architecture and implementation documentation for the **Weather Station Simulator** distributed telemetry ingestion platform.

## Quick Navigation

### Start Here
- **[00_overview.md](00_overview.md)** — System architecture, data flow diagrams, key concepts
- **[architecture-decisions.md](architecture-decisions.md)** — Detailed architecture, protocol specification, and design trade-offs

### Application Logic (5 files)
- **[app_py.md](app_py.md)** — TCP server: asyncio, multiplexing, single-writer task
- **[protocol_py.md](protocol_py.md)** — Protocol validation: all-or-nothing semantics, field validation
- **[client_py.md](client_py.md)** — Station client: resilience, buffering, exponential backoff
- **[web_web_py.md](web_web_py.md)** — Flask API: endpoints, aggregations, database queries
- **[test_consumer_py.md](test_consumer_py.md)** — Protocol test suite: 8 comprehensive live integration tests

### Web UI (3 files)
- **[web_index_html.md](web_index_html.md)** — Dashboard HTML: structure, conditional rendering
- **[web_app_js.md](web_app_js.md)** — JavaScript frontend: API calls, Chart.js rendering, rolling average
- **[web_styles_css.md](web_styles_css.md)** — Dashboard styling: CSS Grid, responsive design

### Docker & Deployment (5 files)
- **[dockerfile_server.md](dockerfile_server.md)** — Server container image (Python 3.11, aiosqlite)
- **[dockerfile_station_client.md](dockerfile_station_client.md)** — Client container image (minimal, stdlib only)
- **[dockerfile_web.md](dockerfile_web.md)** — Web UI container image (Flask)
- **[docker_compose_yml.md](docker_compose_yml.md)** — Service orchestration: 3 clients + server + web
- **[docker_compose_stress_yml.md](docker_compose_stress_yml.md)** — Stress test configuration: horizontally scaled station clients

### Testing & Validation (1 file)
- **[test_api_py.md](test_api_py.md)** — API sanity check: database verification, Flask testing patterns

---

## Key Features of This Documentation

✅ **Grounded in Code:** Every explanation references actual file paths, function names, and code blocks  
✅ **Consistent Structure:** All per-file docs follow the same template (Purpose, Blocks, Interfaces, Observability)  
✅ **Cross-Linked:** Related docs link to each other; [00_overview.md](00_overview.md) links to per-file deep dives  
✅ **Architecture Focus:** Explains distributed systems design decisions, concurrency models, and trade-offs  
✅ **Copy-Paste Commands:** Terminal commands are ready to run (see [../../README.md](../../README.md))  
✅ **Debugging Tips:** Each doc includes troubleshooting, common issues, and verification approaches  

---

## Quick Start Commands

```bash
# Run normal mode (3 clients + server + web UI)
docker compose up --build

# Run stress test (scale to 100 concurrent station replicas)
docker compose -f docker-compose.stress.yml up --build --scale station=100

# Run automated unit & integration test suite
pytest

# Run live consumer protocol integration tests (requires running server)
python consumer_client/test_consumer.py

# View dashboard
open http://localhost:8000
```

For detailed setup and demo instructions, see [../../README.md](../../README.md).

---

## Architecture at a Glance

```
Station Clients (3 or 100)
         ↓ NDJSON batch (1–50 readings)
    TCP Server (port 12345)
         ↓ Single-writer task
    SQLite Database (weather.db)
    ↙                      ↘
Consumer Clients      Web UI (port 8000)
(test script)         Dashboard + API
```

**Key Design Patterns:**
- **Single-writer:** Avoids SQLite lock contention under high concurrency
- **Multiplexed protocol:** Producer batches and consumer queries on the same TCP stream
- **Local buffering:** Clients queue readings when disconnected and flush on reconnect
- **Exponential backoff:** Connection retries with 1s–60s jitter to prevent thundering herd
- **All-or-nothing validation:** Batch fails atomically on first validation error

---

## Document Statistics

| Category | Count | Total Words |
|----------|-------|-------------|
| Application Logic | 5 | ~7,000 |
| Web UI | 3 | ~4,500 |
| Docker & Deployment | 5 | ~4,500 |
| Testing | 1 | ~1,500 |
| Overview & Architecture | 2 | ~4,000 |
| **Total** | **16** | **~21,500** |

Each file includes:
- Detailed block-by-block explanations
- Control flow walkthroughs
- Interfaces & data structures
- Debugging & troubleshooting
- Common errors & solutions

---

## Core Engineering Topics Covered

This documentation set covers key distributed systems and software engineering concepts:
- Socket programming (TCP/IP, Python `asyncio` streams)
- Protocol design (NDJSON framing, multiplexed request/response patterns)
- Database access (SQLite WAL mode, single-writer concurrency, atomic transactions)
- Container orchestration (Docker multi-container networking, health checks, horizontal scaling)
- Web API design (REST endpoints, time-windowed aggregations, JSON serialization)
- Client resilience (jittered exponential backoff, bounded FIFO buffering, automatic recovery)

---

## File Structure

```
weather-station/
├── README.md                           ← Main project documentation & guide
├── LICENSE                             ← MIT License
├── pyproject.toml                      ← Packaging, pytest, coverage & ruff config
├── requirements-dev.txt                ← Development & test dependencies
├── docker-compose.yml                  ← Main Compose stack (server + 3 stations + web)
├── docker-compose.stress.yml           ← Stress Compose stack (scalable station service)
├── docs/
│   ├── PROJECT_REVIEW.md               ← Project architecture & quality review
│   ├── CODE_REVIEW.md                  ← Codebase audit & findings
│   ├── diagrams/
│   │   ├── architecture_component_view.md
│   │   └── sequence_message_flow.md
│   └── explanation/                    ← This folder
│       ├── INDEX.md                    ← Documentation index
│       ├── 00_overview.md              ← System overview
│       ├── architecture-decisions.md   ← Design decisions document
│       ├── app_py.md                   ← Server logic
│       ├── protocol_py.md              ← Validation
│       ├── client_py.md                ← Client logic
│       ├── web_web_py.md               ← Flask API
│       ├── web_index_html.md           ← HTML template
│       ├── web_app_js.md               ← JavaScript frontend
│       ├── web_styles_css.md           ← CSS styling
│       ├── dockerfile_server.md        ← Server image
│       ├── dockerfile_station_client.md ← Client image
│       ├── dockerfile_web.md           ← Web image
│       ├── docker_compose_yml.md       ← Compose config
│       ├── docker_compose_stress_yml.md ← Stress config
│       ├── test_api_py.md              ← API testing guide
│       └── test_consumer_py.md         ← Protocol tests
├── server/
│   ├── app.py
│   ├── protocol.py
│   ├── requirements.txt
│   └── Dockerfile
├── station_client/
│   ├── client.py
│   ├── requirements.txt
│   └── Dockerfile
├── web/
│   ├── web.py
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── app.js
│       └── styles.css
├── consumer_client/
│   └── test_consumer.py
├── tests/
│   ├── test_protocol.py
│   ├── test_server.py
│   ├── test_web.py
│   ├── test_client.py
│   └── test_smoke.py
├── scripts/
│   ├── gen_stress_compose.py
│   └── verify_correctness_perf.py
└── screenshots/
    ├── 01_docker_compose.png
    ├── 02_docker_ps.png
    ├── 03_network_ls.png
    ├── 04_docker_logs.png
    ├── 05_web_ui.png
    ├── 06_architecture_components.png
    └── 07_message_flow.png
```

---

**License:** [MIT License](../../LICENSE)
