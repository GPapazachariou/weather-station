"""
Weather Station Server
Receives weather data from clients via TCP socket and stores in SQLite.
"""

import asyncio
import json
import aiosqlite
import signal
import os
from pathlib import Path
from protocol import validate_batch, MAX_LINE_SIZE


# Database configuration - use environment variable with absolute path fallback
DB_FILE = os.getenv("DB_PATH", "/app/data/weather.db")

# Server configuration
HOST = "0.0.0.0"
PORT = 12345

# Global state
write_queue = None
shutdown_event = None
server = None
writer_task = None
db_connection = None

# Sentinel to signal writer task to exit
_WRITER_SENTINEL = (None, None)


def _safe_json_loads(data: str):
    """
    Safely parse JSON, rejecting NaN/Infinity constants.
    Raises ValueError if non-finite constants are detected.
    """
    def reject_non_finite(s):
        raise ValueError(f"non_finite_constant: {s}")
    
    return json.loads(data, parse_constant=reject_non_finite)


async def init_database():
    """Initialize SQLite database and create table if it doesn't exist."""
    # Ensure directory exists
    db_path = Path(DB_FILE)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT,
                timestamp TEXT,
                temperature REAL,
                humidity REAL,
                windspeed REAL
            )
        """)
        await db.commit()
        print(f"Database initialized: {DB_FILE}")


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
        print("Database writer task started")
        
        # Apply SQLite pragmas to reduce lock contention and improve concurrency
        await db_connection.execute("PRAGMA journal_mode=WAL;")
        await db_connection.execute("PRAGMA synchronous=NORMAL;")
        await db_connection.execute("PRAGMA busy_timeout=3000;")
        await db_connection.commit()
        print("SQLite pragmas applied: WAL mode, NORMAL sync, 3000ms busy timeout")
        
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
                
                try:
                    # Insert batch in a single transaction (all-or-nothing)
                    await db_connection.executemany(
                        """
                        INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                reading["station_id"],
                                reading["timestamp"],
                                reading["temperature"],
                                reading["humidity"],
                                reading["windspeed"]
                            )
                            for reading in batch
                        ]
                    )
                    await db_connection.commit()
                    
                    # Signal success with number of inserted records
                    if not result_future.done():
                        result_future.set_result(len(batch))
                    
                except Exception as e:
                    # Signal failure only if not already done
                    if not result_future.done():
                        result_future.set_exception(e)
                
            except asyncio.TimeoutError:
                # No items in queue, continue to check shutdown
                continue
        
        # Graceful shutdown: process remaining items in queue
        print("Flushing remaining writes...")
        while not write_queue.empty():
            batch, result_future = await write_queue.get()
            
            # Skip sentinel
            if batch is None and result_future is None:
                break
            
            # Skip cancelled futures
            if result_future.cancelled() or result_future.done():
                continue
            
            try:
                await db_connection.executemany(
                    """
                    INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            reading["station_id"],
                            reading["timestamp"],
                            reading["temperature"],
                            reading["humidity"],
                            reading["windspeed"]
                        )
                        for reading in batch
                    ]
                )
                await db_connection.commit()
                if not result_future.done():
                    result_future.set_result(len(batch))
            except Exception as e:
                if not result_future.done():
                    result_future.set_exception(e)
        
    finally:
        # Close database connection
        if db_connection:
            await db_connection.close()
            print("Database connection closed")


async def enqueue_batch(batch):
    """
    Enqueue a validated batch for writing.
    Creates a future using the current event loop to handle cancellation properly.
    Returns the number of inserted readings.
    """
    if not batch:
        return 0
    
    # Create a future using the current running loop
    loop = asyncio.get_running_loop()
    result_future = loop.create_future()
    
    # Add batch to queue
    await write_queue.put((batch, result_future))
    
    # Wait for writer task to process it
    try:
        return await result_future
    except asyncio.CancelledError:
        # Client disconnected while waiting
        result_future.cancel()
        raise


async def handle_consumer_request(req, addr):
    """
    Handle a consumer read-only request.
    
    req: dict with "request" key and optional parameters (station_id, limit, etc.)
    addr: client address for logging
    
    Returns: dict response with "status" and data fields (or error).
    """
    try:
        request_type = req.get("request")
        
        if request_type == "stations":
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
                        "SELECT station_id, timestamp, temperature, humidity, windspeed FROM readings WHERE station_id = ? ORDER BY id DESC LIMIT 1",
                        (station_id,)
                    )
                else:
                    cursor = await db.execute(
                        "SELECT station_id, timestamp, temperature, humidity, windspeed FROM readings ORDER BY id DESC LIMIT 1"
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
            if not isinstance(limit, int) or limit < 1:
                limit = 50
            limit = min(limit, 500)
            
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("PRAGMA busy_timeout=3000;")
                db.row_factory = aiosqlite.Row
                
                if station_id:
                    cursor = await db.execute(
                        "SELECT station_id, timestamp, temperature, humidity, windspeed FROM readings WHERE station_id = ? ORDER BY id DESC LIMIT ?",
                        (station_id, limit)
                    )
                else:
                    cursor = await db.execute(
                        "SELECT station_id, timestamp, temperature, humidity, windspeed FROM readings ORDER BY id DESC LIMIT ?",
                        (limit,)
                    )
                
                rows = await cursor.fetchall()
                readings = [dict(row) for row in rows]
            
            return {"status": "ok", "readings": readings}
        
        else:
            # Unknown request type
            request_type_str = str(request_type) if request_type is not None else "null"
            return {"status": "error", "reason": f"unknown_request:{request_type_str}"}
    
    except Exception as e:
        print(f"Error in consumer request from {addr}: {e}")
        return {"status": "error", "reason": f"server_error: {str(e)}"}


async def handle_client(reader, writer):
    """
    Handle a single client connection.
    Read batches of weather data and store them in the database.
    
    Note: asyncio.start_server() is called with limit=MAX_LINE_SIZE+1 to prevent
    unbounded line buffering. Oversized lines trigger LimitOverrunError.
    """
    addr = writer.get_extra_info('peername')
    print(f"Client connected: {addr}")
    
    try:
        while True:
            # Read one line (one batch)
            # The stream reader's limit=MAX_LINE_SIZE+1 enforces max size
            try:
                line = await reader.readline()
            except asyncio.LimitOverrunError:
                # Line exceeds MAX_LINE_SIZE
                response = {"status": "error", "reason": "line_too_long"}
                writer.write((json.dumps(response) + "\n").encode('utf-8'))
                await writer.drain()
                break  # Close connection on oversized line
            
            # Connection closed
            if not line:
                break
            
            try:
                # Decode line
                data = line.decode('utf-8').strip()
                
                # Parse JSON safely (rejects NaN/Infinity)
                parsed = _safe_json_loads(data)
                
                # Protocol multiplexing: check if this is a consumer request or producer batch
                if isinstance(parsed, dict) and "request" in parsed:
                    # Consumer request path
                    response = await handle_consumer_request(parsed, addr)
                    writer.write((json.dumps(response) + "\n").encode('utf-8'))
                    await writer.drain()
                    print(f"Processed consumer request from {addr}: {parsed.get('request')}")
                else:
                    # Producer ingest path (original behavior)
                    batch = parsed
                    
                    # Validate batch using protocol validation
                    # This raises ValueError on any validation error
                    # (includes timestamp format and numeric finiteness checks)
                    validate_batch(batch)
                    
                    # All-or-nothing: enqueue batch for sequential writing
                    inserted = await enqueue_batch(batch)
                    
                    # Send success response
                    response = {"status": "ok", "inserted": inserted}
                    writer.write((json.dumps(response) + "\n").encode('utf-8'))
                    await writer.drain()
                    
                    print(f"Processed batch from {addr}: {inserted} readings inserted")
                
            except asyncio.CancelledError:
                # Client handler was cancelled (connection lost, etc.)
                raise
                
            except json.JSONDecodeError as e:
                # Invalid JSON - do not insert anything
                response = {"status": "error", "reason": f"invalid_json: {str(e)}"}
                writer.write((json.dumps(response) + "\n").encode('utf-8'))
                await writer.drain()
                
            except ValueError as e:
                # Validation error - do not insert anything
                # Includes: invalid timestamp, non-finite numbers, range violations
                response = {"status": "error", "reason": str(e)}
                writer.write((json.dumps(response) + "\n").encode('utf-8'))
                await writer.drain()
                
            except Exception as e:
                # Other errors - do not insert anything
                response = {"status": "error", "reason": f"server_error: {str(e)}"}
                writer.write((json.dumps(response) + "\n").encode('utf-8'))
                await writer.drain()
    
    except Exception as e:
        print(f"Error handling client {addr}: {e}")
    
    finally:
        print(f"Client disconnected: {addr}")
        writer.close()
        await writer.wait_closed()


async def shutdown_handler(signum):
    """
    Signal handler to initiate graceful shutdown.
    Sets shutdown event and closes the listening socket.
    """
    global server, shutdown_event
    print(f"\nReceived signal {signum}, initiating shutdown...")
    if server:
        server.close()
    shutdown_event.set()


async def main():
    """Start the weather station server with graceful shutdown support."""
    global write_queue, shutdown_event, server, writer_task
    
    # Initialize database
    await init_database()
    
    # Create queue and shutdown event
    write_queue = asyncio.Queue()
    shutdown_event = asyncio.Event()
    
    # Start database writer task
    writer_task = asyncio.create_task(db_writer_task())
    
    # Start TCP server with stream limit to prevent unbounded line buffering.
    # limit=MAX_LINE_SIZE+1 ensures asyncio.LimitOverrunError is raised if a line
    # exceeds MAX_LINE_SIZE, protecting against malicious or buggy clients sending
    # arbitrarily large lines.
    server = await asyncio.start_server(
        handle_client,
        HOST,
        PORT,
        limit=MAX_LINE_SIZE + 1
    )
    
    addr = server.sockets[0].getsockname()
    print(f"Weather Station Server running on {addr[0]}:{addr[1]}")
    print("Waiting for client connections...")
    print("(Ctrl+C to shutdown gracefully)")
    
    # Setup signal handlers for graceful shutdown (POSIX only)
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_handler(s)))
    except NotImplementedError:
        # Signal handlers not supported on Windows
        print("Signal handlers not supported on this platform; use Ctrl+C to stop.")
    
    try:
        # Keep server running until shutdown
        async with server:
            await server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Graceful shutdown sequence
        print("\nInitiating graceful shutdown...")
        
        # Close listening socket (stop accepting new connections)
        server.close()
        await server.wait_closed()
        print("Server socket closed")
        
        # Set shutdown event and send sentinel to writer task
        shutdown_event.set()
        await write_queue.put(_WRITER_SENTINEL)
        
        # Wait for writer task to flush queue and exit
        try:
            await asyncio.wait_for(writer_task, timeout=10.0)
        except asyncio.TimeoutError:
            print("Warning: Writer task did not exit within timeout")
            writer_task.cancel()
        
        print("Server stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
