"""
Weather Station Server
Receives weather data from clients via TCP socket and stores in SQLite.
"""

import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import telemetry
from protocol import MAX_LINE_SIZE, normalize_utc_timestamp, validate_batch

log = logging.getLogger("weather.server")

# Database configuration - use environment variable with absolute path fallback
DB_FILE = os.getenv("DB_PATH", "/app/data/weather.db")

# Server configuration
# SERVER_HOST accepts one address, a comma-separated list, or "*" for every
# interface on both IPv4 and IPv6 (dual-stack).
HOST = os.getenv("SERVER_HOST", "*")
PORT = int(os.getenv("SERVER_PORT", "12345"))

# Prometheus metrics listener. METRICS_PORT <= 0 disables it.
METRICS_HOST = os.getenv("METRICS_HOST", "0.0.0.0")
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))

# Largest forward jump in seq a station may make past its highest stored seq.
# Without authentication this is only a sanity bound: it stops one bad batch
# (e.g. seq=2**62) from poisoning the station's sequence cursor.
MAX_SEQ_JUMP = int(os.getenv("MAX_SEQ_JUMP", "10000000"))

# Queue and backpressure configuration
QUEUE_MAXSIZE = int(os.getenv("WRITE_QUEUE_MAX", "500"))
WRITE_ENQUEUE_TIMEOUT = float(os.getenv("WRITE_ENQUEUE_TIMEOUT", "2.0"))
WRITE_ACK_TIMEOUT = float(os.getenv("WRITE_ACK_TIMEOUT", "5.0"))

# Global state
write_queue = None
shutdown_event = None
server = None
metrics_server = None
writer_task = None
db_connection = None
client_tasks = set()

# Sentinel to signal writer task to exit
_WRITER_SENTINEL = (None, None)


class BackpressureError(Exception):
    """Raised when write queue is full and enqueue times out."""
    pass


class BatchRejected(Exception):
    """A well-formed batch that storage refuses; retrying it cannot succeed."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


# Metrics (Prometheus text format, served by telemetry.start_metrics_server)
METRICS = telemetry.Registry()
M_CONNECTIONS_ACTIVE = METRICS.gauge("weather_connections_active", "Open TCP client connections")
M_CONNECTIONS_TOTAL = METRICS.counter(
    "weather_connections_total", "TCP client connections accepted"
)
M_BATCHES = METRICS.counter("weather_batches_total", "Producer batches by outcome", ("result",))
M_READINGS_INSERTED = METRICS.counter(
    "weather_readings_inserted_total", "Readings committed to storage"
)
M_READINGS_DUPLICATE = METRICS.counter(
    "weather_readings_duplicate_total", "Replayed readings ignored as identical duplicates"
)
M_CONSUMER_REQUESTS = METRICS.counter(
    "weather_consumer_requests_total", "Consumer requests by type", ("request",)
)
M_BYTES_RECEIVED = METRICS.counter("weather_bytes_received_total", "Bytes read from clients")
M_BYTES_SENT = METRICS.counter("weather_bytes_sent_total", "Bytes written to clients")
M_QUEUE_DEPTH = METRICS.gauge("weather_write_queue_depth", "Batches waiting for the writer")
M_QUEUE_DEPTH.set_function(lambda: write_queue.qsize() if write_queue is not None else 0)
M_ACK_LATENCY = METRICS.histogram(
    "weather_batch_ack_seconds", "Time from batch line received to reply written"
)
M_COMMIT_LATENCY = METRICS.histogram(
    "weather_db_commit_seconds", "Time to check, insert and commit one batch"
)

KNOWN_REQUESTS = ("ping", "stations", "latest", "recent", "latest_seq", "since")


def _safe_json_loads(data: str):
    """
    Safely parse JSON, rejecting NaN/Infinity constants.
    Raises ValueError if non-finite constants are detected.
    """
    def reject_non_finite(s):
        raise ValueError(f"non_finite_constant: {s}")
    
    return json.loads(data, parse_constant=reject_non_finite)


async def init_database():
    """Initialize SQLite database and create table and indexes if they don't exist."""
    # Ensure directory exists
    db_path = Path(DB_FILE)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                temperature REAL NOT NULL,
                humidity REAL NOT NULL,
                windspeed REAL NOT NULL,
                UNIQUE(station_id, seq)
            )
        """)
        # If readings table already existed without seq column, migrate it
        cursor = await db.execute("PRAGMA table_info(readings)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "seq" not in columns:
            await db.execute("ALTER TABLE readings ADD COLUMN seq INTEGER")

        # Compound unique index for station_id and seq to guarantee idempotent writes
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_station_seq
            ON readings(station_id, seq)
        """)
        # Compound index for time-series filtering and ordering (station_id + timestamp DESC)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_readings_station_ts
            ON readings(station_id, timestamp DESC)
        """)
        # Compound index for consumer latest/recent queries (station_id + id DESC)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_readings_station_id_desc
            ON readings(station_id, id DESC)
        """)
        await db.commit()
        log.info("Database initialized with indexes: %s", DB_FILE)


