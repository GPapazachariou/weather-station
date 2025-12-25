FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY app.py .
COPY protocol.py .
COPY storage.py .

# Create data directory for SQLite database
RUN mkdir -p /app/data

# Expose TCP port
EXPOSE 12345

# Run the server
CMD ["python", "app.py"]
