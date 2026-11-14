# AGENTS.md

Guidance for AI coding agents working in this repository. `CLAUDE.md` points here, so this file is the single source of truth.

## How to work with me

**Tone.** Be short and direct. Use plain language, lead with the result, and skip filler. Write more only when the content needs it.

**Autonomy.** Handle small, clear tasks yourself, then report. For bigger work (several files, new features, design or scope choices), first propose a short plan with the design and scope options, agree on it with me, and then build it.

**Push back.** If you think my request is wrong or there's a better approach, stop before building. Propose the alternative with its pros and cons, and let me decide. Saying no is fine when it's warranted.

**When unsure.** Investigate the code first. Ask me only when the answer really depends on me. Never present a guess as fact.

**Git.** Never commit, push, or open PRs unless I ask.

**Done means verified.** Before reporting, run whatever tests prove the change works *and* that nothing else broke. At minimum that's `ruff check .` and the full `pytest` suite. For protocol changes, also run the consumer client against a live server. For dashboard changes, run the web app and check it. Report results honestly, including failures and anything you skipped.

**Final report.** Keep it short: what changed and why, test results, and anything I need to decide or know.

## Project

Distributed weather-station telemetry platform:

- `server/` — asyncio TCP server on `:12345`. Validates NDJSON batches (`protocol.py`) and persists them to SQLite through a single-writer queue (`app.py`). It also answers consumer queries (`ping`, `stations`, `latest`, `recent`, `latest_seq`, `since`) on the same port, and serves Prometheus metrics on `:9100` (`telemetry.py`; named that way because `web/metrics.py` already owns the `metrics` import name).
- `station_client/` — simulated station (`client.py`) that uses jittered exponential backoff and a bounded local buffer.
- `web/` — Flask dashboard and REST API on `:8000` (`web.py`, `metrics.py`, `templates/`, `static/`). Reads SQLite in read-only/WAL mode.
- `consumer_client/` — live integration script run against a running server.
- `scripts/` — stress-compose generator, demo seeding, correctness/perf and recovery verification, and the network lab (`netlab.py` harness, `impair_proxy.py`, `netlab_plot.py`). Lab runs are recorded under `docs/evidence/netlab/<label>/`; record them in Docker (`Dockerfile.verify`), since Windows timer granularity distorts small delays.
- `tests/` — pytest suite.
- `docs/` — design notes, reviews, and per-file explanations (`docs/explanation/`).

## Commands

```bash
pip install -r requirements-dev.txt   # runtime + dev deps
ruff check .                          # lint (CI gate)
pytest                                # tests + coverage (CI gate)
docker compose up --build             # full stack: server, 3 stations, web
docker compose -f docker-compose.stress.yml up --build --scale station=100
```

CI (`.github/workflows/ci.yml`) runs `ruff check .` and `pytest` on Python 3.11, 3.12, and 3.13. Before you finish a change, make sure both pass locally.

## Conventions

- Target Python 3.11+. Ruff line length is 100, with rules `E, F, W, I, B` (see `pyproject.toml`).
- pytest puts `server`, `web`, and `station_client` on `pythonpath`, so tests import modules directly (`import protocol`, `import web`, `import client`), not as packages.
- `asyncio_mode = "auto"`, so async tests don't need a marker.
- All SQLite writes go through the server's single-writer queue. Don't add new write paths elsewhere, and keep the web app read-only.
- Any protocol change in `server/protocol.py` must stay compatible with `station_client/client.py` and `consumer_client/test_consumer.py`.

## Gotchas

- The server and the web app use **different** env vars for the database path: `DB_PATH` for the server and `WEATHER_DB_PATH` for the web app. The server defaults to `/app/data/weather.db` and the web app defaults to `./data/weather.db`. When running outside Docker, set both to the same file.
- Station config precedence is CLI args, then `WEATHER_STATION_*` env vars, then defaults. Station ID resolution also falls back to `HOSTNAME`, which is how `--scale` replicas get unique IDs.

## Agent skills

Shared skills are mirrored in `.agents/`, `.claude/`, `.codex/`, `.gemini/`, and `.antigravity/` under `skills/`. The copies are identical except that `.gemini/` also has `goal-loop`. If you edit a skill, update every mirror.
