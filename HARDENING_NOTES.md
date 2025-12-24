# Server Hardening Implementation

## Summary of Changes

This document describes the 4 hardening improvements made to the asyncio TCP weather server.

### Issue 1: MAX_LINE_SIZE Enforcement

**Implementation:**
- `asyncio.start_server()` now called with `limit=MAX_LINE_SIZE + 1`
- Prevents unbounded line buffering; oversized lines trigger `asyncio.LimitOverrunError`
- Server sends `{"status": "error", "reason": "line_too_long"}` and closes connection on overflow
- `MAX_LINE_SIZE` imported from `protocol.py` (single source of truth: 65536 bytes)

**File:** `server/app.py`, `handle_client()` function
**Manual Test:**
```powershell
# Test 1: Send a line exceeding MAX_LINE_SIZE
# Create a batch with a very long station_id to exceed 65536 bytes
# Should receive "line_too_long" error and connection should close
python -c "
import socket
import json
batch = [{'station_id': 'A' * 100000, 'timestamp': '2025-01-01T00:00:00Z', 'temperature': 20, 'humidity': 50, 'windspeed': 5}]
s = socket.socket()
s.connect(('localhost', 12345))
s.sendall((json.dumps(batch) + '\n').encode())
print(s.recv(1024).decode())
s.close()
"
```

---

### Issue 2: Timestamp Format and Numeric Finiteness

**Implementation:**

#### Timestamp Validation (`protocol.py`)
- New helper: `validate_timestamp(timestamp_str)`
- Accepts ISO 8601: `YYYY-MM-DDTHH:MM:SSZ` or with explicit timezone
- Normalizes trailing `Z` → `+00:00` for `datetime.fromisoformat()` compatibility
- Rejects invalid formats with clear error: `"invalid_timestamp"`

#### Numeric Finiteness (`protocol.py`)
- New helper: `validate_finite_number(value, field_name)`
- Uses `math.isfinite()` to reject NaN, Infinity, -Infinity
- Rejects booleans explicitly (bool is subclass of int)
- Error: `"non_finite_number: field_name=NaN"`

#### Safe JSON Parsing (`app.py`)
- New helper: `_safe_json_loads(data)`
- Uses `json.loads(..., parse_constant=lambda x: ...throw ValueError())`
- Rejects `NaN`, `Infinity`, `-Infinity` in JSON with error: `"non_finite constant in JSON"`

**Files:** `server/protocol.py`, `server/app.py`
**Manual Tests:**

```powershell
# Test 2a: Send batch with invalid timestamp format
python -c "
import socket
import json
batch = [{'station_id': 'TEST', 'timestamp': 'not-a-timestamp', 'temperature': 20, 'humidity': 50, 'windspeed': 5}]
s = socket.socket()
s.connect(('localhost', 12345))
s.sendall((json.dumps(batch) + '\n').encode())
print(s.recv(1024).decode())
s.close()
"

# Test 2b: Send batch with NaN (should be rejected by JSON or validation)
# Note: Python's json module doesn't natively serialize NaN, but we test parsing
python -c "
import socket
# Manually craft JSON with NaN constant
bad_json = '[{\"station_id\": \"TEST\", \"timestamp\": \"2025-01-01T00:00:00Z\", \"temperature\": NaN, \"humidity\": 50, \"windspeed\": 5}]'
s = socket.socket()
s.connect(('localhost', 12345))
s.sendall((bad_json + '\n').encode())
print(s.recv(1024).decode())
s.close()
"

# Test 2c: Valid ISO 8601 with 'Z' suffix (should work)
python -c "
import socket
import json
batch = [{'station_id': 'VALID', 'timestamp': '2025-01-01T12:30:45Z', 'temperature': 20.5, 'humidity': 75.0, 'windspeed': 3.2}]
s = socket.socket()
s.connect(('localhost', 12345))
s.sendall((json.dumps(batch) + '\n').encode())
print(s.recv(1024).decode())
s.close()
"
```

---

### Issue 3: Hardened Writer Queue Futures

**Implementation:**
- Futures created with `asyncio.get_running_loop().create_future()` (typed to the correct loop)
- Before `set_result()` / `set_exception()`:
  - Check `if not future.done()` to avoid double-completion
  - Check `if not future.cancelled()` to skip cancelled futures (client disconnected)
- Sentinel `(None, None)` item placed in queue to signal writer task to exit
- Writer task breaks on sentinel instead of checking `shutdown_event`

