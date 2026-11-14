"""Phase 0 server behaviour: ping, seq integrity, dual-stack binding, metrics wiring."""

import asyncio
import json
import sqlite3

import app
import healthcheck
import protocol
import pytest


def reading(seq, station="STN-X", temperature=20.0, timestamp="2026-10-02T01:00:00Z"):
    return {
        "station_id": station,
        "seq": seq,
        "timestamp": timestamp,
        "temperature": temperature,
        "humidity": 50.0,
        "windspeed": 5.0,
    }


@pytest.fixture
async def live_server(tmp_path, monkeypatch):
    """A real listener plus writer task on an isolated database. Yields the port."""
    monkeypatch.setattr(app, "DB_FILE", str(tmp_path / "phase0.db"))
    monkeypatch.setattr(app, "write_queue", asyncio.Queue())
    monkeypatch.setattr(app, "shutdown_event", None)
    app.METRICS.reset()
    await app.init_database()
    writer_task = asyncio.create_task(app.db_writer_task())
    listener = await asyncio.start_server(
        app.handle_client, "127.0.0.1", 0, limit=app.MAX_LINE_SIZE + 1
    )
    try:
        yield listener.sockets[0].getsockname()[1]
    finally:
        listener.close()
        await listener.wait_closed()
        await app.write_queue.put(app._WRITER_SENTINEL)
        await writer_task


