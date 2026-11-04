"""Delivery guarantees exercised through real streams and SQLite transactions."""

import asyncio
import json
import signal
import sqlite3
from collections import deque

import app
import client
import pytest


async def test_eof_restores_buffered_batch_exactly_once():
    async def close_without_ack(reader, writer):
        await reader.readline()
        writer.close()
        await writer.wait_closed()

    listener = await asyncio.start_server(close_without_ack, "127.0.0.1", 0)
    batch = client.generate_batch("RECOVERY", 2, start_seq=1)
    buffer = deque([batch])
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", listener.sockets[0].getsockname()[1]
        )
        try:
            with pytest.raises(ConnectionError):
                await client.flush_buffer(reader, writer, buffer, "RECOVERY", 1, 1, lambda: False)
            assert list(buffer) == [batch]
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        listener.close()
        await listener.wait_closed()


async def test_writer_ack_timeout_keeps_batch_for_retry():
    async def timeout_ack(reader, writer):
        await reader.readline()
        writer.write(b'{"status":"error","reason":"server_error: write_ack_timeout"}\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    listener = await asyncio.start_server(timeout_ack, "127.0.0.1", 0)
    batch = client.generate_batch("RECOVERY", 1, start_seq=1)
    buffer = deque([batch])
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", listener.sockets[0].getsockname()[1]
        )
        try:
            status, _ = await client.flush_buffer(reader, writer, buffer, "RECOVERY", 1, 1, lambda: False)
            assert status == "backpressure"
            assert list(buffer) == [batch]
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        listener.close()
        await listener.wait_closed()


async def test_failed_batch_is_rolled_back_before_next_commit(tmp_path, monkeypatch):
    db_path = tmp_path / "atomic.db"
    monkeypatch.setattr(app, "DB_FILE", str(db_path))
    await app.init_database()
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON readings
                      WHEN NEW.seq = 2 BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    monkeypatch.setattr(app, "write_queue", asyncio.Queue())
    task = asyncio.create_task(app.db_writer_task())
    try:
        with pytest.raises(Exception, match="injected failure"):
            await app.enqueue_batch(client.generate_batch("ATOMIC", 2, start_seq=1))
        assert await app.enqueue_batch(client.generate_batch("ATOMIC", 1, start_seq=3)) == {
            "inserted": 1, "duplicates": 0,
        }
        with sqlite3.connect(db_path) as db:
            assert db.execute("SELECT seq FROM readings ORDER BY seq").fetchall() == [(3,)]
    finally:
        await app.write_queue.put(app._WRITER_SENTINEL)
        await task


async def test_backoff_generates_only_at_complete_sample_intervals():
    buffer = deque()
    await client.wait_backoff_period(
        0.12, 0.05, 1, 1, "CADENCE", buffer, 100, lambda: False,
        seq_tracker=client.SequenceTracker(),
    )
    assert len(buffer) == 2


async def test_sequence_sync_error_cannot_be_mistaken_for_empty_database():
    reader = asyncio.StreamReader()
    reader.feed_data(json.dumps({"status": "error", "reason": "database unavailable"}).encode() + b"\n")

    class Writer:
        def write(self, data):
            pass

        async def drain(self):
            pass

    with pytest.raises(ConnectionError):
        await client.fetch_latest_seq(reader, Writer(), "RECOVERY")


