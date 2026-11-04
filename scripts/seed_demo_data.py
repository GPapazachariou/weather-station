"""Script to create a rich SQLite database for browser validation and testing.
Seeds:
- STATION-ALPHA: Dense dataset (800 points over 48h) with distinct peaks and troughs.
- STATION-BETA: Sparse dataset with a multi-hour outage to test missing data gaps.
"""
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

db_dir = Path(__file__).parent.parent / "data"
db_dir.mkdir(parents=True, exist_ok=True)
db_path = db_dir / "weather.db"

conn = sqlite3.connect(str(db_path))
c = conn.cursor()

c.execute("DROP TABLE IF EXISTS readings")
c.execute("""
    CREATE TABLE readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        station_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        temperature REAL NOT NULL,
        humidity REAL NOT NULL,
        windspeed REAL NOT NULL
    )
""")
c.execute("CREATE INDEX IF NOT EXISTS idx_readings_station_ts ON readings(station_id, timestamp DESC)")
c.execute("CREATE INDEX IF NOT EXISTS idx_readings_station_id_desc ON readings(station_id, id DESC)")

now = datetime.now(timezone.utc)

# 1. STATION-ALPHA: 800 points over 48 hours (dense data)
alpha_records = []
total_alpha = 800
interval_alpha = (48 * 3600) / total_alpha

for i in range(total_alpha):
    ts = now - timedelta(seconds=(total_alpha - 1 - i) * interval_alpha)
    # Sine waves with temperature peak and trough
    temp = 20.0 + 8.0 * math.sin(i / 30.0) + 2.0 * math.cos(i / 10.0)
    # Inject noticeable sharp peak and trough
    if i == 250:
        temp = 34.5  # sharp peak
    elif i == 550:
        temp = 8.2   # sharp trough

    hum = 55.0 + 25.0 * math.cos(i / 35.0)
    wind = 6.0 + 4.5 * math.sin(i / 20.0) + (i % 7) * 0.4

    alpha_records.append((
        "STATION-ALPHA",
        ts.isoformat(),
        round(temp, 2),
        round(max(10.0, min(100.0, hum)), 2),
        round(max(0.0, wind), 2)
    ))

c.executemany(
    "INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed) VALUES (?, ?, ?, ?, ?)",
    alpha_records
)

# 2. STATION-BETA: Sparse data with a 4-hour gap
beta_records = []
# 30 records from -12h to -8h
for i in range(30):
    ts = now - timedelta(hours=12) + timedelta(minutes=i * 8)
    beta_records.append(("STATION-BETA", ts.isoformat(), 18.0 + (i % 5) * 0.5, 60.0 + (i % 4), 3.0 + (i % 3) * 0.3))

# GAP from -8h to -4h (station went offline)

# 30 records from -4h to now
for i in range(30):
    ts = now - timedelta(hours=4) + timedelta(minutes=i * 8)
    beta_records.append(("STATION-BETA", ts.isoformat(), 21.0 + (i % 6) * 0.6, 65.0 - (i % 5), 4.5 + (i % 4) * 0.2))

c.executemany(
    "INSERT INTO readings (station_id, timestamp, temperature, humidity, windspeed) VALUES (?, ?, ?, ?, ?)",
    beta_records
)

conn.commit()
conn.close()
print(f"Successfully seeded {len(alpha_records)} records for STATION-ALPHA and {len(beta_records)} for STATION-BETA at {db_path}")
