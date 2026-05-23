FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy web app code
COPY web.py .
COPY templates/ templates/
COPY static/ static/

# Set default environment variables
ENV WEATHER_DB_PATH=../data/weather.db
ENV WEB_HOST=0.0.0.0
ENV WEB_PORT=8000
ENV PYTHONUNBUFFERED=1

# Expose web port
EXPOSE 8000

# Run the web app
CMD ["python", "web.py"]
