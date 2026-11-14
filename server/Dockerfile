FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY app.py .
COPY protocol.py .
COPY telemetry.py .
COPY healthcheck.py .

# Create data directory for SQLite database
RUN mkdir -p /app/data

# Enable Python unbuffered mode for real-time logging
ENV PYTHONUNBUFFERED=1

# Expose TCP port
EXPOSE 12345
# Prometheus metrics
EXPOSE 9100

# Run the server
CMD ["python", "app.py"]
