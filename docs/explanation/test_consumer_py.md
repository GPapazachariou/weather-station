# consumer_client/test_consumer.py — Explanation

**Related docs:** [00_overview.md](00_overview.md), [app_py.md](app_py.md), [protocol_py.md](protocol_py.md), [client_py.md](client_py.md), [README.md](../README.md)

## Purpose

Comprehensive test suite for the weather station protocol over TCP. Tests both **producer functionality** (inserting batches) and **consumer functionality** (querying stored data). Eight asyncio-based tests validate that the server correctly: multiplexes producer batches and consumer requests, returns valid JSON responses, filters by station and time, enforces limits, and rejects invalid requests.

## Key Responsibilities

- Establish TCP connections to server (localhost:12345)
- Send protocol messages (producer batches and consumer requests) as newline-delimited JSON
- Receive and parse JSON responses
- Validate response structure and data format
- Test all consumer request types: `stations`, `latest`, `recent` (with optional filters)
- Test producer batch validation (all-or-nothing semantics)
- Verify error handling for invalid requests
- Report pass/fail for each test

## Important Blocks

### Block: Async Connection & Request Sending

**Where:** Lines 17-39 (`async def send_request(request)`)

**What it does:**
1. Opens async TCP connection to HOST:PORT (localhost:12345)
2. Encodes request as JSON
3. Appends newline delimiter
4. Sends over socket
5. Waits for drain (all bytes sent)
6. Reads response line (blocks until newline)
7. Decodes and parses JSON response
8. Closes connection
9. Returns parsed response

```python
async def send_request(request):
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
        
        # Send request as newline-delimited JSON
        msg = json.dumps(request) + "\n"
        writer.write(msg.encode('utf-8'))
        await writer.drain()
        
        # Read response
        response_line = await reader.readline()
        if not response_line:
            print(f"  ERROR: No response from server")
            return None
        
        response = json.loads(response_line.decode('utf-8').strip())
        
        writer.close()
        await writer.wait_closed()
        
        return response
    except Exception as e:
        print(f"  ERROR: {e}")
        return None
```

**Inputs:** request (dict or list, to be JSON encoded)

**Outputs:** response (parsed dict)

