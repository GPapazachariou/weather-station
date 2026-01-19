# Server Log Analysis - Connect/Disconnect Behavior

## Observed Behavior

The server logs show two distinct connection patterns:

### Pattern 1: Persistent Connections (Station Clients)
```
Client connected: ('172.18.0.5', 47046)  ← Docker station-001
Client connected: ('172.18.0.6', 33884)  ← Docker station-002
Client connected: ('172.18.0.4', 40952)  ← Docker station-003
```

These are the **3 weather station clients** running in Docker containers:
- IP addresses from Docker bridge network (172.18.0.x range)
- Connect once and maintain persistent connection
- Send batches every 5 seconds continuously
- Connection stays alive between batch sends
- **This is the intended behavior** for long-lived producer connections

### Pattern 2: Rapid Connect/Disconnect (Health Checks)
```
Client connected: ('127.0.0.1', 36582)
Client disconnected: ('127.0.0.1', 36582)
Client connected: ('127.0.0.1', 56210)
Client disconnected: ('127.0.0.1', 56210)
Client connected: ('127.0.0.1', 58944)
Client disconnected: ('127.0.0.1', 58944)
```

These brief localhost connections are **Docker health checks** defined in docker-compose.yml:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import socket; s=socket.socket(); s.connect(('localhost',12345)); s.close()"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 10s
```

The health check:
- Connects from localhost (127.0.0.1) inside the container
- Opens TCP connection to port 12345
- Immediately closes connection
- Runs every 30 seconds
- Verifies server is listening and accepting connections

## Conclusion

✅ **All behavior is normal and expected:**

1. Station clients maintain persistent connections (correct)
2. Batches are being processed successfully every ~5 seconds (correct)
3. Health checks verify server availability every 30s (correct)
4. No errors, timeouts, or connection failures observed

## Optional Adjustments

If you want to reduce health check frequency in logs:

```yaml
healthcheck:
  interval: 60s  # Change from 30s to 60s
```

Or disable health checks entirely (not recommended for production):

```yaml
# Comment out or remove healthcheck section
```

## Data Flow Summary

```
Station Clients (172.18.0.x)
    ↓ persistent connection
    ↓ batch every 5s
TCP Server (port 12345)
    ↓ validate + enqueue
    ↓ single-writer task
SQLite Database (weather.db)

Health Check (127.0.0.1)
    ↓ connect every 30s
TCP Server (port 12345)
    ↓ immediate close
Health Status: OK
```

**Status:** System operating normally ✅
