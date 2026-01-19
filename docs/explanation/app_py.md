# server/app.py — Explanation

**Related docs:** [00_overview.md](00_overview.md), [protocol_py.md](protocol_py.md), [docker_compose_yml.md](docker_compose_yml.md)

## Purpose

The TCP server is the central hub of the system. It listens on port 12345 for incoming JSON-based messages from weather station clients and optional consumer clients. It multiplexes two request types: producer batches (writes) and consumer queries (reads). All database writes are funneled through a single dedicated writer task to avoid SQLite lock contention, while reads open fresh connections on-demand.

## Key Responsibilities

- Listen on TCP port 12345 for client connections using asyncio
- Parse and validate newline-delimited JSON messages (max 65536 bytes per line)
- Route requests: producer batches (JSON arrays) to validation → queue → DB, or consumer dicts with `"request"` key to read-only handlers
- Maintain a single asyncio.Queue to serialize database writes
- Run a dedicated `db_writer_task()` that dequeues batches and inserts them as transactions
- Apply SQLite pragmas (WAL mode, PRAGMA synchronous=NORMAL, busy_timeout=3000ms) to optimize concurrency
- Handle client disconnections gracefully and flush remaining queued batches on shutdown
- Implement all-or-nothing batch validation: either entire batch succeeds or entire batch fails

## Important Blocks

### Block: Initialization & Database Setup

**Where:** `init_database()`, `app.py` top-level globals (HOST, PORT, DB_FILE)

**What it does:** Creates the SQLite database file and table schema on first run. Sets environment-based `DB_PATH` (default `/app/data/weather.db`). Initializes global state: `write_queue`, `shutdown_event`, `server`, `writer_task`, `db_connection`.

**Inputs:** `DB_FILE` env var or default path; asyncio event loop available

**Outputs:** SQLite table `readings` created; globals initialized; directory `/app/data` created if needed

**Why it matters:** Ensures database schema exists before any writes; centralizes configuration so Docker compose can override `DB_PATH` per deployment

**Failure modes:** DB file cannot be created (permission error, disk full) → exception halts startup

### Block: Single-Writer Task (db_writer_task)

**Where:** `db_writer_task()` coroutine

**What it does:** 
1. Opens one persistent SQLite connection at startup
2. Applies WAL + pragmas for concurrent access
3. Enters infinite loop: dequeue `(batch, result_future)` from `write_queue`
4. Performs `executemany()` INSERT in a single transaction
5. Resolves future with row count or exception
6. Checks sentinel `(None, None)` to exit and flush remaining batches
7. Closes connection on final exit

**Inputs:** Batches from `write_queue` (list of reading dicts); asyncio events

**Outputs:** Rows inserted into SQLite; futures resolved with status; WAL/PRAGMA applied to DB

**Why it matters:** 
- SQLite default locking is per-connection; only one writer prevents `database is locked` errors
- Sequential processing ensures no race conditions
- Queue naturally applies backpressure: slow writers rate-limit fast clients
- Graceful shutdown: sentinel signals writer to flush before exit

**Failure modes:**
- DB connection fails on startup → exception halts server
- Batch insert fails (invalid data somehow got past validation) → future.set_exception() signals client with error
- Client disconnects while future is pending → code checks `result_future.done()` before setting state to avoid invalid state error

### Block: Safe JSON Parsing

**Where:** `_safe_json_loads(data: str)`

**What it does:** Wraps Python's `json.loads()` with a custom `parse_constant` callback that rejects NaN, Infinity, and -Infinity. Raises `ValueError` if non-finite constants are detected.

**Inputs:** JSON string

**Outputs:** Parsed dict/list, or `ValueError`

**Why it matters:** Wire protocols may not support IEEE special values; rejecting them early prevents ambiguous data in DB

**Failure modes:** JSON with `NaN` or `Infinity` → ValueError → client gets error response

### Block: Request Multiplexing in handle_client

**Where:** `handle_client(reader, writer)` coroutine

**What it does:**
1. Read one line (max 65536 bytes enforced by asyncio.StreamReader limit)
2. Decode UTF-8, parse JSON safely
3. Check if parsed data is dict with "request" key (consumer path) or array (producer path)
4. Route accordingly:
   - Consumer: call `handle_consumer_request()`, send JSON response
   - Producer: validate batch, enqueue to write_queue, send response
5. On line_too_long or connection close, break and exit

**Inputs:** TCP client connection (reader/writer); protocol is NDJSON

**Outputs:** JSON response per request; connection closed on error or timeout