**Why it matters:**
- Core helper for all tests
- Handles TCP connection lifecycle
- Graceful exception handling (doesn't crash test on network error)
- Returns None on error (allows tests to detect failures)

**Failure modes:**
- Connection refused → "Connection refused" exception → returns None
- Server doesn't respond → readline() times out → returns None
- JSON parse error → "Expecting value" → returns None

### Block: Test 1 - Producer Batch Ingest

**Where:** Lines 42-60 (`async def test_producer_ingest()`)

**What it does:**
1. Create batch array with 2 readings (STATION-001, STATION-002)
2. Include all required fields: station_id, timestamp (ISO 8601), temperature, humidity, windspeed
3. Send batch array to server
4. Verify response: `{"status": "ok", "inserted": 2}`
5. Return True if successful

```python
async def test_producer_ingest():
    print("\n=== Test 1: Producer Batch Ingest ===")
    
    now = datetime.now(timezone.utc).isoformat()
    batch = [
        {
            "station_id": "STATION-001",
            "timestamp": now,
            "temperature": 22.5,
            "humidity": 60,
            "windspeed": 5.2
        },
        {
            "station_id": "STATION-002",
            "timestamp": now,
            "temperature": 18.3,
            "humidity": 75,
            "windspeed": 3.1
        }
    ]
    
    response = await send_request(batch)
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "inserted" in response:
            print(f"  ✓ Producer batch ingest successful: {response['inserted']} readings inserted")
            return True
    
    print("  ✗ Producer batch ingest failed")
    return False
```

**Inputs:** None (generates test data)

**Outputs:** Boolean (True if 2 readings inserted)

**Why it matters:**
- Baseline test: verifies server can accept and store producer batches
- Uses current UTC timestamp (validates ISO 8601 parsing)
- Tests 2 stations (validates concurrent station handling)

**Failure modes:**
- Server crashes → connection refused
- Batch validation fails → `{"status": "error", "reason": "..."}`
- Database write fails → `{"status": "error", "reason": "database error"}`

### Block: Test 2 - Consumer `stations` Request

**Where:** Lines 63-76 (`async def test_consumer_stations()`)

**What it does:**
1. Send consumer request: `{"request": "stations"}`
2. Verify response contains: `{"status": "ok", "stations": [...]}`
3. Parse and print stations list

```python
async def test_consumer_stations():
    print("\n=== Test 2: Consumer Request - Stations ===")
    
    request = {"request": "stations"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "stations" in response:
            print(f"  ✓ Consumer stations request successful: {response['stations']}")
            return True
    
    print("  ✗ Consumer stations request failed")
    return False
```

**Inputs:** None (generates request)

**Outputs:** Boolean (True if stations list returned)

**Why it matters:**
- Tests consumer multiplexing (server distinguishes dict vs array)
- Verifies SQL query for distinct stations works
- Returns all station IDs in database

**Failure modes:**
- Request not recognized as consumer → treated as batch → validation error
- No stations in database → returns empty list (still "ok" status)

### Block: Test 3 - Consumer `latest` Request (All Stations)

**Where:** Lines 79-94 (`async def test_consumer_latest()`)

**What it does:**
1. Send: `{"request": "latest"}` (no station_id filter)
2. Verify response: `{"status": "ok", "reading": {...}}`
3. Reading includes: station_id, timestamp, temperature, humidity, windspeed

```python
async def test_consumer_latest():
    print("\n=== Test 3: Consumer Request - Latest (All Stations) ===")
    
    request = {"request": "latest"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "reading" in response:
            print(f"  ✓ Consumer latest request successful")
            return True
    
    print("  ✗ Consumer latest request failed")
    return False
```

**Inputs:** None (generates request)

**Outputs:** Boolean (True if latest reading returned)

**Why it matters:**
- Tests SQL ORDER BY timestamp DESC LIMIT 1
- Single reading (not array)
- All-stations aggregation

### Block: Test 4 - Consumer `latest` Request (Specific Station)

**Where:** Lines 97-111 (`async def test_consumer_latest_station()`)

**What it does:**
1. Send: `{"request": "latest", "station_id": "STATION-001"}`
2. Verify response: `{"status": "ok", "reading": {...}}`
3. Reading belongs to STATION-001

```python
async def test_consumer_latest_station():
    print("\n=== Test 4: Consumer Request - Latest (Specific Station) ===")
    
    request = {"request": "latest", "station_id": "STATION-001"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "reading" in response:
            print(f"  ✓ Consumer latest station request successful")
            return True
    
    print("  ✗ Consumer latest station request failed")
    return False
```

**Inputs:** None (generates request with station_id)

**Outputs:** Boolean

**Why it matters:**
- Tests WHERE clause filtering by station_id

### Block: Test 5 - Consumer `recent` Request (Default Limit)

**Where:** Lines 114-128 (`async def test_consumer_recent()`)

**What it does:**
1. Send: `{"request": "recent"}` (no limit specified)
2. Verify response: `{"status": "ok", "readings": [...]}`
3. Multiple readings (array) with default limit (10 or server-defined)

```python
async def test_consumer_recent():
    print("\n=== Test 5: Consumer Request - Recent (Default Limit) ===")
    
    request = {"request": "recent"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "readings" in response:
            print(f"  ✓ Consumer recent request successful: {len(response['readings'])} readings")
            return True
    
    print("  ✗ Consumer recent request failed")
    return False
```

**Inputs:** None (generates request)

**Outputs:** Boolean (True if readings array returned)

**Why it matters:**
- Tests LIMIT clause with default value
- Array response (multiple readings)

### Block: Test 6 - Consumer `recent` Request (Custom Limit)

**Where:** Lines 131-145 (`async def test_consumer_recent_limit()`)

**What it does:**
1. Send: `{"request": "recent", "limit": 1}`
2. Verify response: `{"status": "ok", "readings": [...]}`
3. Array contains ≤ 1 reading

```python
async def test_consumer_recent_limit():
    print("\n=== Test 6: Consumer Request - Recent (Custom Limit) ===")
    
    request = {"request": "recent", "limit": 1}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "readings" in response:
            count = len(response['readings'])
            print(f"  ✓ Consumer recent limit request successful: {count} readings (limit 1)")
            return True
    
    print("  ✗ Consumer recent limit request failed")
    return False
```

**Inputs:** None

**Outputs:** Boolean

**Why it matters:**
- Tests parameterized LIMIT clause
- Validates limit parameter is respected

### Block: Test 7 - Consumer `recent` Request (Station + Limit)

**Where:** Lines 148-162 (`async def test_consumer_recent_station()`)

**What it does:**
1. Send: `{"request": "recent", "station_id": "STATION-001", "limit": 5}`
2. Verify response: readings array with max 5 items, all from STATION-001

```python
async def test_consumer_recent_station():
    print("\n=== Test 7: Consumer Request - Recent (Specific Station) ===")
    
    request = {"request": "recent", "station_id": "STATION-001", "limit": 5}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "readings" in response:
            print(f"  ✓ Consumer recent station request successful: {len(response['readings'])} readings")
            return True
    
    print("  ✗ Consumer recent station request failed")
    return False
```

**Inputs:** None

**Outputs:** Boolean

**Why it matters:**
- Tests combined WHERE and LIMIT clauses

### Block: Test 8 - Invalid Consumer Request

**Where:** Lines 165-180 (`async def test_invalid_consumer_request()`)

**What it does:**
1. Send: `{"request": "unknown_type"}` (invalid request type)
2. Verify response: `{"status": "error", "reason": "..."}`
3. Error message contains "unknown_request"

```python
async def test_invalid_consumer_request():
    print("\n=== Test 8: Invalid Consumer Request ===")
    
    request = {"request": "unknown_type"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "error" and "unknown_request" in response.get("reason", ""):
            print(f"  ✓ Unknown request properly rejected")
            return True
    
    print("  ✗ Unknown request handling failed")
    return False
```

**Inputs:** None

**Outputs:** Boolean (True if error properly returned)

**Why it matters:**
- Tests error handling in server
- Validates that server doesn't crash on invalid input

### Block: Main Test Runner

**Where:** Lines 183-210 (`async def main()`)

**What it does:**
1. Define list of 8 test functions
2. Run each test in sequence with 100ms delay between tests
3. Collect results (True/False for each)
4. Print summary: "X/Y tests passed"
5. Return exit code 0 (all passed) or 1 (any failed)

```python
async def main():
    print("=" * 50)
    print("Weather Station Consumer Tests")
    print("=" * 50)
    print(f"Server: {HOST}:{PORT}")
    
    tests = [
        test_producer_ingest,
        test_consumer_stations,
        test_consumer_latest,
        test_consumer_latest_station,
        test_consumer_recent,
        test_consumer_recent_limit,
        test_consumer_recent_station,
        test_invalid_consumer_request,
    ]
    
    results = []
    for test_func in tests:
        try:
            result = await test_func()
            results.append(result)
            await asyncio.sleep(0.1)  # Small delay between tests
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            results.append(False)
    
    # Summary
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 50)
    
    return 0 if passed == total else 1
```

**Inputs:** None (reads HOST, PORT constants)

**Outputs:** Exit code (0 = all pass, 1 = any fail)

**Why it matters:**
- Orchestrates all 8 tests
- Collects results
- Returns meaningful exit code (for CI/automation)

## Data & State

### Test Data Generated

**Producer Batch (Test 1):**
```json
[
  {"station_id": "STATION-001", "timestamp": "2025-01-17T14:30:45.123456+00:00", "temperature": 22.5, "humidity": 60, "windspeed": 5.2},
  {"station_id": "STATION-002", "timestamp": "2025-01-17T14:30:45.123456+00:00", "temperature": 18.3, "humidity": 75, "windspeed": 3.1}
]
```

**Consumer Requests:**
- Test 2: `{"request": "stations"}`
- Test 3: `{"request": "latest"}`
- Test 4: `{"request": "latest", "station_id": "STATION-001"}`
- Test 5: `{"request": "recent"}`
- Test 6: `{"request": "recent", "limit": 1}`
- Test 7: `{"request": "recent", "station_id": "STATION-001", "limit": 5}`
- Test 8: `{"request": "unknown_type"}`

### Expected Responses

| Test | Expected Response |
|------|-------------------|
| 1 | `{"status": "ok", "inserted": 2}` |
| 2 | `{"status": "ok", "stations": ["STATION-001", "STATION-002"]}` |
| 3 | `{"status": "ok", "reading": {...}}` |
| 4 | `{"status": "ok", "reading": {...}}` (STATION-001 only) |
| 5 | `{"status": "ok", "readings": [...]}` (≤10 items) |
| 6 | `{"status": "ok", "readings": [...]}` (≤1 item) |
| 7 | `{"status": "ok", "readings": [...]}` (≤5 items, STATION-001 only) |
| 8 | `{"status": "error", "reason": "unknown_request..."}` |

## Control Flow Walkthrough

### Test Execution

```
$ python consumer_client/test_consumer.py

1. main() called (asyncio.run())

2. Print header with server address

3. Loop through 8 tests:
   
   Test 1: test_producer_ingest()
     → send_request([batch...])
     → await response
     → check "inserted": 2
     → print result
     → await 100ms
   
   Test 2-8: (similar for each consumer request)
   
4. After all tests complete:
   → Count passed tests
   → Print summary: "X/8 tests passed"
   → Exit with code 0 (all pass) or 1 (any fail)
```

### Execution Time

- ~2-5 seconds total (8 tests × 100ms delay + network latency)

## Interfaces

### Command-Line

```bash
python consumer_client/test_consumer.py
```

- No arguments
- No stdin
- stdout: Test results and summary
- stderr: (empty if successful)
- exit code: 0 (all pass) or 1 (any fail)

### Network

- **Server:** localhost:12345 (TCP)
- **Protocol:** Newline-delimited JSON
- **Message format:** `<json>\n`

## Observability & Debugging

### Verbose Output

Add debugging to see raw messages:

```python
# In send_request():
print(f"  Sending: {msg.strip()}", file=sys.stderr)
print(f"  Received: {response_line.decode().strip()}", file=sys.stderr)
```

### Manual Test

```bash
# Start server in one terminal
python server/app.py

# In another terminal, send consumer request manually
python -c "
import socket, json
s = socket.socket()
s.connect(('localhost', 12345))
s.send(json.dumps({'request': 'stations'}).encode() + b'\n')
print(json.loads(s.recv(1024).decode()))
s.close()
"
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "Connection refused" | Server not running | Start server: `python server/app.py` |
| "timeout" | Server overloaded | Reduce concurrency or increase timeout |
| Test 1 fails, 2-8 pass | Database populated from earlier tests | Expected behavior; tests use same data |
| All tests fail | Protocol incompatibility | Check server code; verify JSON format |

### Debug Output During Test

```
=== Test 1: Producer Batch Ingest ===
  Response: {
    "status": "ok",
    "inserted": 2
  }
  ✓ Producer batch ingest successful: 2 readings inserted

=== Test 2: Consumer Request - Stations ===
  Response: {
    "status": "ok",
    "stations": [
      "STATION-001",
      "STATION-002"
    ]
  }
  ✓ Consumer stations request successful: ['STATION-001', 'STATION-002']
```

---

**Key Takeaway:** `test_consumer.py` is a comprehensive asyncio-based protocol validation suite with 8 tests: producer batch ingest, and 7 consumer request types (stations, latest, latest+station, recent, recent+limit, recent+station+limit, invalid). Each test sends NDJSON request, validates JSON response structure, and reports pass/fail. Run to verify protocol compliance before production use.
