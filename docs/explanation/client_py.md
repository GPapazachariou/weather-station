# station_client/client.py — Explanation

**Related docs:** [00_overview.md](00_overview.md), [app_py.md](app_py.md), [protocol_py.md](protocol_py.md)

## Purpose

The weather station client is an autonomous producer that generates synthetic weather readings and sends them to the TCP server in regular batches. It demonstrates resilience patterns: exponential backoff for reconnection, local buffering when disconnected, and graceful shutdown handling. The client runs indefinitely, tolerating transient network failures and server restarts.

## Key Responsibilities

- Generate deterministic-yet-noisy sensor readings (temperature, humidity, windspeed) at configurable intervals
- Batch multiple readings and serialize to newline-delimited JSON
- Establish TCP connection to server; send batches and await responses
- Detect disconnection and implement exponential backoff + jitter for reconnection
- Buffer readings locally (up to 1000) when disconnected; flush on reconnect
- Parse and validate server responses; log success or error
- Handle graceful shutdown via SIGINT/SIGTERM signals

## Important Blocks

### Block: Configuration Loading

**Where:** `load_config()` function

**What it does:**
1. Define defaults for all parameters (host, port, station ID, batch size, intervals, timeouts)
2. Read environment variables with prefix `WEATHER_STATION_*` (override defaults)
3. Parse command-line arguments (override env vars)
4. Validate ranges: batch sizes > 0, batch_min ≤ batch_max, port 0–65535, timeouts > 0
5. Return dict with all config values

**Inputs:** Environment variables (`WEATHER_STATION_*`); CLI args (`--host`, `--port`, etc.); defaults

**Outputs:** Config dict with validated values

**Why it matters:**
- Supports three-tier config hierarchy (defaults → env → CLI) for flexibility
- Validation early prevents runtime errors
- Easy to test with different configs without code changes

**Failure modes:**
- Invalid CLI arg → argparse prints error and exits
- Port out of range → parser.error("Port must be between 0 and 65535")

### Block: Deterministic Reading Generation

**Where:** `stable_hash_int()`, `mean_for()`, `value_for()`, `generate_reading()`

**What it does:**
1. `stable_hash_int(s)` — Generate stable integer hash from string using SHA256 (first 8 hex chars)
2. `mean_for(station_id, metric, lo, hi)` — Use hash to compute deterministic mean for station+metric
3. `value_for(station_id, metric)` — Fetch mean, add random noise (Gaussian), clamp to valid range
4. `generate_reading(station_id)` — Create one reading with all three metrics; timestamp is current UTC time

**Inputs:** Station ID; metric name; range [lo, hi]

**Outputs:** Single reading dict with all fields

**Why it matters:**
- Deterministic mean ensures readings are consistent across runs (useful for testing/debugging)
- Random noise simulates realistic sensor variation
- Reading includes current timestamp; server validates ISO 8601 format
- Noise parameters tuned per metric (temperature stddev 0.6, humidity 2.5, windspeed 1.2)

**Failure modes:** 
- Hash algorithm could change but is stable within a run
- Random seed not fixed so each run produces different noise (intentional, simulates real variation)

### Block: Batch Generation & Sending

**Where:** `generate_batch()`, `send_batches()` main loop

**What it does:**
1. `generate_batch(station_id, size)` — Generate N readings with current timestamp
2. In `send_batches()`:
   - Generate batch every N seconds (configurable, default 5s)
   - Serialize to JSON array + newline
   - Send via socket; await response
   - Parse response; check status
   - Log success or error

**Inputs:** Station ID; batch size (random between min/max)

**Outputs:** JSON batch sent to server; response received and logged

**Why it matters:** 
- Batching reduces overhead (one JSON array vs N separate sends)
- Regular interval ensures steady data flow
- Response check ensures server processed batch (or client knows to retry)

**Failure modes:**
- write() succeeds but drain() times out → client retries from buffer
- Connection drops during send → batch added to buffer
- Server rejects batch → logged but not retried (data lost from that batch)

### Block: Connection Management & Exponential Backoff

**Where:** Outer loop in `send_batches()`; backoff state variables

**What it does:**
1. Initialize `current_backoff = 1.0` sec, `shutdown = False`
2. Outer infinite loop:
   - Try to connect with `connect_timeout` (default 10s)
   - On success: reset backoff to 1.0 sec, proceed to inner loop
   - On failure: increment backoff, sleep, retry
3. Inner loop (connected state):
   - Generate and send batches every batch_interval
   - On connection drop: break to outer loop (backoff applies)

**Inputs:** Config (base_backoff, max_backoff, jitter, connect_timeout)

**Outputs:** Connection established or retried with delay

**Why it matters:**
- Exponential backoff prevents thundering herd if server is restarting
- Jitter prevents many clients reconnecting simultaneously
- Inner loop logic focuses on sending; connection management is outer loop concern

**Failure modes:**
- Backoff capped at 60s; very long outages take time to detect recovery
- If server is permanently down, client keeps retrying forever (intended)

### Block: Local Buffering & Flush

**Where:** `buffer = deque()`, buffer management in `send_batches()`