def _stored_form(reading):
    """The (timestamp, temperature, humidity, windspeed) tuple as SQLite stores it."""
    return (
        normalize_utc_timestamp(reading["timestamp"]),
        float(reading["temperature"]),
        float(reading["humidity"]),
        float(reading["windspeed"]),
    )


async def _check_batch(db, batch):
    """
    Reject a batch that would corrupt a station's history. Runs on the writer
    connection, so the check and the insert see the same committed state.

    - conflict: a (station_id, seq) key is reused with a different payload,
      either inside the batch or against a stored row. Identical replays pass
      and are counted as duplicates by INSERT OR IGNORE.
    - seq_out_of_range: seq jumps more than MAX_SEQ_JUMP past the station's
      highest stored seq.
    """
    by_station = {}
    for reading in batch:
        seqs = by_station.setdefault(reading["station_id"], {})
        form = _stored_form(reading)
        if seqs.setdefault(reading["seq"], form) != form:
            raise BatchRejected(
                "conflict",
                f"seq_conflict: station_id={reading['station_id']} seq={reading['seq']}",
            )

    for station_id, seqs in by_station.items():
        cursor = await db.execute(
            "SELECT MAX(seq) FROM readings WHERE station_id = ?", (station_id,)
        )
        row = await cursor.fetchone()
        highest = row[0] if row and row[0] is not None else -1
        limit = highest + MAX_SEQ_JUMP
        too_far = max(seqs)
        if too_far > limit:
            raise BatchRejected(
                "seq_out_of_range",
                f"seq_out_of_range: station_id={station_id} seq={too_far} max_allowed={limit}",
            )

        placeholders = ",".join("?" * len(seqs))
        cursor = await db.execute(
            "SELECT seq, timestamp, temperature, humidity, windspeed FROM readings "
            f"WHERE station_id = ? AND seq IN ({placeholders})",
            (station_id, *seqs),
        )
        for seq, timestamp, *values in await cursor.fetchall():
            if (normalize_utc_timestamp(timestamp), *values) != seqs[seq]:
                raise BatchRejected(
                    "conflict", f"seq_conflict: station_id={station_id} seq={seq}"
                )


