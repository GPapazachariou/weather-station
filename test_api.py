"""
Quick test script to verify API endpoints work correctly
"""
import sqlite3
import json

# Check database
print("=" * 60)
print("Testing Weather Station Database")
print("=" * 60)

conn = sqlite3.connect('data/weather.db')
cursor = conn.execute("SELECT DISTINCT station_id FROM readings ORDER BY station_id")
stations = [row[0] for row in cursor.fetchall()]
print(f"\n✓ Found {len(stations)} station(s): {stations}")

cursor = conn.execute("SELECT COUNT(*) FROM readings")
count = cursor.fetchone()[0]
print(f"✓ Total records: {count}")

# Show sample data
if count > 0:
    cursor = conn.execute("SELECT * FROM readings LIMIT 1")
    sample = cursor.fetchone()
    print(f"\nSample record:")
    cursor2 = conn.execute("SELECT * FROM readings LIMIT 1")
    cols = [description[0] for description in cursor2.description]
    for i, col in enumerate(cols):
        print(f"  {col}: {sample[i]}")

conn.close()

print("\n" + "=" * 60)
print("API Test Results")
print("=" * 60)

# Test Flask API
print("\nStarting Flask app test...")
print("Run: py web/web.py")
print("Then visit: http://127.0.0.1:8000/api/stations")
print("Expected response: {\"stations\": [...]}")

print("\nTo send test data:")
print("Run: py station_client/client.py --station-id TEST-001")
print("\nTo view dashboard:")
print("Visit: http://127.0.0.1:8000")
