"""
Test script for consumer requests over the weather station TCP server.
Tests both producer (batch ingest) and consumer (read-only) request paths.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone

HOST = "localhost"
PORT = 12345


async def send_request(request):
    """Send a request to the server and receive response."""
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
        
        # Send request as newline-delimited JSON
        msg = json.dumps(request) + "\n"
        writer.write(msg.encode('utf-8'))
        await writer.drain()
        
        # Read response
        response_line = await reader.readline()
        if not response_line:
            print("  ERROR: No response from server")
            return None
        
        response = json.loads(response_line.decode('utf-8').strip())
        
        writer.close()
        await writer.wait_closed()
        
        return response
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


TEST_STATION = "CONSUMER-TEST"


async def next_test_seq():
    """Next unused seq for TEST_STATION, so reruns never reuse a stored key."""
    response = await send_request({"request": "latest_seq", "station_id": TEST_STATION})
    latest = (response or {}).get("latest_seq")
    return 0 if latest is None else latest + 1


def make_reading(seq, temperature=22.5):
    return {
        "station_id": TEST_STATION,
        "seq": seq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "temperature": temperature,
        "humidity": 60,
        "windspeed": 5.2,
    }


async def test_producer_ingest():
    """Test producer batch ingest (existing functionality)."""
    print("\n=== Test 1: Producer Batch Ingest ===")

    seq = await next_test_seq()
    batch = [make_reading(seq), make_reading(seq + 1, temperature=18.3)]

    response = await send_request(batch)
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "inserted" in response:
            print(f"  ✓ Producer batch ingest successful: {response['inserted']} readings inserted (duplicates: {response.get('duplicates', 0)})")
            return True
    
    print("  ✗ Producer batch ingest failed")
    return False


async def test_consumer_stations():
    """Test consumer request: list all stations."""
    print("\n=== Test 2: Consumer Request - Stations ===")
    
    request = {"request": "stations"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "stations" in response:
            print(f"  ✓ Consumer stations request successful: {response['stations']}")
            return True
    
    print("  ✗ Consumer stations request failed")
    return False


async def test_consumer_latest():
    """Test consumer request: latest reading across all stations."""
    print("\n=== Test 3: Consumer Request - Latest (All Stations) ===")
    
    request = {"request": "latest"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "reading" in response:
            print("  ✓ Consumer latest request successful")
            return True
    
    print("  ✗ Consumer latest request failed")
    return False


async def test_consumer_latest_station():
    """Test consumer request: latest reading for specific station."""
    print("\n=== Test 4: Consumer Request - Latest (Specific Station) ===")
    
    request = {"request": "latest", "station_id": "STATION-001"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "reading" in response:
            print("  ✓ Consumer latest station request successful")
            return True
    
    print("  ✗ Consumer latest station request failed")
    return False


async def test_consumer_recent():
    """Test consumer request: recent readings with limit."""
    print("\n=== Test 5: Consumer Request - Recent (Default Limit) ===")
    
    request = {"request": "recent"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "readings" in response:
            print(f"  ✓ Consumer recent request successful: {len(response['readings'])} readings")
            return True
    
    print("  ✗ Consumer recent request failed")
    return False


async def test_consumer_recent_limit():
    """Test consumer request: recent readings with custom limit."""
    print("\n=== Test 6: Consumer Request - Recent (Custom Limit) ===")
    
    request = {"request": "recent", "limit": 1}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "readings" in response:
            count = len(response['readings'])
            print(f"  ✓ Consumer recent limit request successful: {count} readings (limit 1)")
            return True
    
    print("  ✗ Consumer recent limit request failed")
    return False


async def test_consumer_recent_station():
    """Test consumer request: recent readings for specific station."""
    print("\n=== Test 7: Consumer Request - Recent (Specific Station) ===")
    
    request = {"request": "recent", "station_id": "STATION-001", "limit": 5}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "readings" in response:
            print(f"  ✓ Consumer recent station request successful: {len(response['readings'])} readings")
            return True
    
    print("  ✗ Consumer recent station request failed")
    return False


async def test_consumer_latest_seq():
    """Test consumer request: latest_seq for specific station."""
    print("\n=== Test 8: Consumer Request - Latest Seq ===")
    
    request = {"request": "latest_seq", "station_id": "STATION-001"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "latest_seq" in response:
            print(f"  ✓ Consumer latest_seq request successful: latest_seq={response['latest_seq']}")
            return True
    
    print("  ✗ Consumer latest_seq request failed")
    return False


async def test_consumer_since():
    """Test consumer request: replay since sequence number."""
    print("\n=== Test 9: Consumer Request - Since (Replay) ===")
    
    request = {"request": "since", "station_id": "STATION-001", "seq": 0, "limit": 10}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and "readings" in response:
            print(f"  ✓ Consumer since replay request successful: {len(response['readings'])} readings returned")
            return True
    
    print("  ✗ Consumer since replay request failed")
    return False


async def test_invalid_consumer_request():
    """Test consumer request with unknown type."""
    print("\n=== Test 10: Invalid Consumer Request ===")
    
    request = {"request": "unknown_type"}
    response = await send_request(request)
    
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "error" and "unknown_request" in response.get("reason", ""):
            print("  ✓ Unknown request properly rejected")
            return True
    
    print("  ✗ Unknown request handling failed")
    return False


async def test_ping():
    """Test liveness probe: ping returns pong without touching the database."""
    print("\n=== Test 11: Ping ===")

    response = await send_request({"request": "ping"})
    if response:
        print(f"  Response: {json.dumps(response, indent=2)}")
        if response.get("status") == "ok" and response.get("pong") is True:
            print("  ✓ Ping answered with pong")
            return True

    print("  ✗ Ping failed")
    return False


async def test_seq_conflict():
    """Test integrity: identical replay is a duplicate, a different payload is a conflict."""
    print("\n=== Test 12: Replay vs. Conflict ===")

    reading = make_reading(await next_test_seq())
    first = await send_request([reading])
    replay = await send_request([reading])
    conflict = await send_request([{**reading, "temperature": reading["temperature"] + 1}])
    print(f"  First:    {first}")
    print(f"  Replay:   {replay}")
    print(f"  Conflict: {conflict}")

    if (
        first and first.get("inserted") == 1
        and replay and replay.get("duplicates") == 1
        and conflict and conflict.get("code") == "conflict"
    ):
        print("  ✓ Replay deduplicated and conflicting payload rejected")
        return True

    print("  ✗ Replay/conflict handling failed")
    return False


async def main():
    """Run all tests."""
    print("=" * 50)
    print("Weather Station Consumer Tests")
    print("=" * 50)
    print(f"Server: {HOST}:{PORT}")
    
    tests = [
        test_producer_ingest,
        test_consumer_stations,
        test_consumer_latest,
        test_consumer_latest_station,
        test_consumer_recent,
        test_consumer_recent_limit,
        test_consumer_recent_station,
        test_consumer_latest_seq,
        test_consumer_since,
        test_invalid_consumer_request,
        test_ping,
        test_seq_conflict,
    ]
    
    results = []
    for test_func in tests:
        try:
            result = await test_func()
            results.append(result)
            await asyncio.sleep(0.1)  # Small delay between tests
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            results.append(False)
    
    # Summary
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 50)
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