async def _process_write(batch, result_future):
    """Check, insert and commit one batch, then resolve its future."""
    started = time.perf_counter()
    try:
        await _check_batch(db_connection, batch)
        # INSERT OR IGNORE keeps identical replays idempotent
        cursor = await db_connection.executemany(
            """
            INSERT OR IGNORE INTO readings (station_id, seq, timestamp, temperature, humidity, windspeed)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (reading["station_id"], reading["seq"], *_stored_form(reading))
                for reading in batch
            ],
        )
        await db_connection.commit()
        inserted = cursor.rowcount
        duplicates = len(batch) - inserted
        M_READINGS_INSERTED.inc(inserted)
        M_READINGS_DUPLICATE.inc(duplicates)
        if not result_future.done():
            result_future.set_result({"inserted": inserted, "duplicates": duplicates})
    except Exception as e:
        await db_connection.rollback()
        if not result_future.done():
            result_future.set_exception(e)
    finally:
        M_COMMIT_LATENCY.observe(time.perf_counter() - started)


async def db_writer_task():
    """
    Single writer task that processes batches sequentially from the queue.
    This prevents "database is locked" errors by ensuring only one writer at a time.
    
    Handles cancellation gracefully: checks if futures are cancelled before
    completing them to prevent "invalid state" errors.
    """
    global db_connection
    
    try:
        # Open single persistent database connection
        db_connection = await aiosqlite.connect(DB_FILE)
        log.info("Database writer task started")
        
        # Apply SQLite pragmas to reduce lock contention and improve concurrency
        await db_connection.execute("PRAGMA journal_mode=WAL;")
        await db_connection.execute("PRAGMA synchronous=NORMAL;")
        await db_connection.execute("PRAGMA busy_timeout=3000;")
        await db_connection.commit()
        log.info("SQLite pragmas applied: WAL mode, NORMAL sync, 3000ms busy timeout")
        
        while True:
            try:
                # Wait for batch with timeout to check shutdown periodically
                batch, result_future = await asyncio.wait_for(
                    write_queue.get(),
                    timeout=1.0
                )
                
                # Check for sentinel to exit gracefully
                if batch is None and result_future is None:
                    break
                
                # Guard: skip if future already cancelled or completed
                if result_future.cancelled() or result_future.done():
                    continue
                
                await _process_write(batch, result_future)
                
            except asyncio.TimeoutError:
                # No items in queue, continue to check shutdown
                continue
        
        # Graceful shutdown: process remaining items in queue
        log.info("Flushing remaining writes...")
        while not write_queue.empty():
            batch, result_future = await write_queue.get()
            
            # Skip sentinel
            if batch is None and result_future is None:
                break
            
            # Skip cancelled futures
            if result_future.cancelled() or result_future.done():
                continue
            
            await _process_write(batch, result_future)
        
    finally:
        # Close database connection
        if db_connection:
            await db_connection.close()
            log.info("Database connection closed")


async def enqueue_batch(batch):
    """
    Enqueue a validated batch for writing.
    Creates a future using the current event loop to handle cancellation properly.
    Enforces backpressure queue put timeout and writer ACK timeout.
    Returns a dict with 'inserted' and 'duplicates' counts.
    """
    if not batch:
        return {"inserted": 0, "duplicates": 0}
    
    # Create a future using the current running loop
    loop = asyncio.get_running_loop()
    result_future = loop.create_future()
    
    # Add batch to queue with backpressure timeout
    try:
        await asyncio.wait_for(
            write_queue.put((batch, result_future)),
            timeout=WRITE_ENQUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        result_future.cancel()
        raise BackpressureError("server_busy") from None
    
    # Wait for writer task to process it with bounded ACK timeout
    try:
        return await asyncio.wait_for(result_future, timeout=WRITE_ACK_TIMEOUT)
    except asyncio.TimeoutError:
        result_future.cancel()
        raise TimeoutError("write_ack_timeout") from None
    except asyncio.CancelledError:
        # Client disconnected while waiting
        result_future.cancel()
        raise


async def handle_consumer_request(req, addr):
    """
    Handle a consumer read-only request.
    
    req: dict with "request" key and optional parameters (station_id, limit, seq, etc.)
    addr: client address for logging
    
    Returns: dict response with "status" and data fields (or error).
    """
    try:
        request_type = req.get("request")
        
        if request_type == "ping":
            # Supported: {"request":"ping"} - liveness probe that never touches the database
            # Return: {"status":"ok","pong":true,"server_time":"<UTC ISO-8601>"}
            return {
                "status": "ok",
                "pong": True,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }

        elif request_type == "stations":
            # Supported: {"request":"stations"}
            # Return: {"status":"ok","stations":[...]}
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("PRAGMA busy_timeout=3000;")
                cursor = await db.execute(
                    "SELECT DISTINCT station_id FROM readings ORDER BY station_id"
                )
                rows = await cursor.fetchall()
                stations = [row[0] for row in rows]
            return {"status": "ok", "stations": stations}
        
        elif request_type == "latest":
            # Supported: {"request":"latest"} or {"request":"latest","station_id":"..."}
            # Return: {"status":"ok","reading":{...} or null}
            station_id = req.get("station_id")
            
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("PRAGMA busy_timeout=3000;")
                db.row_factory = aiosqlite.Row
                
                if station_id:
                    cursor = await db.execute(
                        "SELECT station_id, seq, timestamp, temperature, humidity, windspeed FROM readings WHERE station_id = ? ORDER BY id DESC LIMIT 1",
                        (station_id,)
                    )
                else:
                    cursor = await db.execute(
                        "SELECT station_id, seq, timestamp, temperature, humidity, windspeed FROM readings ORDER BY id DESC LIMIT 1"
                    )
                
                row = await cursor.fetchone()
                reading = dict(row) if row else None
            
            return {"status": "ok", "reading": reading}
        
        elif request_type == "recent":
            # Supported: {"request":"recent","limit":50} or with "station_id"
            # Return: {"status":"ok","readings":[...]}
            station_id = req.get("station_id")
            limit = req.get("limit", 50)
            
            # Clamp limit to 1..500
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                limit = 50
            limit = min(limit, 500)
            
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("PRAGMA busy_timeout=3000;")
                db.row_factory = aiosqlite.Row
                
                if station_id:
                    cursor = await db.execute(
                        "SELECT station_id, seq, timestamp, temperature, humidity, windspeed FROM readings WHERE station_id = ? ORDER BY id DESC LIMIT ?",
                        (station_id, limit)
                    )
                else:
                    cursor = await db.execute(
                        "SELECT station_id, seq, timestamp, temperature, humidity, windspeed FROM readings ORDER BY id DESC LIMIT ?",
                        (limit,)
                    )
                
                rows = await cursor.fetchall()
                readings = [dict(row) for row in rows]
            
            return {"status": "ok", "readings": readings}
        
        elif request_type == "latest_seq":
            # Supported: {"request":"latest_seq"} or {"request":"latest_seq","station_id":"..."}
            # Return: {"status":"ok","latest_seq":N} (and station_id if requested)
            station_id = req.get("station_id")
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("PRAGMA busy_timeout=3000;")
                if station_id:
                    cursor = await db.execute(
                        "SELECT MAX(seq) FROM readings WHERE station_id = ?",
                        (station_id,)
                    )
                else:
                    cursor = await db.execute("SELECT MAX(seq) FROM readings")
                row = await cursor.fetchone()
                latest_seq = row[0] if (row and row[0] is not None) else None
            
            res = {"status": "ok", "latest_seq": latest_seq}
            if station_id:
                res["station_id"] = station_id
            return res

        elif request_type == "since":
            # Supported: {"request":"since","seq":100} or {"request":"since","station_id":"...","seq":100,"limit":50}
            # Return: {"status":"ok","readings":[...],"count":N}
            # seq is only ordered within one station, so the all-stations form
            # groups by station and orders by seq inside each group.
            station_id = req.get("station_id")
            since_seq = req.get("seq")
            if since_seq is None:
                return {"status": "error", "reason": "missing seq"}
            if not isinstance(since_seq, int) or isinstance(since_seq, bool) or since_seq < 0:
                return {"status": "error", "reason": "invalid seq (expected non-negative integer)"}

            limit = req.get("limit", 100)
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                limit = 100
            limit = min(limit, 500)

            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("PRAGMA busy_timeout=3000;")
                db.row_factory = aiosqlite.Row

                if station_id:
                    cursor = await db.execute(
                        """
                        SELECT station_id, seq, timestamp, temperature, humidity, windspeed
                        FROM readings
                        WHERE station_id = ? AND seq > ?
                        ORDER BY seq ASC
                        LIMIT ?
                        """,
                        (station_id, since_seq, limit)
                    )
                else:
                    cursor = await db.execute(
                        """
                        SELECT station_id, seq, timestamp, temperature, humidity, windspeed
                        FROM readings
                        WHERE seq > ?
                        ORDER BY station_id ASC, seq ASC
                        LIMIT ?
                        """,
                        (since_seq, limit)
                    )

                rows = await cursor.fetchall()
                readings = [dict(row) for row in rows]

            return {"status": "ok", "readings": readings, "count": len(readings)}

        else:
            # Unknown request type
            request_type_str = str(request_type) if request_type is not None else "null"
            return {"status": "error", "reason": f"unknown_request:{request_type_str}"}
    
    except Exception as e:
        log.warning("Error in consumer request from %s: %s", addr, e)
        return {"status": "error", "reason": f"server_error: {str(e)}"}


async def _send(writer, response):
    """Write one NDJSON reply and wait for the socket buffer to drain."""
    data = (json.dumps(response) + "\n").encode("utf-8")
    writer.write(data)
    await writer.drain()
    M_BYTES_SENT.inc(len(data))


async def _handle_line(line, writer, addr):
    """Process one request line and send exactly one reply."""
    is_batch = False
    result = "error"
    started = time.perf_counter()
    try:
        # Parse JSON safely (rejects NaN/Infinity)
        parsed = _safe_json_loads(line.decode("utf-8").strip())

        # Protocol multiplexing: a dict with "request" is a consumer query,
        # anything else is treated as a producer batch.
        if isinstance(parsed, dict) and "request" in parsed:
            name = parsed.get("request")
            M_CONSUMER_REQUESTS.inc(request=name if name in KNOWN_REQUESTS else "unknown")
            response = await handle_consumer_request(parsed, addr)
            log.debug("Processed consumer request from %s: %s", addr, name)
        else:
            is_batch = True
            # Raises ValueError on the first validation error (all-or-nothing)
            validate_batch(parsed)
            write_result = await enqueue_batch(parsed)
            response = {"status": "ok", **write_result}
            result = "ok"
            log.debug("Processed batch from %s: %s", addr, write_result)

    except BackpressureError:
        result = "backpressure"
        response = {
            "status": "error",
            "code": "backpressure",
            "reason": "server_busy",
            "retry_after": 1,
        }
        log.warning("Backpressure applied to %s: server_busy", addr)

    except BatchRejected as e:
        # Not retryable: the same batch would be refused again
        result = e.code
        response = {"status": "error", "code": e.code, "reason": e.reason}
        log.warning("Batch from %s rejected: %s", addr, e.reason)

    except TimeoutError as e:
        result = "ack_timeout"
        response = {"status": "error", "code": "retryable", "reason": f"server_error: {e}"}
        log.warning("Writer ACK timeout for %s: %s", addr, e)

    except json.JSONDecodeError as e:
        result = "invalid"
        response = {"status": "error", "reason": f"invalid_json: {e}"}

    except ValueError as e:
        # Validation error: invalid timestamp, non-finite numbers, range violations
        result = "invalid"
        response = {"status": "error", "reason": str(e)}

    except aiosqlite.Error as e:
        result = "db_error"
        response = {"status": "error", "code": "retryable", "reason": f"server_error: {e}"}

    except Exception as e:
        response = {"status": "error", "reason": f"server_error: {e}"}

    await _send(writer, response)
    if is_batch or result == "invalid":
        M_BATCHES.inc(result=result)
    if is_batch:
        M_ACK_LATENCY.observe(time.perf_counter() - started)


async def handle_client(reader, writer):
    """
    Handle a single client connection: one NDJSON request line, one reply.

    Note: asyncio.start_server() is called with limit=MAX_LINE_SIZE+1 to prevent
    unbounded line buffering. Oversized lines trigger LimitOverrunError.
    """
    addr = writer.get_extra_info('peername')
    task = asyncio.current_task()
    client_tasks.add(task)
    M_CONNECTIONS_TOTAL.inc()
    M_CONNECTIONS_ACTIVE.inc()
    log.debug("Client connected: %s", addr)

    try:
        while shutdown_event is None or not shutdown_event.is_set():
            # Read one line (one batch)
            # The stream reader's limit=MAX_LINE_SIZE+1 enforces max size
            try:
                line = await reader.readline()
                if len(line) > MAX_LINE_SIZE:
                    raise ValueError("line_too_long")
            except (asyncio.LimitOverrunError, ValueError):
                # Line exceeds MAX_LINE_SIZE: reply, then close the connection
                M_BATCHES.inc(result="invalid")
                await _send(writer, {"status": "error", "reason": "line_too_long"})
                break

            # Connection closed
            if not line:
                break
            if shutdown_event is not None and shutdown_event.is_set():
                break

            M_BYTES_RECEIVED.inc(len(line))
            await _handle_line(line, writer, addr)

    except Exception as e:
        log.info("Error handling client %s: %s", addr, e)

    finally:
        client_tasks.discard(task)
        M_CONNECTIONS_ACTIVE.dec()
        log.debug("Client disconnected: %s", addr)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def resolve_bind_hosts(value):
    """
    Turn SERVER_HOST into the host argument for asyncio.start_server().

    "*" or empty -> None (every interface, IPv4 and IPv6). A comma-separated
    list -> list of addresses. Anything else -> that single address.
    """
    hosts = [h.strip() for h in (value or "").split(",") if h.strip()]
    if not hosts or "*" in hosts:
        return None
    return hosts[0] if len(hosts) == 1 else hosts


async def start_listener():
    """Bind the ingest listener, falling back to IPv4 if dual-stack is unavailable."""
    host = resolve_bind_hosts(HOST)
    # limit=MAX_LINE_SIZE+1 makes readline() raise instead of buffering an
    # arbitrarily long line from a buggy or hostile client.
    try:
        return await asyncio.start_server(handle_client, host, PORT, limit=MAX_LINE_SIZE + 1)
    except OSError as e:
        if host is not None:
            raise
        log.warning("Dual-stack bind failed (%s); falling back to IPv4 only", e)
        return await asyncio.start_server(
            handle_client, "0.0.0.0", PORT, limit=MAX_LINE_SIZE + 1
        )


async def start_metrics_listener():
    """Start the Prometheus endpoint. A bind failure must not stop ingest."""
    if METRICS_PORT <= 0:
        return None
    try:
        listener = await telemetry.start_metrics_server(METRICS, METRICS_HOST, METRICS_PORT)
    except OSError as e:
        log.warning("Metrics endpoint disabled: cannot bind %s:%s (%s)", METRICS_HOST, METRICS_PORT, e)
        return None
    log.info("Metrics on http://%s:%s/metrics", METRICS_HOST, METRICS_PORT)
    return listener


async def shutdown_handler(signum):
    """
    Signal handler to initiate graceful shutdown.
    Sets shutdown event and closes the listening socket.
    """
    global server, shutdown_event
    log.info("Received signal %s, initiating shutdown...", signum)
    if server:
        server.close()
    shutdown_event.set()


async def main():
    """Start the weather station server with graceful shutdown support."""
    global write_queue, shutdown_event, server, writer_task, metrics_server
    
    # Initialize database
    await init_database()
    
    # Create bounded write queue and shutdown event
    write_queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    shutdown_event = asyncio.Event()
    
    # Start database writer task
    writer_task = asyncio.create_task(db_writer_task())
    
    server = await start_listener()
    metrics_server = await start_metrics_listener()

    for sock in server.sockets:
        bound = sock.getsockname()
        log.info("Weather Station Server listening on %s port %s", bound[0], bound[1])
    log.info("Waiting for client connections (Ctrl+C to shutdown gracefully)")

    # Setup signal handlers for graceful shutdown (POSIX only)
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_handler(s)))
    except NotImplementedError:
        # Signal handlers not supported on Windows
        log.info("Signal handlers not supported on this platform; use Ctrl+C to stop.")
    
    try:
        # Keep server running until shutdown
        await shutdown_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        # Graceful shutdown sequence
        log.info("Initiating graceful shutdown...")
        
        # Close listening sockets (stop accepting new connections)
        server.close()
        if metrics_server is not None:
            metrics_server.close()
        shutdown_event.set()

        # Finish in-flight handlers before placing the FIFO writer sentinel.
        # Idle readers are cancelled after this bounded grace period.
        if client_tasks:
            _, pending = await asyncio.wait(
                list(client_tasks), timeout=WRITE_ENQUEUE_TIMEOUT + WRITE_ACK_TIMEOUT + 1
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        # Python 3.12+ also waits for active connections here, so this must
        # follow handler cleanup rather than precede the cancellation deadline.
        await server.wait_closed()
        log.info("Server socket closed")
        
        # Set shutdown event and send sentinel to writer task
        shutdown_event.set()
        await write_queue.put(_WRITER_SENTINEL)
        
        # Wait for writer task to flush queue and exit
        try:
            await asyncio.wait_for(writer_task, timeout=10.0)
        except asyncio.TimeoutError:
            log.warning("Writer task did not exit within timeout")
            writer_task.cancel()
        
        log.info("Server stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
