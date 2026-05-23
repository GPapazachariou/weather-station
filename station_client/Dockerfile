FROM python:3.11-slim

WORKDIR /app

# Copy client code (no external dependencies needed)
COPY client.py .

# Set default environment variables (can be overridden in docker-compose)
ENV WEATHER_STATION_HOST=server
ENV WEATHER_STATION_PORT=12345
ENV WEATHER_STATION_ID=STATION-001
ENV WEATHER_STATION_BATCH_MIN=1
ENV WEATHER_STATION_BATCH_MAX=1
ENV WEATHER_STATION_BATCH_INTERVAL=5
ENV WEATHER_STATION_TIMEOUT=5.0
ENV WEATHER_STATION_MAX_RETRIES=3
ENV PYTHONUNBUFFERED=1

# Run the client
CMD ["python", "client.py"]
