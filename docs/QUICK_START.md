# Quick Start: Testing Consumer Protocol

## Prerequisites
- Python 3.8+
- asyncio (built-in)
- aiosqlite
- sqlite3 (built-in)

## Option 1: Quick Manual Test (No Docker)

### Terminal 1: Start Server
```bash
cd server
python app.py
```

Expected output:
```
Database initialized: /app/data/weather.db
SQLite pragmas applied: WAL mode, NORMAL sync, 3000ms busy timeout
Database writer task started
Weather Station Server running on 0.0.0.0:12345
Waiting for client connections...
```

### Terminal 2: Test Producer (Ingest)
```bash
# Send a batch of readings
python3 << 'EOF'
import socket
import json
from datetime import datetime, timezone

sock = socket.socket()
sock.connect(("localhost", 12345))

now = datetime.now(timezone.utc).isoformat()
batch = [
    {
        "station_id": "STATION-001",
        "timestamp": now,
        "temperature": 22.5,
        "humidity": 60,
        "windspeed": 5.2
    }
]

sock.send((json.dumps(batch) + "\n").encode())
response = json.loads(sock.recv(1024).decode())
print(f"Producer response: {response}")
sock.close()
EOF
```

Expected response:
```json
{"status": "ok", "inserted": 1}
```

### Terminal 3: Test Consumer (Query)
```bash
# Query list of stations
python3 << 'EOF'
import socket
import json

sock = socket.socket()
sock.connect(("localhost", 12345))

request = {"request": "stations"}
sock.send((json.dumps(request) + "\n").encode())
response = json.loads(sock.recv(1024).decode())
print(f"Stations response: {json.dumps(response, indent=2)}")
sock.close()
EOF
```

Expected response:
```json
{
  "status": "ok",
  "stations": ["STATION-001"]
}
```

---

## Option 2: Automated Tests

### Run Full Test Suite
```bash
python test_consumer.py
```

This runs 8 tests:
1. ✓ Producer batch ingest (backward compatibility)
2. ✓ Consumer: list stations
3. ✓ Consumer: latest reading (all)
4. ✓ Consumer: latest reading (specific station)
5. ✓ Consumer: recent readings (default limit)
6. ✓ Consumer: recent readings (custom limit)
7. ✓ Consumer: recent readings (specific station)
8. ✓ Invalid consumer request

Expected output:
```
==================================================
Weather Station Consumer Tests
==================================================
Server: localhost:12345

=== Test 1: Producer Batch Ingest ===
  Response: {...}
  ✓ Producer batch ingest successful: 1 readings inserted

=== Test 2: Consumer Request - Stations ===
  Response: {...}
  ✓ Consumer stations request successful: ['STATION-001']

... [more tests]

==================================================
Results: 8/8 tests passed
==================================================
```

---

## Option 3: Docker Deployment

### With Docker Compose (already configured)

1. **Ensure docker-compose.yml has port mapping:**
```yaml
services:
  server:
    build: ./server
    ports:
      - "12345:12345"
    volumes:
      - ./data:/app/data
```

2. **Start services:**
```bash
docker-compose up -d
```

3. **Test from host:**
```bash
python test_consumer.py
```

---

## Common Test Scenarios

### Scenario 1: Monitor Live Readings
```python
import socket, json, time

sock = socket.socket()
sock.connect(("localhost", 12345))

while True:
    request = {"request": "latest"}
    sock.send((json.dumps(request) + "\n").encode())
    response = json.loads(sock.recv(1024).decode())
    
    if response.get("reading"):
        r = response["reading"]
        print(f"{r['station_id']}: {r['temperature']}°C (humidity: {r['humidity']}%)")
    
    time.sleep(5)
```

### Scenario 2: Export Recent Data
```python
import socket, json, csv

sock = socket.socket()
sock.connect(("localhost", 12345))

request = {"request": "recent", "limit": 100}
sock.send((json.dumps(request) + "\n").encode())
response = json.loads(sock.recv(4096).decode())

with open("export.csv", "w") as f:
    writer = csv.DictWriter(f, fieldnames=["station_id", "timestamp", "temperature", "humidity", "windspeed"])
    writer.writeheader()
    writer.writerows(response["readings"])

print(f"Exported {len(response['readings'])} readings")
```

### Scenario 3: Station Comparison
```python
import socket, json

sock = socket.socket()
sock.connect(("localhost", 12345))

# Get all stations
sock.send((json.dumps({"request": "stations"}) + "\n").encode())
stations = json.loads(sock.recv(1024).decode())["stations"]

print("Latest readings by station:")
for station in stations:
    req = {"request": "latest", "station_id": station}
    sock.send((json.dumps(req) + "\n").encode())
    resp = json.loads(sock.recv(1024).decode())
    
    if resp["reading"]:
        r = resp["reading"]
        print(f"  {station}: {r['temperature']}°C")
```

---

## Troubleshooting

### "Connection refused"
- Is server running? Check `python app.py`
- Wrong port? Default is 12345 (check protocol.py)

### "No such table: readings"
- Database needs initialization. Start server first.
- Data persists in `/app/data/weather.db` (or `$DB_PATH`)

### "Timeout" on consumer request
- Increase socket timeout: `sock.settimeout(10.0)`
- Check server logs for errors
- Verify database is accessible: `ls -la /app/data/`

### Producer works, Consumer fails
- Verify aiosqlite.Row factory is set correctly (it is by default in new code)
- Check JSON response: should be valid dict with "status" key

### All requests return "unknown_request"
- Verify JSON has "request" key (case-sensitive)
- Typo in request type? ("stations", "latest", "recent")

---

## Performance Notes

### Response Times
- **stations:** ~5-50ms (depends on unique station count)
- **latest:** ~5-20ms (one row)
- **recent(limit=50):** ~10-50ms (fifty rows)
- **recent(limit=500):** ~50-200ms (five hundred rows)

### Limits
- **Max limit:** 500 (larger values clamped)
- **Line size:** 65KB (MAX_LINE_SIZE from protocol.py)
- **Concurrent clients:** Unlimited (asyncio handles many)

### Best Practices
1. **Reuse connections:** Keep socket open for multiple requests
2. **Batch operations:** Query multiple stations in sequence
3. **Set timeouts:** Use `sock.settimeout(30)` to prevent hangs
4. **Close connections:** Always `sock.close()` when done

---

## Files Modified
- **server/app.py:** Added `handle_consumer_request()` + protocol multiplexing
- **test_consumer.py:** Created comprehensive test suite
- **docs:** CONSUMER_API.md, CONSUMER_IMPLEMENTATION.md, IMPLEMENTATION_DETAILS.md, ARCHITECTURE.md

## Files NOT Modified
- ✓ protocol.py
- ✓ db_writer_task()
- ✓ validate_batch()
- ✓ enqueue_batch()
- ✓ docker-compose.yml (already has correct port mapping)
- ✓ Dockerfile