**File:** `server/app.py`, `enqueue_batch()` and `db_writer_task()`
**Why It Matters:**
- Prevents `InvalidStateError: cannot set result on cancelled future`
- Handles rapid client disconnections without crashing writer task
- Ensures no pending futures are left in invalid state

---

### Issue 4: Graceful Shutdown

**Implementation:**
- Signal handlers (SIGINT, SIGTERM) call `shutdown_handler()`
- Shutdown sequence:
  1. Close server socket (stop accepting new connections)
  2. Wait for server socket to close (`await server.wait_closed()`)
  3. Set `shutdown_event` and place sentinel in writer queue
  4. Wait for writer task to flush remaining queue items and exit
  5. SQLite connection automatically closes in finally block of writer task
- 10-second timeout for writer task exit; cancels if it hangs

**File:** `server/app.py`, `shutdown_handler()` and updated `main()`
**Manual Test:**
```powershell
# Test 3: Start server, send a few batches, then Ctrl+C
# Observe: existing batches should flush, no "database locked" errors
python server/app.py &
sleep 2
python station_client/client.py &
sleep 5
# Press Ctrl+C on server terminal
# Expected output:
#   "Received signal X, shutting down..."
#   "Closing server socket..."
#   "Flushing remaining writes..."
#   "Database connection closed"
#   "Server stopped"
```

---

## Validation Tests

Run these to verify all hardening:

```powershell
# Terminal 1: Start the server
cd weather-station
python server/app.py

# Terminal 2: Run client (should work fine with valid data)
cd weather-station
python station_client/client.py

# Terminal 3: Manual protocol tests
cd weather-station

# Test oversized line
python -c "
import socket, json
batch = [{'station_id': 'A' * 100000, 'timestamp': '2025-01-01T00:00:00Z', 'temperature': 20, 'humidity': 50, 'windspeed': 5}]
s = socket.socket()
s.connect(('localhost', 12345))
s.sendall((json.dumps(batch) + '\n').encode())
print('Response:', s.recv(1024).decode())
s.close()
"

# Test invalid timestamp
python -c "
import socket, json
batch = [{'station_id': 'TEST', 'timestamp': 'bad-format', 'temperature': 20, 'humidity': 50, 'windspeed': 5}]
s = socket.socket()
s.connect(('localhost', 12345))
s.sendall((json.dumps(batch) + '\n').encode())
print('Response:', s.recv(1024).decode())
s.close()
"

# Test NaN in JSON
python -c "
import socket
bad_json = '[{\"station_id\": \"TEST\", \"timestamp\": \"2025-01-01T00:00:00Z\", \"temperature\": NaN, \"humidity\": 50, \"windspeed\": 5}]'
s = socket.socket()
s.connect(('localhost', 12345))
s.sendall((bad_json + '\n').encode())
print('Response:', s.recv(1024).decode())
s.close()
"

# Test graceful shutdown
# Start server, send a batch, then Ctrl+C
# Expected: clean exit, no errors
```

---

## Acceptance Criteria (All Met)

✅ **Oversized lines** cannot cause unbounded memory use (stream limit enforced via `limit=MAX_LINE_SIZE+1`)

✅ **Records with invalid timestamps or NaN/Infinity** are rejected before insertion (validated in `validate_batch()` and `validate_timestamp()`, NaN/Infinity rejected in JSON parsing)

✅ **Cancellation/disconnect** does not produce "invalid state" errors (futures guarded with `done()` / `cancelled()` checks)

✅ **Ctrl+C / SIGTERM** exits without hanging, closes socket, closes SQLite cleanly (signal handlers, graceful shutdown sequence, sentinel in queue)

---

## Files Modified

1. **`server/protocol.py`**
   - Added `math` import
   - Added `datetime` import
   - New: `validate_timestamp()`
   - New: `validate_finite_number()`
   - Updated: `validate_batch()` to use helpers

2. **`server/app.py`**
   - Added `signal`, `sys` imports
   - New globals: `server`, `writer_task`, `_WRITER_SENTINEL`
   - New: `_safe_json_loads()`
   - New: `shutdown_handler()`
   - Updated: `db_writer_task()` with sentinel, future guards
   - Updated: `enqueue_batch()` with loop.create_future(), CancelledError handling
   - Updated: `handle_client()` with LimitOverrunError handling, safe JSON, improved comments
   - Updated: `main()` with signal handlers, graceful shutdown sequence

---