**Why it matters:** Single TCP stream carries both request types; multiplexing logic here determines path

**Failure modes:**
- Line exceeds 65536 bytes → asyncio.LimitOverrunError → respond with error + close connection
- Invalid JSON → ValueError → respond with error
- Validation fails → all-or-nothing rule means entire batch rejected
- Client disconnects → connection closes, handle exits

### Block: Batch Validation & Enqueueing

**Where:** In `handle_client()` producer path; calls `validate_batch()` then `enqueue_batch()`

**What it does:**
1. Call `validate_batch(batch)` from protocol.py (raises ValueError on first error)
2. If valid, create a future using current event loop
3. Add `(batch, result_future)` to write_queue
4. Await future (blocks until writer dequeues and processes)
5. Send response with row count or exception message

**Inputs:** JSON array of reading dicts (already parsed)

**Outputs:** Response JSON; future resolved with row count; rows in DB (if successful)

**Why it matters:** 
- Validation is all-or-nothing: no partial inserts preserve DB consistency
- Enqueueing serializes writes through single task
- Awaiting future ensures client sees response only after DB commit

**Failure modes:** Validation error → client receives error, nothing inserted; DB error → future.set_exception() sends error to client

### Block: Consumer Request Handling (Read-Only)

**Where:** `handle_consumer_request(req, addr)` coroutine

**What it does:** 
- Detect request type ("stations", "latest", "recent")
- Open fresh DB connection (read-only, each request)
- Execute corresponding SELECT query
- Format response JSON

**Supported requests:**
- `"stations"` → DISTINCT station_id
- `"latest"` (optional station_id) → ORDER BY id DESC LIMIT 1
- `"recent"` (optional station_id, limit 1–500) → ORDER BY id DESC LIMIT ?

**Inputs:** Dict with "request" key and optional parameters

**Outputs:** JSON response with status + data

**Why it matters:** Separate read path from write path; consumer queries don't contend with write queue

**Failure modes:** 
- Unknown request type → error response with "unknown_request:<type>"
- DB read fails → error response with server_error message
- No data matching query → null reading or empty readings list (valid response)

### Block: Graceful Shutdown

**Where:** Signal handlers registered in main; `shutdown_event`; final flush in `db_writer_task()`

**What it does:**
1. On SIGINT/SIGTERM, set `shutdown_event`
2. Main loop detects shutdown_event and breaks
3. Cancel pending client tasks
4. Send sentinel `(None, None)` to writer task
5. Writer task flushes remaining queued batches then exits
6. Close server socket

**Inputs:** OS signals (SIGINT, SIGTERM)

**Outputs:** Graceful exit; all pending writes processed before close

**Why it matters:** Prevents data loss; ensures all batches in queue are persisted before DB connection closes

**Failure modes:** Forceful kill (SIGKILL) bypasses handler → data in queue lost; OOM during flush → exception logged but process exits anyway

### Block: Database Writer Lifecycle

**Where:** `db_writer_task()` setup; `PRAGMA` statements

**What it does:**
1. Apply `PRAGMA journal_mode=WAL` — Write-Ahead Logging allows concurrent reads while writer commits
2. Apply `PRAGMA synchronous=NORMAL` — Sync only WAL, not main DB file; balances durability and speed
3. Apply `PRAGMA busy_timeout=3000` — Wait up to 3 seconds if DB is locked before giving up

**Inputs:** Fresh SQLite connection

**Outputs:** Database configured for concurrent access; writes are durable

**Why it matters:** WAL mode + relaxed sync optimize for high-write concurrency; busy_timeout prevents spurious lock errors

**Failure modes:** Pragmas fail (bad DB state) → exception logs but may proceed anyway

## Data & State

### Global Variables

```python
write_queue = None                  # asyncio.Queue holding (batch, future) tuples
shutdown_event = None              # asyncio.Event signaling graceful shutdown
server = None                       # asyncio.Server instance
writer_task = None                 # asyncio.Task running db_writer_task()
db_connection = None               # aiosqlite.Connection (persistent in writer task)
_WRITER_SENTINEL = (None, None)    # Sentinel tuple signaling writer to exit
```

### Key Invariants

- `write_queue` is the only inter-task communication channel; only one consumer (writer_task)
- `db_connection` is owned by writer_task; no other coroutine accesses it
- Futures in queue represent pending writes; resolved only by writer_task
- `shutdown_event` is used to coordinate graceful exit across all tasks

## Control Flow Walkthrough

### Startup

