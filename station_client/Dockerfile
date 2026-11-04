FROM python:3.11-slim

WORKDIR /app

# Copy client code (no external dependencies needed)
COPY client.py .

# Set default environment variables (can be overridden in docker-compose)
# WEATHER_STATION_ID is omitted so scaled containers fall back to container HOSTNAME
ENV WEATHER_STATION_HOST=server
ENV WEATHER_STATION_PORT=12345
ENV WEATHER_STATION_BATCH_MIN=1
ENV WEATHER_STATION_BATCH_MAX=1
ENV WEATHER_STATION_BATCH_INTERVAL=5
ENV WEATHER_STATION_TIMEOUT=5.0
ENV PYTHONUNBUFFERED=1

# Run the client
CMD ["python", "client.py"]