async def exchange(port, *messages):
    """Send each message on one connection and return the decoded replies."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        replies = []
        for message in messages:
            writer.write((json.dumps(message) + "\n").encode())
            await writer.drain()
            replies.append(json.loads(await asyncio.wait_for(reader.readline(), 5)))
        return replies
    finally:
        writer.close()
        await writer.wait_closed()


def stored(seq, station="STN-X"):
    with sqlite3.connect(app.DB_FILE) as db:
        return db.execute(
            "SELECT temperature FROM readings WHERE station_id = ? AND seq = ?", (station, seq)
        ).fetchall()


# ---------------------------------------------------------------- ping / healthcheck


async def test_ping_returns_pong_without_database(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "DB_FILE", str(tmp_path / "missing-dir" / "never-created.db"))
    response = await app.handle_consumer_request({"request": "ping"}, None)
    assert response["status"] == "ok" and response["pong"] is True
    assert protocol.normalize_utc_timestamp(response["server_time"]) == response["server_time"]


async def test_healthcheck_passes_against_live_server_and_fails_when_down(live_server):
    assert await asyncio.to_thread(healthcheck.check, "127.0.0.1", live_server) is True

    async def not_the_protocol(reader, writer):
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()

    impostor = await asyncio.start_server(not_the_protocol, "127.0.0.1", 0)
    port = impostor.sockets[0].getsockname()[1]
    try:
        # A listening socket alone is not healthy: it must speak the protocol.
        assert await asyncio.to_thread(healthcheck.check, "127.0.0.1", port) is False
    finally:
        impostor.close()
        await impostor.wait_closed()
    assert await asyncio.to_thread(healthcheck.check, "127.0.0.1", port, 0.5) is False


# ---------------------------------------------------------------- seq integrity


async def test_identical_replay_is_duplicate_but_changed_payload_is_conflict(live_server):
    first, replay, conflict = await exchange(
        live_server, [reading(1)], [reading(1)], [reading(1, temperature=35.0)]
    )
    assert first == {"status": "ok", "inserted": 1, "duplicates": 0}
    assert replay == {"status": "ok", "inserted": 0, "duplicates": 1}
    assert conflict["status"] == "error" and conflict["code"] == "conflict"
    assert "station_id=STN-X seq=1" in conflict["reason"]
    assert stored(1) == [(20.0,)]


async def test_replay_with_equivalent_timestamp_and_int_values_is_not_a_conflict(live_server):
    same_instant = reading(1, temperature=20, timestamp="2026-10-02T03:00:00+02:00")
    first, replay = await exchange(live_server, [reading(1)], [same_instant])
    assert first["inserted"] == 1
    assert replay == {"status": "ok", "inserted": 0, "duplicates": 1}


async def test_conflict_rejects_whole_batch(live_server):
    await exchange(live_server, [reading(1)])
    (response,) = await exchange(live_server, [reading(2), reading(1, temperature=1.0)])
    assert response["code"] == "conflict"
    assert stored(2) == []


async def test_conflicting_duplicate_keys_inside_one_batch(live_server):
    same, different = await exchange(
        live_server,
        [reading(1), reading(1)],
        [reading(2), reading(2, temperature=30.0)],
    )
    assert same == {"status": "ok", "inserted": 1, "duplicates": 1}
    assert different["code"] == "conflict"
    assert stored(2) == []


async def test_seq_jump_bound_blocks_cursor_poisoning(live_server, monkeypatch):
    monkeypatch.setattr(app, "MAX_SEQ_JUMP", 100)
    ok, poisoned, at_limit, latest = await exchange(
        live_server,
        [reading(0)],
        [reading(2**62)],
        [reading(100)],
        {"request": "latest_seq", "station_id": "STN-X"},
    )
    assert ok["inserted"] == 1
    assert poisoned["code"] == "seq_out_of_range" and "max_allowed=100" in poisoned["reason"]
    assert at_limit["inserted"] == 1
    assert latest["latest_seq"] == 100


async def test_seq_jump_bound_applies_per_station_and_to_new_stations(live_server, monkeypatch):
    monkeypatch.setattr(app, "MAX_SEQ_JUMP", 10)
    await exchange(live_server, [reading(5, station="OLD")])
    new_ok, new_far, mixed = await exchange(
        live_server,
        [reading(9, station="NEW")],
        [reading(10, station="NEW2")],
        [reading(6, station="OLD"), reading(500, station="NEW")],
    )
    assert new_ok["inserted"] == 1
    assert new_far["code"] == "seq_out_of_range"
    assert mixed["code"] == "seq_out_of_range"
    assert stored(6, "OLD") == []


def test_seq_above_sqlite_integer_range_is_invalid():
    protocol.validate_batch([reading(protocol.MAX_SEQ)])
    with pytest.raises(ValueError, match="invalid seq at index 0"):
        protocol.validate_batch([reading(protocol.MAX_SEQ + 1)])


async def test_client_does_not_retry_rejected_batches(live_server):
    """conflict / seq_out_of_range must not look like backpressure to the station client."""
    import client

    await exchange(live_server, [reading(1)])
    (conflict,) = await exchange(live_server, [reading(1, temperature=2.0)])
    assert client.is_backpressure_response(conflict) is False


# ---------------------------------------------------------------- since ordering


async def test_since_without_station_orders_by_seq_within_each_station(live_server):
    await exchange(
        live_server,
        [reading(3, station="B"), reading(1, station="B")],
        [reading(2, station="A"), reading(4, station="A")],
    )
    (response,) = await exchange(live_server, {"request": "since", "seq": 0})
    assert [(r["station_id"], r["seq"]) for r in response["readings"]] == [
        ("A", 2), ("A", 4), ("B", 1), ("B", 3),
    ]


# ---------------------------------------------------------------- binding


@pytest.mark.parametrize(
    "value, expected",
    [
        ("*", None),
        ("", None),
        (None, None),
        ("0.0.0.0", "0.0.0.0"),
        (" ::1 ", "::1"),
        ("127.0.0.1,::1", ["127.0.0.1", "::1"]),
        ("127.0.0.1, *", None),
    ],
)
def test_resolve_bind_hosts(value, expected):
    assert app.resolve_bind_hosts(value) == expected


async def test_wildcard_host_falls_back_to_ipv4_when_dual_stack_bind_fails(monkeypatch):
    monkeypatch.setattr(app, "HOST", "*")
    monkeypatch.setattr(app, "PORT", 0)
    real = asyncio.start_server
    hosts = []

    async def no_ipv6(handler, host, port, **kwargs):
        hosts.append(host)
        if host is None:
            raise OSError("address family not supported")
        return await real(handler, "127.0.0.1", port, **kwargs)

    monkeypatch.setattr(asyncio, "start_server", no_ipv6)
    listener = await app.start_listener()
    listener.close()
    await listener.wait_closed()
    assert hosts == [None, "0.0.0.0"]


async def test_explicit_host_bind_failure_is_not_masked(monkeypatch):
    monkeypatch.setattr(app, "HOST", "127.0.0.1")

    async def refuse(*args, **kwargs):
        raise OSError("in use")

    monkeypatch.setattr(asyncio, "start_server", refuse)
    with pytest.raises(OSError, match="in use"):
        await app.start_listener()


# ---------------------------------------------------------------- metrics


async def test_metrics_reflect_traffic(live_server):
    await exchange(
        live_server,
        [reading(1), reading(2)],
        [reading(1)],
        [reading(1, temperature=9.0)],
        [{"station_id": ""}],
        {"request": "ping"},
        {"request": "no-such-request"},
    )
    reader, writer = await asyncio.open_connection("127.0.0.1", live_server)
    writer.write(b"not json\n")
    await writer.drain()
    await reader.readline()
    assert app.M_CONNECTIONS_ACTIVE.value() == 1
    writer.close()
    await writer.wait_closed()

    assert app.M_BATCHES.value(result="ok") == 2
    assert app.M_BATCHES.value(result="conflict") == 1
    assert app.M_BATCHES.value(result="invalid") == 2
    assert app.M_READINGS_INSERTED.value() == 2
    assert app.M_READINGS_DUPLICATE.value() == 1
    assert app.M_CONSUMER_REQUESTS.value(request="ping") == 1
    assert app.M_CONSUMER_REQUESTS.value(request="unknown") == 1
    assert app.M_CONNECTIONS_TOTAL.value() == 2
    assert app.M_ACK_LATENCY.count == 4
    assert app.M_COMMIT_LATENCY.count == 3
    assert app.M_BYTES_RECEIVED.value() > 0 and app.M_BYTES_SENT.value() > 0
    assert app.M_QUEUE_DEPTH.value() == 0
    for _ in range(100):
        if app.M_CONNECTIONS_ACTIVE.value() == 0:
            break
        await asyncio.sleep(0.01)
    assert app.M_CONNECTIONS_ACTIVE.value() == 0

    text = app.METRICS.render()
    assert 'weather_batches_total{result="conflict"} 1' in text
    assert "weather_batch_ack_seconds_count 4" in text


async def test_metrics_listener_serves_registry_and_tolerates_port_in_use(monkeypatch):
    blocker = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    taken = blocker.sockets[0].getsockname()[1]
    monkeypatch.setattr(app, "METRICS_HOST", "127.0.0.1")
    try:
        monkeypatch.setattr(app, "METRICS_PORT", taken)
        assert await app.start_metrics_listener() is None
    finally:
        blocker.close()
        await blocker.wait_closed()

    monkeypatch.setattr(app, "METRICS_PORT", 0)
    assert await app.start_metrics_listener() is None  # <= 0 disables

    monkeypatch.setattr(app, "METRICS_PORT", taken)
    listener = await app.start_metrics_listener()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", taken)
        writer.write(b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        body = (await reader.read()).decode()
        writer.close()
        await writer.wait_closed()
        assert body.startswith("HTTP/1.1 200")
        assert "# TYPE weather_write_queue_depth gauge" in body
    finally:
        listener.close()
        await listener.wait_closed()
