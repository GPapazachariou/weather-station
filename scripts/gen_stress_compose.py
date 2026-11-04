#!/usr/bin/env python3
"""
Generate a stress-test docker-compose.stress.yml for the Weather Station Simulator.

Produces a modern Docker Compose v2 configuration with a single scalable `station`
service using `deploy.replicas: N`. Each container replica derives its unique
station_id automatically from its container HOSTNAME.

Usage:
    python scripts/gen_stress_compose.py [--stations N]

Examples:
    python scripts/gen_stress_compose.py                # default: 50 station replicas
    python scripts/gen_stress_compose.py --stations 100 # 100 station replicas

You can also scale directly without regenerating the file:
    docker compose -f docker-compose.stress.yml up --build --scale station=100
"""

import argparse
import sys
from pathlib import Path


def generate_compose_yaml(num_stations: int) -> str:
    """Generate the replica-based docker-compose.stress.yml content."""
    return f"""# ============================================================================
# STRESS TEST DOCKER COMPOSE FOR WEATHER STATION SIMULATOR
# ============================================================================
# Runs a scalable fleet of weather station containers using a single service
# definition. Each station replica automatically uses its Docker container
# HOSTNAME as its unique station_id.
#
# USAGE:
#   Default ({num_stations} station replicas):
#     docker compose -f docker-compose.stress.yml up --build
#
#   Custom scale on the fly (e.g., 100 stations):
#     docker compose -f docker-compose.stress.yml up --build --scale station=100
#
#   Clean up:
#     docker compose -f docker-compose.stress.yml down -v
# ============================================================================

name: weather-stress

services:
  # TCP Server - receives weather data and stores in SQLite
  server:
    build:
      context: ./server
      dockerfile: Dockerfile
    ports:
      - "12345:12345"
    volumes:
      - weather-stress-data:/app/data
    environment:
      - SERVER_HOST=0.0.0.0
      - SERVER_PORT=12345
      - DB_PATH=/app/data/weather.db
      - PYTHONUNBUFFERED=1
    stop_grace_period: 20s
    restart: unless-stopped
    networks:
      - weather-network
    healthcheck:
      test: ["CMD", "python", "-c", "import socket; s=socket.socket(); s.connect(('localhost',12345)); s.close()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

  # Web Dashboard - Flask UI for viewing data
  web:
    build:
      context: ./web
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    volumes:
      - weather-stress-data:/app/data:ro
    environment:
      - WEATHER_DB_PATH=/app/data/weather.db
      - WEB_HOST=0.0.0.0
      - WEB_PORT=8000
      - PYTHONUNBUFFERED=1
    depends_on:
      - server
    restart: unless-stopped
    networks:
      - weather-network

  # Scalable Weather Station Fleet
  # Omit WEATHER_STATION_ID so each replica derives its ID from container HOSTNAME
  station:
    build:
      context: ./station_client
      dockerfile: Dockerfile
    environment:
      - WEATHER_STATION_HOST=server
      - WEATHER_STATION_PORT=12345
      - WEATHER_STATION_BATCH_MIN=1
      - WEATHER_STATION_BATCH_MAX=5
      - WEATHER_STATION_BATCH_INTERVAL=1
      - WEATHER_STATION_TIMEOUT=5.0
      - PYTHONUNBUFFERED=1
    deploy:
      replicas: {num_stations}
    depends_on:
      server:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - weather-network

volumes:
  weather-stress-data:
    driver: local

networks:
  weather-network:
    driver: bridge
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate a stress-test docker-compose.stress.yml for Weather Station Simulator"
    )
    parser.add_argument(
        "--stations",
        type=int,
        default=50,
        help="Number of weather station replicas to configure (default: 50)",
    )

    args = parser.parse_args()

    if args.stations < 1:
        print("Error: --stations must be at least 1", file=sys.stderr)
        sys.exit(1)

    if args.stations > 500:
        print("Warning: Creating more than 500 stations may cause performance issues", file=sys.stderr)

    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    output_file = repo_root / "docker-compose.stress.yml"

    print(f"Generating docker-compose.stress.yml with {args.stations} station replicas...")

    yaml_content = generate_compose_yaml(args.stations)
    output_file.write_text(yaml_content, encoding="utf-8")

    print(f"✓ Generated: {output_file}")
    print("\nTo run the stress test:")
    print("  docker compose -f docker-compose.stress.yml up --build")
    print(f"  docker compose -f docker-compose.stress.yml up --build --scale station={args.stations}")
    print("\nTo clean up:")
    print("  docker compose -f docker-compose.stress.yml down -v")


if __name__ == "__main__":
    main()
