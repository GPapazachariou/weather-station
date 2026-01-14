# App (server)

## What this part is
The server accepts weather readings over TCP, validates them, and stores them in SQLite. It is implemented in `server/app.py` and relies on the protocol rules in `server/protocol.py`.

## Data flow
1) A station client opens a TCP connection and sends a JSON line that contains a batch of readings.
2) `server/app.py` reads one line at a time, parses JSON safely, validates the batch, and enqueues it for a single writer task.
3) The writer task inserts the whole batch in one transaction into the `readings` table.
4) The server replies with `{ "status": "ok", "inserted": N }` or an error reason.

## Key modules and behavior
- `server/app.py` is the TCP server and DB writer.
  - `init_database()` creates the `readings` table with columns `station_id`, `timestamp`, `temperature`, `humidity`, `windspeed`.
  - `_safe_json_loads()` rejects NaN/Infinity at parse time to avoid invalid numeric values.
  - `handle_client()` reads one line per batch, calls `validate_batch()`, enqueues the batch, and returns success or error JSON.
  - `db_writer_task()` keeps one SQLite connection open and applies WAL pragmas to reduce lock contention. It processes batches sequentially to avoid `database is locked` errors.
  - `enqueue_batch()` connects the request/response flow to the writer queue and waits on a future to get the insert count or error.
  - The server uses `asyncio.start_server(..., limit=MAX_LINE_SIZE + 1)` so a client cannot send unbounded line sizes.
  - Graceful shutdown uses a sentinel queue item and lets the writer flush pending batches before closing.
  - Configuration is driven by `DB_PATH` (env) and hard-coded host/port in the file.

- `server/protocol.py` defines protocol rules used by the server.
  - Limits: `MAX_LINE_SIZE` and `MAX_BATCH_SIZE` cap message size and batch count.
  - Validation: `validate_batch()` checks that each reading is a dict with required fields, that timestamps are ISO 8601, that numbers are finite, and that values are within configured ranges.

## Error handling and responses
- Invalid JSON returns `{"status": "error", "reason": "invalid_json: ..."}`.
- Validation errors (timestamp, ranges, non-finite numbers) return `{"status": "error", "reason": "..."}`.
- Oversized lines return `{"status": "error", "reason": "line_too_long"}`.
- Other exceptions return `{"status": "error", "reason": "server_error: ..."}`.

## Related paths
- `server/app.py`
- `server/protocol.py`
- `server/Dockerfile`
- `server/requirements.txt`
