# consumer_client/test_api.py — Explanation

**Related docs:** [00_overview.md](00_overview.md), [web_web_py.md](web_web_py.md), [README.md](../README.md)

## Purpose

A quick sanity check script to verify that the weather station API is working correctly. It tests two key components: (1) **database integrity** — checks that SQLite database exists and contains readings, and (2) **Flask API** — provides instructions for testing the HTTP endpoints (`/api/stations`, `/api/stats`, `/api/readings`). Run this script to confirm the system is operational before running comprehensive tests.

## Key Responsibilities

- Open SQLite database at `data/weather.db`
- Query database for station IDs (verify at least one exists)
- Count total readings inserted
- Fetch and display a sample reading
- Print human-readable test instructions
- Verify Flask can be started and endpoints can be accessed

## Important Blocks

### Block: Database Connection & Station Query

**Where:** Lines 12-15

**What it does:**
1. Open SQLite connection to `data/weather.db`
2. Execute SQL query: `SELECT DISTINCT station_id FROM readings ORDER BY station_id`
3. Fetch all unique station IDs (each client's readings tagged with station_id)
4. Print count and list of stations

```python
conn = sqlite3.connect('data/weather.db')
cursor = conn.execute("SELECT DISTINCT station_id FROM readings ORDER BY station_id")
stations = [row[0] for row in cursor.fetchall()]
print(f"\n✓ Found {len(stations)} station(s): {stations}")
```

**Inputs:** SQLite database file

**Outputs:** List of station IDs (strings like "STATION-001", "STATION-002", etc.)

**Why it matters:**
- Confirms database is readable
- Confirms at least one reading has been inserted
- Verifies station_id field is populated

**Failure modes:**
- File not found → "cannot open database file"
- No readings → stations = [] (empty list)
- Permissions issue → "attempt to write a readonly database"

### Block: Record Count Query

**Where:** Lines 17-19

**What it does:**
1. Execute `SELECT COUNT(*) FROM readings`
2. Fetch count of total readings in database
3. Print count

```python
cursor = conn.execute("SELECT COUNT(*) FROM readings")
count = cursor.fetchone()[0]
print(f"✓ Total records: {count}")
```

**Inputs:** SQLite database

**Outputs:** Integer count of readings

**Why it matters:**
- Quick gauge of data volume
- Confirms readings table is not empty
- Indicates system has been collecting data

**Failure modes:**
- Table missing → "no such table: readings"
- count = 0 → no readings collected yet

### Block: Sample Record Inspection

**Where:** Lines 21-29

**What it does:**
1. Check if count > 0 (at least one reading exists)
2. Fetch first reading: `SELECT * FROM readings LIMIT 1`
3. Query column names via cursor.description
4. Print each column with its value

```python
if count > 0:
    cursor = conn.execute("SELECT * FROM readings LIMIT 1")
    sample = cursor.fetchone()
    print(f"\nSample record:")
    cursor2 = conn.execute("SELECT * FROM readings LIMIT 1")
    cols = [description[0] for description in cursor2.description]
    for i, col in enumerate(cols):
        print(f"  {col}: {sample[i]}")
```

**Inputs:** SQLite database

**Outputs:** Formatted sample reading (all columns)

**Why it matters:**
- Verifies schema (confirms expected columns exist: station_id, timestamp, temperature, humidity, windspeed)
- Shows actual data format (e.g., timestamp as string, temperature as float)
- User can visually verify data sanity (temperature in valid range?)

**Failure modes:**
- Column mismatch → unexpected column names in output

### Block: Flask API Test Instructions

**Where:** Lines 34-45

**What it does:**
1. Print instructions for starting Flask app
2. Print expected API endpoints to test:
   - `/api/stations` — list stations
   - `/api/stats` — aggregate statistics
   - `/api/readings` — retrieve readings
3. Print commands for sending test data and viewing dashboard

```python
print("\n" + "=" * 60)
print("API Test Results")
print("=" * 60)

print("\nStarting Flask app test...")
print("Run: py web/web.py")
print("Then visit: http://127.0.0.1:8000/api/stations")
print("Expected response: {\"stations\": [...]}")

print("\nTo send test data:")
print("Run: py station_client/client.py --station-id TEST-001")
print("\nTo view dashboard:")
print("Visit: http://127.0.0.1:8000")
```

**Inputs:** None (static instructions)

**Outputs:** Human-readable test checklist

**Why it matters:**
- Guides user on next steps
- Shows expected response format
- Easy copy-paste commands

## Data & State

### Database Schema (Referenced, Not Created)

Assumes readings table with columns:

| Column | Type | Example |
|--------|------|---------|
| station_id | TEXT | "STATION-001" |
| timestamp | TEXT | "2025-01-17T14:30:45.123456+00:00" |
| temperature | REAL | 22.5 |
| humidity | REAL | 60.0 |
| windspeed | REAL | 5.2 |

### Expected Output Example

```
============================================================
Testing Weather Station Database
============================================================

✓ Found 3 station(s): ['STATION-001', 'STATION-002', 'STATION-003']
✓ Total records: 150

Sample record:
  station_id: STATION-001
  timestamp: 2025-01-17T14:30:45.123456+00:00
  temperature: 22.5
  humidity: 60.0
  windspeed: 5.2

============================================================
API Test Results
============================================================

Starting Flask app test...
Run: py web/web.py
Then visit: http://127.0.0.1:8000/api/stations
Expected response: {"stations": [...]}

To send test data:
Run: py station_client/client.py --station-id TEST-001

To view dashboard:
Visit: http://127.0.0.1:8000
```

## Control Flow Walkthrough

### Execution Flow

```
$ python consumer_client/test_api.py

1. Import sqlite3 and json modules

2. Open data/weather.db

3. Query DISTINCT station_id:
   → Fetch all rows
   → Convert to list of strings
   → Print count and list

4. Query COUNT(*):
   → Fetch single count value
   → Print count

5. If count > 0:
   a) Fetch first reading
   b) Query column names via cursor.description
   c) Zip columns with sample values
   d) Print each column: value pair

6. Close database connection

7. Print Flask API test instructions

8. Exit (return code 0)
```

### Expected Execution Time

- < 100ms (database operations are fast for simple queries)

### Success Criteria

- No exceptions raised
- stdout contains "✓ Found X station(s)"
- stdout contains "✓ Total records: X" (X > 0)
- If X > 0, sample record is displayed

## Interfaces

### Command-Line Interface

```bash
python consumer_client/test_api.py
```

- No arguments
- No stdin
- stdout: Test results and instructions
- stderr: (empty if successful)
- exit code: 0 (always; no error handling)

### Database Interface

- **File:** `data/weather.db` (must exist and be readable)
- **Queries:**
  - `SELECT DISTINCT station_id FROM readings ORDER BY station_id`
  - `SELECT COUNT(*) FROM readings`
  - `SELECT * FROM readings LIMIT 1`

## Observability & Debugging

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "cannot open database file" | data/weather.db doesn't exist | Start server to create database; check working directory |
| "no such table: readings" | schema hasn't been created | Server needs to insert at least one batch |
| "readonly database" | Permissions issue on file | Check file permissions; ensure write access to data/ dir |
| No output after "Testing..." | Process hung | Check if another process has database locked |

### Manual Testing

```bash
# Verify database file exists
ls -la data/weather.db

# Query database directly
sqlite3 data/weather.db "SELECT COUNT(*) FROM readings;"

# Check disk space (if file very large)
du -sh data/weather.db

# Monitor database growth during server run
watch -n 1 'sqlite3 data/weather.db "SELECT COUNT(*) FROM readings;"'
```

### Debug Output

To add debug output to the script:

```python
# Add before database operations:
import sys
print(f"Current directory: {os.getcwd()}", file=sys.stderr)
print(f"Database path: {os.path.abspath('data/weather.db')}", file=sys.stderr)
print(f"Database exists: {os.path.exists('data/weather.db')}", file=sys.stderr)
```

---

**Key Takeaway:** `test_api.py` is a lightweight database sanity check and Flask testing guide. It opens the SQLite database, queries for stations and reading count, displays a sample record, and prints instructions for testing the Flask API endpoints. Run this first to verify the system is working before running comprehensive protocol tests (`test_consumer.py`).
