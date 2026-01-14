# Architecture Diagram: Consumer Protocol Integration

## TCP Connection Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  Weather Station TCP Server (Port 12345)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  handle_client(reader, writer)                                 │
│  │                                                              │
│  ├─> Read line from socket                                    │
│  │                                                              │
│  ├─> Parse JSON: _safe_json_loads(data)                       │
│  │                                                              │
│  ├─> PROTOCOL MULTIPLEXING DECISION                            │
│  │   │                                                          │
│  │   ├─ Is dict? AND Has "request" key?                       │
│  │   │                                                          │
│  │   ├─ YES → CONSUMER REQUEST PATH                            │
│  │   │   │                                                      │
│  │   │   └─> handle_consumer_request(req, addr)               │
│  │   │       │                                                  │
│  │   │       ├─> New aiosqlite.connect(DB_FILE)               │
│  │   │       │   └─> PRAGMA busy_timeout=3000                 │
│  │   │       │                                                  │
│  │   │       ├─> Switch on req["request"]:                    │
│  │   │       │   │                                              │
│  │   │       │   ├─ "stations" → SELECT DISTINCT station_id   │
│  │   │       │   │   return {"status":"ok","stations":[...]}   │
│  │   │       │   │                                              │
│  │   │       │   ├─ "latest" → SELECT ... LIMIT 1             │
│  │   │       │   │   [optionally WHERE station_id=?]           │
│  │   │       │   │   return {"status":"ok","reading":{...}}    │
│  │   │       │   │                                              │
│  │   │       │   ├─ "recent" → SELECT ... LIMIT ? DESC        │
│  │   │       │   │   [limit clamped 1-500, default 50]        │
│  │   │       │   │   [optionally WHERE station_id=?]           │
│  │   │       │   │   return {"status":"ok","readings":[...]}   │
│  │   │       │   │                                              │
│  │   │       │   └─ unknown → return {"status":"error"...}    │
│  │   │       │                                                  │
│  │   │       └─> Return response dict                          │
│  │   │                                                          │
│  │   └─ NO → PRODUCER INGEST PATH (Original Logic)            │
│  │       │                                                      │
│  │       ├─> batch = parsed                                   │
│  │       │                                                      │
│  │       ├─> validate_batch(batch)                            │
│  │       │   └─> All 50 records validated (raises ValueError) │
│  │       │                                                      │
│  │       ├─> inserted = await enqueue_batch(batch)            │
│  │       │   └─> Queue → db_writer_task() → SQLite            │
│  │       │                                                      │
│  │       └─> response = {"status":"ok","inserted":N}          │
│  │                                                              │
│  ├─> Encode response as newline-delimited JSON                │
│  │   response_json = json.dumps(response) + "\n"              │
│  │                                                              │
│  ├─> writer.write(response_json.encode('utf-8'))              │
│  │                                                              │
│  ├─> await writer.drain()  [Flush socket]                     │
│  │                                                              │
│  └─> [Loop: Wait for next request]                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Database Layer (SQLite with WAL)                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  WRITER PATH (Producer Only):                                  │
│  │                                                              │
│  ├─> db_connection (persistent, shared writer)                │
│  │   └─> PRAGMA journal_mode=WAL                              │
│  │   └─> PRAGMA synchronous=NORMAL                            │
│  │   └─> PRAGMA busy_timeout=3000                             │
│  │   └─> Single writer task processes queue sequentially      │
│  │       └─> INSERT INTO readings (station_id, timestamp...)   │
│  │                                                              │
│  READER PATH (Consumer Only):                                  │
│  │                                                              │
│  └─> Ephemeral connections per request                        │
│      └─> PRAGMA busy_timeout=3000                             │
│      └─> SELECT queries (no modifications)                    │
│      └─> Closed after each request                            │
│                                                                 │
│  TABLE: readings                                              │
│  ├─ id (INTEGER PRIMARY KEY)                                  │
│  ├─ station_id (TEXT)                                         │
│  ├─ timestamp (TEXT, ISO 8601)                               │
│  ├─ temperature (REAL)                                        │
│  ├─ humidity (REAL)                                           │
│  └─ windspeed (REAL)                                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Client Connection Examples

