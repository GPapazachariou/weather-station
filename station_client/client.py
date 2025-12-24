"""
Weather Station Client
Generates fake weather data and sends it to the server.
"""

import asyncio
import json
import random
import os
import argparse
from datetime import datetime, timezone


# Default configuration values
DEFAULT_SERVER_HOST = "localhost"
DEFAULT_SERVER_PORT = 12345
DEFAULT_STATION_ID = "STATION-001"
DEFAULT_BATCH_SIZE_MIN = 3
DEFAULT_BATCH_SIZE_MAX = 5
DEFAULT_BATCH_INTERVAL = 2
DEFAULT_RESPONSE_TIMEOUT = 5.0
DEFAULT_MAX_RETRIES = 3


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
    env_max_retries = os.getenv("WEATHER_STATION_MAX_RETRIES")
    
    # Parse CLI arguments
    parser = argparse.ArgumentParser(
        description="Weather Station Client - sends weather data to server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables (override defaults):
  WEATHER_STATION_HOST           Server host (default: localhost)
  WEATHER_STATION_PORT           Server port (default: 12345)
  WEATHER_STATION_ID             Station ID (default: STATION-001)
  WEATHER_STATION_BATCH_MIN      Min batch size (default: 3)
  WEATHER_STATION_BATCH_MAX      Max batch size (default: 5)
  WEATHER_STATION_BATCH_INTERVAL Batch interval in seconds (default: 2)
  WEATHER_STATION_TIMEOUT        Response timeout in seconds (default: 5.0)
  WEATHER_STATION_MAX_RETRIES    Max retries per batch (default: 3)

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
        "--max-retries",
        type=int,
        default=int(env_max_retries) if env_max_retries else DEFAULT_MAX_RETRIES,
        help="Maximum retries per batch"
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
    if args.max_retries < 0:
        parser.error("Max retries must be non-negative")
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
        "max_retries": args.max_retries,
    }


def generate_reading(station_id):
    """
    Generate a single fake weather reading with realistic values.
    """
    return {
        "station_id": station_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "temperature": round(random.uniform(-10.0, 35.0), 1),  # Celsius
        "humidity": round(random.uniform(20.0, 95.0), 1),      # Percentage
        "windspeed": round(random.uniform(0.0, 25.0), 1)       # m/s
    }


def generate_batch(station_id, size):
    """
    Generate a batch of weather readings.
    """
    return [generate_reading(station_id) for _ in range(size)]


async def send_batches(config):
    """
    Connect to the server and send batches of weather data.
    """
    server_host = config["server_host"]
    server_port = config["server_port"]
    station_id = config["station_id"]
    batch_size_min = config["batch_size_min"]
    batch_size_max = config["batch_size_max"]
    batch_interval = config["batch_interval"]
    response_timeout = config["response_timeout"]
    max_retries = config["max_retries"]
    
    print(f"Connecting to {server_host}:{server_port}...")
    
    reader = None
    writer = None
    batch_num = 1
    
    try:
        # Connect to server
        reader, writer = await asyncio.open_connection(server_host, server_port)
        print(f"Connected! Sending data from {station_id}\n")
        
        while True:
            # Generate a batch
            batch_size = random.randint(batch_size_min, batch_size_max)
            batch = generate_batch(station_id, batch_size)
            
            # Convert to JSON line
            json_line = json.dumps(batch) + "\n"
            
            # Try sending with retries on timeout
            retry_count = 0
            success = False
            
            while retry_count < max_retries and not success:
                try:
                    # Send to server
                    print(f"Batch #{batch_num} - Sending {batch_size} readings...")
                    writer.write(json_line.encode('utf-8'))
                    await writer.drain()
                    
                    # Read server response with timeout
                    response_line = await asyncio.wait_for(
                        reader.readline(),
                        timeout=response_timeout
                    )
                    response = json.loads(response_line.decode('utf-8'))
                    
                    # Print response
                    if response["status"] == "ok":
                        print(f"✓ Server accepted {response['inserted']} readings")
                    else:
                        print(f"✗ Server error: {response.get('reason', 'unknown')}")
                    
                    success = True
                    
                except asyncio.TimeoutError:
                    retry_count += 1
                    print(f"⚠ Timeout waiting for server response (attempt {retry_count}/{max_retries})")
                    
                    if retry_count < max_retries:
                        # Close old connection
                        if writer:
                            writer.close()
                            await writer.wait_closed()
                        
                        # Reconnect
                        print(f"Reconnecting to {server_host}:{server_port}...")
                        try:
                            reader, writer = await asyncio.open_connection(server_host, server_port)
                            print("Reconnected! Retrying batch...")
                        except Exception as e:
                            print(f"Reconnection failed: {e}")
                            raise
                    else:
                        print(f"✗ Failed after {max_retries} attempts, skipping batch")
            
            print()
            
            # Wait before next batch
            batch_num += 1
            await asyncio.sleep(batch_interval)
    
    except ConnectionRefusedError:
        print(f"Error: Could not connect to server at {server_host}:{server_port}")
        print("Make sure the server is running.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()
            print("Connection closed.")


async def main():
    """Run the weather station client."""
    config = load_config()
    await send_batches(config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nClient stopped by user")