```
main() / asyncio.run()
  ├─ init_database()                  // Create DB + table
  ├─ Create asyncio.Queue()           // write_queue
  ├─ Create asyncio.Event()           // shutdown_event
  ├─ start_server(handle_client, host:port, limit=65537)  // MAX_LINE_SIZE+1
  └─ Run forever:
       ├─ writer_task = create_task(db_writer_task())      // Start writer
       └─ await shutdown_event.wait()                      // Block until signal
```

### Per-Connection (Client Connects)

```
handle_client(reader, writer):
  ├─ Log "Client connected"
  └─ While not shutdown:
       ├─ line = await reader.readline()
       ├─ if not line: break                               // EOF
       ├─ data = json.loads(line, safe=True)
       ├─ if dict + "request" in data:
       │   └─ resp = await handle_consumer_request(data)
       │       └─ response = SELECT * FROM readings ...
       └─ else:
           ├─ validate_batch(data)                          // Raises on error
           ├─ inserted = await enqueue_batch(data)          // Await DB write
           └─ response = {"status": "ok", "inserted": inserted}
       ├─ writer.write(response + "\n")
       └─ await writer.drain()
```

### Writer Task (Background)

```
db_writer_task():
  ├─ Open persistent DB connection
  ├─ Apply WAL + pragmas
  └─ While True:
       ├─ (batch, future) = await write_queue.get(timeout=1s)
       ├─ if (batch is None and future is None):          // Sentinel
       │   └─ break
       ├─ if future.cancelled() or future.done(): continue // Skip
       ├─ await db.executemany(INSERT, batch)              // Transaction
       ├─ await db.commit()
       └─ future.set_result(len(batch))
  └─ Flush remaining queue items
  └─ await db.close()
```

## Interfaces

### Imports & Dependencies

```python
import asyncio               # Async runtime
import json                  # JSON parsing (with custom parse_constant)
import aiosqlite            # Async SQLite interface
import signal               # For SIGINT/SIGTERM handling
import os                   # Environment variables
from pathlib import Path    # Directory creation
from protocol import validate_batch, MAX_LINE_SIZE  # Batch validation
```

### TCP Protocol

- **In:** Newline-delimited JSON (one line per message)
- **Out:** Newline-delimited JSON (one line per response)
- **Line max:** 65536 bytes (enforced by asyncio.StreamReader limit)

### Environment Variables

```
DB_PATH=/app/data/weather.db  (default)
SERVER_HOST=0.0.0.0           (hardcoded)
SERVER_PORT=12345             (hardcoded)
```

### Database Interface

```python
await aiosqlite.connect(DB_FILE)
await db.execute("PRAGMA ...")
await db.executemany("INSERT ...", [(read1), (read2), ...])
await db.commit()
```

### Consumer Request/Response Examples

**Request:**
```json
{"request": "latest", "station_id": "STATION-001"}
```

**Response:**
```json
{"status": "ok", "reading": {"station_id": "STATION-001", "timestamp": "2025-01-17T14:30:45Z", "temperature": 22.5, "humidity": 58.3, "windspeed": 4.2}}
```

## Observability & Debugging

### Key Log Messages

```
Database initialized: /app/data/weather.db
Client connected: <IP:port>
Processed producer batch from <IP>: <count> readings inserted
Processed consumer request from <IP>: <request_type>
Database writer task started
SQLite pragmas applied: WAL mode, NORMAL sync, 3000ms busy timeout
Flushing remaining writes...
Database connection closed
```

### Common Errors

| Error | Cause | Mitigation |
|-------|-------|-----------|
| `asyncio.LimitOverrunError` | Client sends line > 65536 bytes | Server responds with "line_too_long" and closes connection |
| `json.JSONDecodeError` | Invalid JSON in line | Server responds with error; connection remains open |
| `ValueError` from `validate_batch()` | Batch fails validation | Server responds with validation error; nothing inserted |
| `sqlite3.OperationalError` | Database error (corrupt, disk full, permissions) | Exception logged; future.set_exception() sends error to client |
| `asyncio.CancelledError` | Client disconnects while awaiting write | Writer task checks future.done() before setting state |

### Debugging Tips

- Check if DB file exists: `ls -la /app/data/weather.db` (or Docker volume `docker volume inspect weather-station_weather-data`)
- Monitor queue depth: add logging in writer task to print queue size
- Trace request path: look for "Processed producer batch" vs "Processed consumer request" logs
- Check SQLite lock waits: enable `PRAGMA query_only=0` tracing or use `sqlite3 .schema` to verify table exists
