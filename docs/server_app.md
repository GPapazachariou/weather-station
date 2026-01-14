# server/app.py Documentation

## Purpose
- Asyncio TCP server that receives weather data batches, validates them, and persists them into SQLite.
- Consumed by Docker Compose as the `server` service; weather station clients connect over TCP; the web dashboard reads the resulting SQLite DB.

## High-level flow
- Startup: `main()` initializes the SQLite schema, sets up an asyncio queue and shutdown event, starts a single `db_writer_task()`, then starts `asyncio.start_server()` on host/port with line length limit.
- Runtime: For each TCP connection, `handle_client()` reads newline-delimited JSON batches, validates via `validate_batch()`, enqueues the batch, waits for the writer result, and responds with JSON status.
- Shutdown: Signal handlers set `shutdown_event`, close the listening socket, push a sentinel to the queue, flush remaining writes, and close the DB connection.

## Key data structures
- Constants: `DB_FILE` (SQLite path, env `DB_PATH`), `HOST`/`PORT` (bind target), `_WRITER_SENTINEL` for graceful writer exit.
- Global state: `write_queue` (asyncio.Queue of `(batch, future)`), `shutdown_event`, `server`, `writer_task`, `db_connection`.
- Message format: newline-delimited JSON; each line is a batch (list of reading dicts).

## Classes
- None.

## Functions
### Function: _safe_json_loads(data: str)
- Signature: `_safe_json_loads(data: str)`
- Parameters: `data` string of JSON text.
- Returns: parsed Python object.
- Side effects: none.
- Errors: raises ValueError on non-finite constants (NaN/Infinity) via `parse_constant` hook.
- Notes: Ensures JSON cannot sneak in non-finite numbers that later bypass validation.

### Function: init_database()
- Signature: `async def init_database()`
- Parameters: none.
- Returns: None.
- Side effects: creates parent directory, opens SQLite, creates `readings` table if missing, commits.
- Errors: propagates SQLite errors.
- Notes: Ensures schema exists before serving traffic.

### Function: db_writer_task()
- Signature: `async def db_writer_task()`
- Parameters: none (uses globals).
- Returns: None.
- Side effects: opens persistent `db_connection`; sets WAL, `synchronous=NORMAL`, `busy_timeout=3000`; dequeues batches; writes via `executemany`; commits; sets results on futures; closes DB on exit.
- Errors: exceptions inside batch write propagate to the associated future; unexpected exceptions still attempt graceful close in `finally`.
- Notes: Single writer prevents SQLite lock contention; checks `future.done()`/`future.cancelled()` before completing; flushes remaining items on shutdown; sentinel `(None, None)` breaks the loop.

### Function: enqueue_batch(batch)
- Signature: `async def enqueue_batch(batch)`
- Parameters: `batch` list of validated readings.
- Returns: int count of inserted rows (from writer future) or 0 if batch empty.
- Side effects: puts `(batch, future)` into `write_queue`.
- Errors: may raise `asyncio.CancelledError` if caller is cancelled (e.g., client disconnect); underlying writer exceptions propagate when awaiting the future.
- Notes: Future is created on the current running loop to avoid cross-loop issues.

### Function: handle_client(reader, writer)
- Signature: `async def handle_client(reader, writer)`
- Parameters: `reader`/`writer` from asyncio stream server.
- Returns: None.
- Side effects: network I/O; enqueues DB writes.
- Errors: Handles `LimitOverrunError` (line too long → error response + close); `JSONDecodeError`; `ValueError` from validation; generic exceptions; on cancellation, propagates to allow cleanup.
- Logic notes: per-line read; decode UTF-8; `_safe_json_loads` to reject NaN; `validate_batch()` for schema/range; all-or-nothing enqueue; responds with JSON `status` and inserted count or error reason; closes connection in `finally`.

### Function: shutdown_handler(signum)
- Signature: `async def shutdown_handler(signum)`
- Parameters: `signum` signal number.
- Returns: None.
- Side effects: logs, closes server socket, sets `shutdown_event`.
- Notes: Triggered by SIGTERM/SIGINT when supported.

### Function: main()
- Signature: `async def main()`
- Parameters: none.
- Returns: None.
- Side effects: initializes DB; creates queue/event; starts writer task; starts TCP server with `limit=MAX_LINE_SIZE+1`; registers signal handlers (when available); on shutdown closes server, signals writer, waits up to 10s for flush.
- Errors: unhandled exceptions bubble to runtime; KeyboardInterrupt caught to allow clean exit.

## Error handling & edge cases
- Invalid JSON: reply `{"status":"error","reason":"invalid_json: ..."}`; do not insert.
- Validation failures (timestamps, ranges, batch size, finite numbers, missing fields, overlong line): reply with `status:error` and reason; no insert.
- Oversized line (> `MAX_LINE_SIZE`): `line_too_long` response then connection closed.
- Client disconnects/cancellations: propagate `CancelledError`; writer future is cancelled guard-safe.
- DB contention: mitigated via single writer, WAL, busy timeout; all-or-nothing transaction per batch.
- Shutdown: sentinel flushes queue; remaining writes committed before closing.

## Performance & scalability notes
- Single-writer queue avoids SQLite lock storms but caps write throughput; adequate for moderate load.
- Batch insert via `executemany` reduces commit overhead.
- `MAX_LINE_SIZE` and `MAX_BATCH_SIZE` limit memory and validation cost.
- WAL + `busy_timeout` improve concurrent read/write (web + server), but SQLite remains a single-host solution.

## How to test this file
- Unit/protocol sanity: run server and send a valid batch via netcat/socket; expect `status:ok` and DB rows.
- Invalid cases: send bad JSON, NaN, bad timestamp, overlong line; expect `status:error` and no inserts.
- Graceful shutdown: start server, send data, Ctrl+C; confirm logs show flush and `Database connection closed`.
- Commands (from repo root):
  ```bash
  python server/app.py
  # in another shell send data
  python - <<'PY'
  import socket, json
  s=socket.socket(); s.connect(('localhost',12345))
  batch=[{"station_id":"TEST","timestamp":"2025-01-01T00:00:00Z","temperature":20,"humidity":50,"windspeed":5}]
  s.sendall((json.dumps(batch)+'\n').encode()); print(s.recv(1024)); s.close()
  PY
  ```