### Producer Client (Existing)
```
┌──────────────────────────────┐
│  Producer Client (Docker)    │
│  ├─ Batch ingest             │
│  └─ port 12345               │
└────────────────┬─────────────┘
                 │
        TCP Connection
        (newline-delimited JSON)
                 │
        {"readings":[{...},{...}]}
                 ↓
        ┌────────────────────┐
        │ handle_client()    │
        │ → Producer path    │
        │ ← {"status":"ok",  │
        │    "inserted":2}   │
        └────────────────────┘
```

### Consumer Client (New)
```
┌──────────────────────────────┐
│  Consumer Client (Host)      │
│  ├─ Query stations           │
│  ├─ Query latest reading     │
│  ├─ Query recent readings    │
│  └─ port 12345               │
└────────────────┬─────────────┘
                 │
        TCP Connection
        (newline-delimited JSON)
                 │
        {"request":"latest","station_id":"STATION-001"}
                 ↓
        ┌────────────────────────────┐
        │ handle_client()            │
        │ → Consumer path            │
        │ ← {"status":"ok",          │
        │    "reading":{...}}        │
        └────────────────────────────┘
```

---

## Concurrency Model

### Without WAL
```
Writer wants to INSERT          Reader wants to SELECT
        │                                 │
        └─────→ [LOCKED CONFLICT] ←──────┘
           One at a time (sequential access)
```

### With WAL + Separate Connections
```
Writer inserts to -wal file     Readers access main file (snapshots)
        │                              │
        └──────────────────────────────┘
           Concurrent (separate file handles)
```

**Why this matters:**
- **Producer:** Uses persistent `db_connection` from `db_writer_task()`
- **Consumers:** Use ephemeral connections per request
- **Result:** Multiple consumers can read while producer writes (when WAL enabled)

---

## Request-Response Lifecycle

### Single Request
```
┌─────────────────────────────────────────┐
│  Client: send JSON request + "\n"       │
└────────────────┬────────────────────────┘
                 │ (TCP send)
                 ↓
┌──────────────────────────────────────────┐
│  Server: readline() → parse → multiplex  │
│  - Identify: dict + "request"? YES/NO    │
│  - Route: consumer or producer path      │
│  - Process: execute query or validate    │
│  - Response: {"status":"ok"/"error",...} │
└────────────────┬───────────────────────┘
                 │ (TCP send)
                 ↓
┌─────────────────────────────────────────┐
│  Client: receive JSON response + "\n"   │
│  - Parse: json.loads()                  │
│  - Check: response["status"]            │
│  - Use: response["data"]                │
└─────────────────────────────────────────┘
```

---

## Error Handling Flow

```
receive data
    ↓
[asyncio.LimitOverrunError]
    └─→ {"status":"error","reason":"line_too_long"}

decode UTF-8
    ↓
[decode error]
    └─→ {"status":"error","reason":"invalid_utf8"}

parse JSON
    ↓
[json.JSONDecodeError]
    └─→ {"status":"error","reason":"invalid_json: ..."}

multiplex
    ├─→ is dict? + "request"?
    │   ├─ YES (consumer path)
    │   │   ├─→ handle_consumer_request()
    │   │   │   └─→ [database error] → {"status":"error","reason":"server_error: ..."}
    │   │   │   └─→ [unknown request] → {"status":"error","reason":"unknown_request:..."}
    │   │
    │   └─ NO (producer path)
    │       ├─→ validate_batch()
    │       │   └─→ [validation error] → {"status":"error","reason":"invalid_timestamp: ..."}
    │       ├─→ enqueue_batch()
    │       │   └─→ [queue full/error] → {"status":"error","reason":"server_error: ..."}
```

---

## Docker Port Mapping

```
┌──────────────────────────────────────────┐
│  docker-compose.yml                      │
├──────────────────────────────────────────┤
│                                          │
│  services:                               │
│    server:                               │
│      ports:                              │
│        - "12345:12345"                   │
│          ↑       ↑                       │
│      (host)  (container)                │
│                                          │
└──────────────────────────────────────────┘

Host machine (consumer):
  ├─ localhost:12345  ← Consumer queries
  └─ docker_ip:12345

Container (server):
  └─ 0.0.0.0:12345   ← Listens on all interfaces

Producer (in docker):
  └─ server:12345    ← Docker network DNS
```