async def test_offline_startup_resumes_after_persisted_sequence(monkeypatch):
    """A restarted station may collect readings before it can ask the server for its cursor."""
    handlers = {}
    monkeypatch.setattr(client.signal, "signal", lambda sig, fn: handlers.update({sig: fn}))
    real_connect = asyncio.open_connection
    attempts = 0
    received = []

    async def initially_offline(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError("startup outage")
        return await real_connect(*args, **kwargs)

    monkeypatch.setattr(client.asyncio, "open_connection", initially_offline)

    async def peer(reader, writer):
        try:
            while line := await reader.readline():
                message = json.loads(line)
                if isinstance(message, dict):
                    response = {"status": "ok", "latest_seq": 100}
                else:
                    received.extend(message)
                    response = {"status": "ok", "inserted": len(message)}
                    handlers[signal.SIGTERM](None, None)
                writer.write(json.dumps(response).encode() + b"\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    listener = await asyncio.start_server(peer, "127.0.0.1", 0)
    config = {
        "server_host": "127.0.0.1", "server_port": listener.sockets[0].getsockname()[1],
        "station_id": "RESTART", "batch_size_min": 1, "batch_size_max": 1,
        "batch_interval": 0.01, "response_timeout": 1, "connect_timeout": 1,
        "base_backoff": 0.025, "max_backoff": 0.025, "backoff_jitter": 0,
        "max_buffer_records": 100, "drain_timeout": 1,
    }
    try:
        await asyncio.wait_for(client.send_batches(config), 2)
        assert received
        assert received[0]["seq"] == 101
    finally:
        listener.close()
        await listener.wait_closed()


async def test_oversized_frame_returns_protocol_error():
    listener = await asyncio.start_server(app.handle_client, "127.0.0.1", 0, limit=app.MAX_LINE_SIZE + 1)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            writer.write(b"x" * (app.MAX_LINE_SIZE + 2) + b"\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), 1)
            assert json.loads(response)["reason"] == "line_too_long"
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        listener.close()
        await listener.wait_closed()


def test_buffer_capacity_counts_readings_across_variable_batches():
    first = client.generate_batch("BOUNDED", 3, start_seq=0)
    second = client.generate_batch("BOUNDED", 2, start_seq=3)
    newest = client.generate_batch("BOUNDED", 4, start_seq=5)
    buffer = deque([first, second])
    dropped = client.buffer_batch(buffer, newest, max_capacity=5)
    assert sum(map(len, buffer)) <= 5
    assert list(buffer) == [newest]
    assert dropped == first + second


async def test_database_failure_preserves_batch_until_storage_recovers(tmp_path, monkeypatch):
    db_path = tmp_path / "retry.db"
    monkeypatch.setattr(app, "DB_FILE", str(db_path))
    await app.init_database()
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TRIGGER unavailable BEFORE INSERT ON readings
                      BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END""")
    monkeypatch.setattr(app, "write_queue", asyncio.Queue())
    task = asyncio.create_task(app.db_writer_task())
    listener = await asyncio.start_server(app.handle_client, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
    batch = client.generate_batch("STORAGE", 2, start_seq=1)
    buffer = deque([batch])
    try:
        status, _ = await client.flush_buffer(reader, writer, buffer, "STORAGE", 1, 1, lambda: False)
        assert status == "backpressure"
        assert list(buffer) == [batch]
        with sqlite3.connect(db_path) as db:
            db.execute("DROP TRIGGER unavailable")
        status, _ = await client.flush_buffer(reader, writer, buffer, "STORAGE", 1, 1, lambda: False)
        assert status == "ok" and not buffer
        with sqlite3.connect(db_path) as db:
            assert db.execute("SELECT seq FROM readings ORDER BY seq").fetchall() == [(1,), (2,)]
    finally:
        writer.close()
        await writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await app.write_queue.put(app._WRITER_SENTINEL)
        await task


async def test_shutdown_commits_and_acknowledges_already_queued_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_FILE", str(tmp_path / "shutdown.db"))
    monkeypatch.setattr(app, "HOST", "127.0.0.1")
    monkeypatch.setattr(app, "PORT", 0)
    for name in ("server", "shutdown_event", "write_queue", "writer_task"):
        monkeypatch.setattr(app, name, None)
    gate = asyncio.Event()
    real_writer = app.db_writer_task

    async def delayed_writer():
        await gate.wait()
        await real_writer()

    monkeypatch.setattr(app, "db_writer_task", delayed_writer)
    loop = asyncio.get_running_loop()
    # Keep the runner's signal handlers intact; invoke the real shutdown handler below.
    monkeypatch.setattr(loop, "add_signal_handler", lambda *args: None)
    main_task = asyncio.create_task(app.main())
    writer = None
    try:
        async with asyncio.timeout(3):
            while app.server is None:
                await asyncio.sleep(0.001)
            reader, writer = await asyncio.open_connection("127.0.0.1", app.server.sockets[0].getsockname()[1])
            writer.write(json.dumps(client.generate_batch("SHUTDOWN", 2, start_seq=0)).encode() + b"\n")
            await writer.drain()
            while app.write_queue.empty():
                await asyncio.sleep(0.001)
            await app.shutdown_handler(signal.SIGTERM)
            gate.set()
            response = json.loads(await reader.readline())
            assert response == {"status": "ok", "inserted": 2, "duplicates": 0}
            writer.close()
            await writer.wait_closed()
            await main_task
        with sqlite3.connect(app.DB_FILE) as db:
            assert db.execute("SELECT seq FROM readings ORDER BY seq").fetchall() == [(0,), (1,)]
    finally:
        gate.set()
        if writer:
            writer.close()
            await writer.wait_closed()
        if not main_task.done():
            main_task.cancel()
        await asyncio.gather(main_task, return_exceptions=True)
