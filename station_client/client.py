"""
Weather Station Client
Generates fake weather data and sends it to the server.
Resilient with automatic reconnection, exponential backoff, and buffering.
"""

import asyncio
import json
import random
import os
import argparse
import hashlib
import signal
from collections import deque
from datetime import datetime, timezone


# Default configuration values
DEFAULT_SERVER_HOST = "localhost"
DEFAULT_SERVER_PORT = 12345
DEFAULT_STATION_ID = "NEW_STATION"
DEFAULT_BATCH_SIZE_MIN = 1
DEFAULT_BATCH_SIZE_MAX = 1
DEFAULT_BATCH_INTERVAL = 5
DEFAULT_RESPONSE_TIMEOUT = 5.0
DEFAULT_CONNECT_TIMEOUT = 10.0

# Resilience configuration
DEFAULT_BASE_BACKOFF = 1.0      # Start with 1 second
DEFAULT_MAX_BACKOFF = 60.0      # Max 60 seconds between retries
DEFAULT_BACKOFF_JITTER = 1.0    # Add 0-1 second random jitter
DEFAULT_MAX_BUFFER_RECORDS = 1000  # Max buffered readings
DEFAULT_DRAIN_TIMEOUT = 5.0     # Timeout for writer.drain()


def load_config():
    """
    Load configuration from environment variables, CLI arguments, and defaults.
    Precedence: CLI arguments > environment variables > defaults
    """
    # Read environment variables with prefix WEATHER_STATION_
    env_host = os.getenv("WEATHER_STATION_HOST")
    env_port = os.getenv("WEATHER_STATION_PORT")
    env_station_id = os.getenv("WEATHER_STATION_ID")
    env_batch_min = os.getenv("WEATHER_STATION_BATCH_MIN")
    env_batch_max = os.getenv("WEATHER_STATION_BATCH_MAX")
    env_batch_interval = os.getenv("WEATHER_STATION_BATCH_INTERVAL")
    env_timeout = os.getenv("WEATHER_STATION_TIMEOUT")
    env_connect_timeout = os.getenv("WEATHER_STATION_CONNECT_TIMEOUT")
    
    # Parse CLI arguments
    parser = argparse.ArgumentParser(
        description="Weather Station Client - sends weather data to server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables (override defaults):
  WEATHER_STATION_HOST           Server host (default: localhost)
  WEATHER_STATION_PORT           Server port (default: 12345)
  WEATHER_STATION_ID             Station ID (default: STATION-001)
  WEATHER_STATION_BATCH_MIN      Min batch size (default: 1)
  WEATHER_STATION_BATCH_MAX      Max batch size (default: 1)
  WEATHER_STATION_BATCH_INTERVAL Batch interval in seconds (default: 5)
  WEATHER_STATION_TIMEOUT        Response timeout in seconds (default: 5.0)
  WEATHER_STATION_CONNECT_TIMEOUT Connect timeout in seconds (default: 10.0)

CLI arguments override environment variables.
        """
    )
    
    # Server arguments
    parser.add_argument(
        "--host",
        type=str,
        default=env_host or DEFAULT_SERVER_HOST,
        help="Server host"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(env_port) if env_port else DEFAULT_SERVER_PORT,
        help="Server port"
    )
    
    # Client arguments
    parser.add_argument(
        "--station-id",
        type=str,
        default=env_station_id or DEFAULT_STATION_ID,
        help="Station ID"
    )
    
    # Batching arguments
    parser.add_argument(
        "--batch-min",
        type=int,
        default=int(env_batch_min) if env_batch_min else DEFAULT_BATCH_SIZE_MIN,
        help="Minimum batch size"
    )
    parser.add_argument(
        "--batch-max",
        type=int,
        default=int(env_batch_max) if env_batch_max else DEFAULT_BATCH_SIZE_MAX,
        help="Maximum batch size"
    )
    parser.add_argument(
        "--batch-interval",
        type=float,
        default=float(env_batch_interval) if env_batch_interval else DEFAULT_BATCH_INTERVAL,
        help="Seconds between batches"
    )
    
    # Networking arguments
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(env_timeout) if env_timeout else DEFAULT_RESPONSE_TIMEOUT,
        help="Response timeout in seconds"
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=float(env_connect_timeout) if env_connect_timeout else DEFAULT_CONNECT_TIMEOUT,
        help="Connection timeout in seconds"
    )
    
    args = parser.parse_args()
    
    # Validate configuration values
    if args.batch_min <= 0 or args.batch_max <= 0:
        parser.error("Batch sizes must be positive")
    if args.batch_min > args.batch_max:
        parser.error("Batch min must be <= batch max")
    if args.batch_interval <= 0:
        parser.error("Batch interval must be positive")
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    if args.connect_timeout <= 0:
        parser.error("Connect timeout must be positive")
    if args.port < 0 or args.port > 65535:
        parser.error("Port must be between 0 and 65535")
    
    return {
        "server_host": args.host,
        "server_port": args.port,
        "station_id": args.station_id,
        "batch_size_min": args.batch_min,
        "batch_size_max": args.batch_max,
        "batch_interval": args.batch_interval,
        "response_timeout": args.timeout,
        "connect_timeout": args.connect_timeout,
        "base_backoff": DEFAULT_BASE_BACKOFF,
        "max_backoff": DEFAULT_MAX_BACKOFF,
        "backoff_jitter": DEFAULT_BACKOFF_JITTER,
        "max_buffer_records": DEFAULT_MAX_BUFFER_RECORDS,
        "drain_timeout": DEFAULT_DRAIN_TIMEOUT,
    }


def stable_hash_int(s):
    """
    Generate a stable integer hash from a string using SHA256.
    Returns an integer derived from the first 8 hex characters.
    """
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def clamp(x, lo, hi):
    """Clamp x to range [lo, hi]."""
    return max(lo, min(hi, x))


def mean_for(station_id, metric, lo, hi):
    """
    Calculate deterministic mean value for a station and metric.
    
    Args:
        station_id: Station identifier
        metric: Metric name
        lo: Lower bound of range
        hi: Upper bound of range
    
    Returns:
        float: Deterministic mean value in range [lo, hi]
    """
    base = stable_hash_int(f"{station_id}:{metric}") % 50
    return lo + (base / 49.0) * (hi - lo)


def value_for(station_id, metric):
    """
    Generate a sensor value with deterministic mean + random noise.
    
    Args:
        station_id: Station identifier
        metric: Metric name ('temperature', 'humidity', 'windspeed')
    
    Returns:
        float: Value with random noise, clamped to valid range
    """
    # Define ranges and noise for each metric
    if metric == "temperature":
        lo, hi, noise_std = -10.0, 40.0, 0.6
    elif metric == "humidity":
        lo, hi, noise_std = 0.0, 100.0, 2.5
    elif metric == "windspeed":
        lo, hi, noise_std = 0.0, 50.0, 1.2
    else:
        raise ValueError(f"Unknown metric: {metric}")
    
    # Get deterministic mean
    mean = mean_for(station_id, metric, lo, hi)
    
    # Add random noise
    value = mean + random.gauss(0, noise_std)
    
    # Clamp and round
    value = clamp(value, lo, hi)
    return round(value, 2)


def generate_reading(station_id):
    """
    Generate a single weather reading with deterministic mean + random noise.
    
    Args:
        station_id: Station identifier
    """
    return {
        "station_id": station_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "temperature": value_for(station_id, "temperature"),
        "humidity": value_for(station_id, "humidity"),
        "windspeed": value_for(station_id, "windspeed"),
    }


def generate_batch(station_id, size):
    """
    Generate a batch of weather readings.
    
    Args:
        station_id: Station identifier
        size: Number of readings in batch
    """
    return [generate_reading(station_id) for _ in range(size)]


async def send_batches(config):
    """
    Resilient client with infinite reconnection loop, exponential backoff,
    and in-memory buffering when disconnected.
    """
    server_host = config["server_host"]
    server_port = config["server_port"]
    station_id = config["station_id"]
    batch_size_min = config["batch_size_min"]
    batch_size_max = config["batch_size_max"]
    batch_interval = config["batch_interval"]
    response_timeout = config["response_timeout"]
    connect_timeout = config["connect_timeout"]
    base_backoff = config["base_backoff"]
    max_backoff = config["max_backoff"]
    backoff_jitter = config["backoff_jitter"]
    max_buffer_records = config["max_buffer_records"]
    drain_timeout = config["drain_timeout"]
    
    # Buffer for storing batches when disconnected
    buffer = deque()
    
    # Backoff state
    current_backoff = base_backoff
    
    # Shutdown flag
    shutdown = False
    
    def handle_shutdown(signum, frame):
        nonlocal shutdown
        print("\n\n⚠ Shutdown signal received, stopping client...")
        shutdown = True
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    batch_num = 1
    
    print(f"Weather Station Client: {station_id}")
    print(f"Target: {server_host}:{server_port}")
    print(f"Batch interval: {batch_interval}s")
    print(f"Buffer limit: {max_buffer_records} records")
    print()
    
    # Outer infinite loop - keeps client alive forever
    while not shutdown:
        reader = None
        writer = None
        
        try:
            # Attempt connection with timeout
            print(f"[{station_id}] Connecting to {server_host}:{server_port}...")
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(server_host, server_port),
                timeout=connect_timeout
            )
            print(f"[{station_id}] ✓ Connected to server")
            
            # Reset backoff on successful connection
            current_backoff = base_backoff
            
            # Flush buffered batches first
            if buffer:
                print(f"[{station_id}] Flushing {len(buffer)} buffered batches...")
                while buffer and not shutdown:
                    try:
                        buffered_batch = buffer.popleft()
                        json_line = json.dumps(buffered_batch) + "\n"
                        
                        writer.write(json_line.encode('utf-8'))
                        await asyncio.wait_for(writer.drain(), timeout=drain_timeout)
                        
                        # Read response
                        response_line = await asyncio.wait_for(
                            reader.readline(),
                            timeout=response_timeout
                        )
                        
                        if not response_line:
                            raise ConnectionError("Server closed connection")
                        
                        response = json.loads(response_line.decode('utf-8'))
                        
                        if response["status"] == "ok":
                            print(f"[{station_id}] ✓ Flushed batch ({response['inserted']} readings)")
                        else:
                            print(f"[{station_id}] ✗ Server rejected buffered batch: {response.get('reason', 'unknown')}")
                    
                    except (OSError, ConnectionError, asyncio.TimeoutError, BrokenPipeError) as e:
                        print(f"[{station_id}] ✗ Connection lost during flush: {e}")
                        # Put batch back in buffer
                        buffer.appendleft(buffered_batch)
                        raise
                
                print(f"[{station_id}] ✓ Buffer flushed successfully")
            
            # Inner loop - connected state, send batches normally
            while not shutdown:
                # Generate a batch
                batch_size = random.randint(batch_size_min, batch_size_max)
                batch = generate_batch(station_id, batch_size)
                
                # Convert to JSON line
                json_line = json.dumps(batch) + "\n"
                
                try:
                    # Send to server
                    writer.write(json_line.encode('utf-8'))
                    await asyncio.wait_for(writer.drain(), timeout=drain_timeout)
                    
                    # Read server response with timeout
                    response_line = await asyncio.wait_for(
                        reader.readline(),
                        timeout=response_timeout
                    )
                    
                    if not response_line:
                        raise ConnectionError("Server closed connection")
                    
                    response = json.loads(response_line.decode('utf-8'))
                    
                    # Print response
                    if response["status"] == "ok":
                        print(f"[{station_id}] Batch #{batch_num}: ✓ Sent {batch_size} readings, server inserted {response['inserted']}")
                    else:
                        print(f"[{station_id}] Batch #{batch_num}: ✗ Server error: {response.get('reason', 'unknown')}")
                    
                    batch_num += 1
                    
                    # Wait before next batch
                    await asyncio.sleep(batch_interval)
                
                except (OSError, ConnectionError, asyncio.TimeoutError, BrokenPipeError) as e:
                    print(f"[{station_id}] ✗ Connection error during send: {type(e).__name__}: {e}")
                    
                    # Buffer the failed batch
                    if len(buffer) < max_buffer_records:
                        buffer.append(batch)
                        print(f"[{station_id}] → Buffered batch (buffer size: {len(buffer)})")
                    else:
                        # Drop oldest batch to prevent unbounded memory growth
                        dropped = buffer.popleft()
                        buffer.append(batch)
                        print(f"[{station_id}] ⚠ Buffer full! Dropped oldest batch ({len(dropped)} readings), buffer size: {len(buffer)}")
                    
                    # Break inner loop to reconnect
                    raise
        
        except (OSError, ConnectionError, asyncio.TimeoutError, BrokenPipeError, ConnectionRefusedError) as e:
            # Connection failed or lost
            if not shutdown:
                print(f"[{station_id}] ✗ Connection failed: {type(e).__name__}: {e}")
                
                # Calculate backoff with jitter
                jitter = random.uniform(0, backoff_jitter)
                wait_time = current_backoff + jitter
                
                print(f"[{station_id}] → Retrying in {wait_time:.1f}s (backoff: {current_backoff:.1f}s + jitter: {jitter:.1f}s)")
                
                # While disconnected, keep generating data and buffering
                print(f"[{station_id}] → Buffering mode active (buffer: {len(buffer)} batches)")
                
                # Wait with periodic batch generation
                waited = 0.0
                while waited < wait_time and not shutdown:
                    sleep_interval = min(batch_interval, wait_time - waited)
                    await asyncio.sleep(sleep_interval)
                    waited += sleep_interval
                    
                    # Generate and buffer a batch
                    if waited >= batch_interval:
                        batch_size = random.randint(batch_size_min, batch_size_max)
                        batch = generate_batch(station_id, batch_size)
                        
                        if len(buffer) < max_buffer_records:
                            buffer.append(batch)
                            print(f"[{station_id}] → Generated & buffered batch (buffer: {len(buffer)} batches)")
                        else:
                            dropped = buffer.popleft()
                            buffer.append(batch)
                            print(f"[{station_id}] ⚠ Buffer full! Dropped oldest, buffered new (buffer: {len(buffer)} batches)")
                
                # Exponential backoff - double the backoff time
                current_backoff = min(current_backoff * 2, max_backoff)
        
        finally:
            # Clean up connection
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
    
    print(f"[{station_id}] Client shutdown complete.")
    if buffer:
        print(f"[{station_id}] ⚠ Warning: {len(buffer)} buffered batches were not sent")


async def main():
    """Run the weather station client."""
    config = load_config()
    await send_batches(config)


if __name__ == "__main__":
    asyncio.run(main())