**What it does:**
1. Maintain `deque(maxlen=1000)` for buffered readings when disconnected
2. When reconnecting:
   - Before normal batching, flush all buffered batches
   - Send each buffered batch; await response; remove from buffer
   - If disconnect during flush, put batch back and retry later
3. If buffer is full and client is disconnected:
   - New readings are dropped (backpressure; prevents unbounded memory)
   - No error logged (silent drop; assumes transient outage)

**Inputs:** Generated readings; connection state

**Outputs:** Buffered readings persisted in deque; flushed on reconnect

**Why it matters:**
- Handles transient outages gracefully (don't lose data immediately)
- Flush-on-reconnect ensures buffered data is processed (in order)
- Buffer size limit (1000 readings) prevents OOM on prolonged outage
- Silent drop on full buffer is acceptable for non-critical sensor data

**Failure modes:**
- If outage is very long (>1000 readings at 5s intervals ≈ 83 minutes), new readings drop
- If client crashes mid-flush, buffer is lost
- If connection drops during flush, flushed batch is retried (acceptable duplicate-write)

### Block: Response Handling & Acknowledgment

**Where:** In `send_batches()` inner loop, after sending batch

**What it does:**
1. Send batch: `writer.write()` + `writer.drain()`
2. Receive response: `reader.readline()` with response_timeout
3. Parse JSON response
4. Check `response["status"]`:
   - `"ok"`: log success with inserted count; continue
   - `"error"`: log error reason; batch is lost (not retried)
5. On timeout or read error: connection drop, go to outer loop (backoff applies)

**Inputs:** JSON response from server (one line)

**Outputs:** Logged message; decision to continue or reconnect

**Why it matters:**
- Confirmation from server is important; client waits for ACK
- Timeouts trigger reconnect (server is hung or too slow)
- Error responses are logged but not retried (assume validation error, not transient)

**Failure modes:**
- No response within timeout → readline() times out → asyncio.TimeoutError → caught, connection drop
- Malformed response JSON → json.loads() raises → caught, logged, connection drop
- Server says "error" → batch assumed invalid, not buffered for retry

### Block: Signal Handling & Graceful Shutdown

**Where:** `handle_shutdown(signum, frame)` callback; registered with `signal.signal()`

**What it does:**
1. On SIGINT (Ctrl+C) or SIGTERM:
   - Set `shutdown = True`
   - Print shutdown message
2. Main loop checks `shutdown` in condition:
   - Outer loop breaks
   - Close socket
   - Exit normally

**Inputs:** OS signal (SIGINT=2, SIGTERM=15)

**Outputs:** Flag set; outer loop detects and breaks; process exits

**Why it matters:**
- Graceful shutdown allows pending operations to finish
- Client doesn't leave socket half-open
- Avoids leaving connection in TIME_WAIT state

**Failure modes:**
- SIGKILL (9) bypasses handler → no cleanup possible
- Double Ctrl+C may raise exception but process still exits

### Block: Value Generation & Clamping

**Where:** `value_for()`, `clamp()` functions

**What it does:**
1. Fetch deterministic mean for station+metric pair
2. Add Gaussian noise (configurable stddev per metric)
3. Clamp result to valid range [lo, hi]
4. Round to 2 decimal places

**Inputs:** Station ID; metric name (temperature, humidity, windspeed)

**Outputs:** Single float value in valid range

**Why it matters:**
- Realistic noise simulates sensor variation
- Clamping ensures values never exceed valid ranges (even with extreme noise)
- Rounding to 2 decimals mimics real sensor precision

**Failure modes:** 
- Noise could theoretically push value way out of range, but clamp catches it
- Rounding might hide precision (acceptable for demo)

## Data & State

### Global State in send_batches()

```python
buffer = deque()                           # Buffered readings when disconnected
current_backoff = 1.0                      # Current backoff delay (seconds)
shutdown = False                           # Set by signal handler
reader = None                              # TCP reader (per connection)
writer = None                              # TCP writer (per connection)
batch_num = 1                              # Counter for logging
```

### Configuration State

```python
config = {
    "server_host": str,
    "server_port": int,
    "station_id": str,
    "batch_size_min": int,
    "batch_size_max": int,
    "batch_interval": float,
    "response_timeout": float,
    "connect_timeout": float,
    "base_backoff": float,
    "max_backoff": float,
    "backoff_jitter": float,
    "max_buffer_records": int,
    "drain_timeout": float,
}
```

### Noise Parameters (Hardcoded)

```python
# In value_for():
temperature: lo=-10.0, hi=40.0, noise_std=0.6
humidity:    lo=0.0,   hi=100.0, noise_std=2.5
windspeed:   lo=0.0,   hi=50.0,  noise_std=1.2
```

## Control Flow Walkthrough

### Startup & Configuration

```
main():
  ├─ config = load_config()                           // Hierarchy: defaults → env → CLI
  └─ asyncio.run(send_batches(config))
```

### Outer Loop: Connection & Backoff

```
send_batches(config):
  ├─ buffer = deque(maxlen=1000)
  ├─ current_backoff = 1.0
  ├─ signal.signal(SIGINT, handle_shutdown)          // Register handler
  │
  └─ while not shutdown:
       ├─ Try: reader, writer = await asyncio.open_connection(host, port, timeout=10s)
       ├─ On success:
       │   ├─ current_backoff = 1.0                   // Reset backoff
       │   ├─ Flush buffer:
       │   │   └─ for each batch in buffer:
       │   │       ├─ Send + await response
       │   │       └─ Remove from buffer on ACK
       │   └─ Inner loop (connected state)
       │
       └─ On failure:
           ├─ current_backoff = min(current_backoff + increment, 60.0)
           ├─ jitter = random(0, 1.0)
           ├─ Sleep(current_backoff + jitter)
           └─ Retry outer loop
```

### Inner Loop: Batch Generation & Sending

```
send_batches() inner loop (connected):
  └─ while not shutdown:
       ├─ Generate batch (random size between min/max, current timestamp)
       ├─ Serialize to JSON + newline
       ├─ Try: writer.write() + await writer.drain(timeout=5s)
       ├─ Try: response_line = await reader.readline(timeout=5s)
       ├─ Parse response JSON
       ├─ if status == "ok": log success
       ├─ else: log error
       ├─ On timeout/error: raise (caught, go to outer loop)
       └─ Sleep(batch_interval)
```

### Buffer Flush Logic

```
Flush buffered batches on reconnect:
  └─ while buffer and not shutdown:
       ├─ batch = buffer.popleft()
       ├─ Try: send batch, read response
       ├─ On success: continue (batch removed from deque)
       ├─ On timeout/error:
       │   ├─ buffer.appendleft(batch)                // Put back
       │   └─ Raise (go to outer loop, backoff applies)
```

## Interfaces

### Imports & Dependencies

```python
import asyncio              # Async networking
import json                 # JSON serialization
import random               # For noise + random batch size
import os                   # Environment variables
import argparse             # CLI parsing
import hashlib              # For stable_hash_int()
import signal               # For SIGINT/SIGTERM
from collections import deque  # Local buffer
from datetime import datetime, timezone  # Current timestamp
```

### Configuration: Environment Variables & Defaults

```
WEATHER_STATION_HOST            (default: localhost)
WEATHER_STATION_PORT            (default: 12345)
WEATHER_STATION_ID              (default: STATION-001)
WEATHER_STATION_BATCH_MIN       (default: 1)
WEATHER_STATION_BATCH_MAX       (default: 1)
WEATHER_STATION_BATCH_INTERVAL  (default: 5)
WEATHER_STATION_TIMEOUT         (default: 5.0)
WEATHER_STATION_CONNECT_TIMEOUT (default: 10.0)
```

### CLI Arguments

```
--host, --port, --station-id, --batch-min, --batch-max, --batch-interval, --timeout, --connect-timeout
```

### Protocol: Request Format (Client → Server)

```json
[
  {
    "station_id": "STATION-001",
    "timestamp": "2025-01-17T14:30:45.123456Z",
    "temperature": 22.5,
    "humidity": 58.3,
    "windspeed": 4.2
  }
]
```

### Protocol: Response Format (Server → Client)

```json
{"status": "ok", "inserted": 1}
{"status": "error", "reason": "invalid_temperature at index 0 (expected -10..40)"}
```

## Observability & Debugging

### Key Log Messages

```
Weather Station Client: STATION-001
Target: localhost:12345
Batch interval: 5s
Buffer limit: 1000 records

[STATION-001] Connecting to localhost:12345...
[STATION-001] ✓ Connected to server
[STATION-001] Flushing 5 buffered batches...
[STATION-001] ✓ Flushed batch (1 readings)
[STATION-001] ✓ Buffer flushed successfully
[STATION-001] ✓ Batch 1 sent (1 reading) → inserted 1
[STATION-001] ✗ Connection lost during send: <error>
[STATION-001] Sleeping 5.2s before retry...
[STATION-001] ✓ Batch 2 sent (1 reading) → inserted 1

⚠ Shutdown signal received, stopping client...
```

### Common Errors

| Error | Cause | Expected Behavior |
|-------|-------|---|
| Connection refused | Server not running | Exponential backoff; keep retrying |
| Timeout reading response | Server slow or hung | Backoff; reconnect |
| JSON parse error in response | Server response malformed | Log error; reconnect |
| Batch rejected (validation error) | Client sent invalid data (shouldn't happen) | Log error; batch lost (not retried) |
| Buffer full | Disconnected for too long | New readings dropped silently |

### Debugging Tips

- Check if server is running: `telnet localhost 12345` or `nc -zv localhost 12345`
- Monitor logs for "Connecting" messages (frequent reconnects indicate server instability)
- Look for "Buffer full" messages (indicates extended outage)
- Run with custom batch interval: `--batch-interval 1` for faster data flow during testing
- Check station ID in logs to confirm right client is running

---

**Key Takeaway:** The client is resilient and autonomous. It survives network failures, retries with exponential backoff, buffers locally, and flushes on reconnect. The deterministic-plus-noisy reading generation simulates realistic sensor behavior while remaining reproducible for testing.
