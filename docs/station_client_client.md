# station_client/client.py Documentation

## Purpose
- Simulated weather station client that generates synthetic readings and streams newline-delimited JSON batches to the TCP server.
- Dependents: invoked as Docker Compose station services and can be run manually for testing the server ingestion path.

## High-level flow
- Startup: `main()` loads configuration (CLI overrides env overrides defaults) via `load_config()`, then calls `send_batches()`.
- Runtime: `send_batches()` loops forever unless a shutdown signal arrives. It connects to the server, optionally flushes buffered batches, then continuously generates and sends batches, handling responses and buffering on failures.
- Shutdown: SIGINT/SIGTERM set a flag to break loops; connection is closed; buffered batches may remain unsent (warned in logs).

## Key data structures
- Defaults: server host/port, station ID, batch sizing, intervals, timeouts, backoff, jitter, buffer limits (e.g., `DEFAULT_BATCH_SIZE_MIN/MAX`, `DEFAULT_BATCH_INTERVAL`, `DEFAULT_MAX_BUFFER_RECORDS`).
- Buffer: `collections.deque` storing batches awaiting resend when disconnected.
- Backoff state: `current_backoff` with exponential growth capped by `DEFAULT_MAX_BACKOFF`, plus random jitter.
- Message format: batch = list of reading dicts with `station_id`, ISO 8601 `timestamp`, numeric `temperature`, `humidity`, `windspeed`.

## Classes
- None.

## Functions
### Function: load_config()
- Signature: `def load_config()`
- Parameters: none (reads env and CLI args).
- Returns: dict of configuration values (host, port, station_id, batch sizes, intervals, timeouts, backoff limits, buffer settings).
- Side effects: parses CLI args; prints parser errors on invalid user input.
- Errors: parser `.error()` exits on invalid sizes/port/interval/timeouts.
- Notes: Precedence is CLI > env > defaults; validates positive intervals and proper batch bounds.

### Function: stable_hash_int(s)
- Signature: `def stable_hash_int(s)`
- Parameters: `s` string.
- Returns: deterministic int from SHA256 prefix.
- Side effects: none.
- Notes: Used to derive deterministic per-station means.

### Function: clamp(x, lo, hi)
- Signature: `def clamp(x, lo, hi)`
- Parameters: value and bounds.
- Returns: bounded value.
- Side effects: none.

### Function: mean_for(station_id, metric, lo, hi)
- Signature: `def mean_for(station_id, metric, lo, hi)`
- Parameters: station_id, metric name, lower/upper bounds.
- Returns: deterministic mean within range based on hashed station/metric.
- Side effects: none.
- Notes: Ensures each station has a stable characteristic baseline.

### Function: value_for(station_id, metric)
- Signature: `def value_for(station_id, metric)`
- Parameters: station_id, metric string (temperature/humidity/windspeed).
- Returns: float with Gaussian noise around deterministic mean, clamped and rounded.
- Errors: raises ValueError for unknown metric.

### Function: generate_reading(station_id)
- Signature: `def generate_reading(station_id)`
- Parameters: station_id string.
- Returns: dict with station_id, current UTC ISO timestamp, and three metrics.
- Side effects: time access.

### Function: generate_batch(station_id, size)
- Signature: `def generate_batch(station_id, size)`
- Parameters: station_id, batch size int.
- Returns: list of readings created by `generate_reading`.

### Function: send_batches(config)
- Signature: `async def send_batches(config)`
- Parameters: config dict from `load_config()`.
- Returns: None.
- Side effects: network I/O; stdout logging; buffers batches on disconnect.
- Errors: catches `OSError`, `ConnectionError`, `asyncio.TimeoutError`, `BrokenPipeError`, `ConnectionRefusedError`; on these, buffers current batch (dropping oldest if full) and triggers reconnect with backoff. Propagates `CancelledError` only via shutdown flag.
- Important logic:
  - Registers SIGINT/SIGTERM to set shutdown flag.
  - Outer loop reconnects with exponential backoff + jitter.
  - On connect, flushes buffered batches first with timeouts on send/drain/response; failed flush re-buffers and reconnects.
  - Inner loop generates batch of random size within [min,max], sends JSON line, waits for response, logs result, sleeps `batch_interval`.
  - Buffer capped at `DEFAULT_MAX_BUFFER_RECORDS`; oldest dropped when full.
  - Connect timeout and response timeout enforced with `asyncio.wait_for`; writer drain timeout enforced.

### Function: main()
- Signature: `async def main()`
- Parameters: none.
- Returns: None.
- Side effects: loads config, starts `send_batches` coroutine.

## Error handling & edge cases
- Invalid configuration: argparse errors abort startup.
- Connection failures/timeouts: logged; batches buffered; exponential backoff up to max; jitter added.
- Buffer full: oldest batch dropped to avoid unbounded memory.
- Server closes connection mid-send: exception caught, batch buffered, reconnect triggered.
- Shutdown: signal handler sets flag; loops exit; connection closed; warns about unsent buffered batches.

## Performance & scalability notes
- CPU-light; main constraints are network latency and backpressure from server.
- Batching reduces per-request overhead; configurable intervals allow tuning load.
- Buffering permits temporary outages but may drop data when full; max buffer size limits memory.

## How to test this file
- Manual run against local server:
  ```bash
  python station_client/client.py --station-id TEST-001 --batch-min 1 --batch-max 3 --batch-interval 2 --host localhost --port 12345
  ```
- Observe stdout for successful sends and server responses.
- Induce failures (stop server) to see buffering/backoff logs, then restart server to watch buffered flush.
- In Docker Compose, check `docker compose logs station-001` for continuous successful batches.