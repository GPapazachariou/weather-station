"""Exercise real server processes, dropped acknowledgments and outage recovery.

Run in Docker: python scripts/verify_recovery.py --output docs/evidence/recovery.json
Uses a temporary database and ephemeral loopback ports; never touches demo/user data.
"""

import argparse
import asyncio
import json
import os
import platform
import signal
import socket
import sqlite3
import sys
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "station_client"))
import client


async def verify(directory):
    db_path = directory / "recovery.db"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = None
    log = (directory / "server.log").open("w")

    async def start():
        nonlocal process
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "server/app.py"),
            env={**os.environ, "DB_PATH": str(db_path), "SERVER_HOST": "127.0.0.1",
                 "SERVER_PORT": str(port), "PYTHONUNBUFFERED": "1"},
            stdout=log, stderr=log,
        )
        for _ in range(100):
            if process.returncode is not None:
                raise RuntimeError((directory / "server.log").read_text())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(b'{"request":"stations"}\n')
                await writer.drain()
                reply = json.loads(await asyncio.wait_for(reader.readline(), 1))
                writer.close()
                await writer.wait_closed()
                if reply["status"] == "ok":
                    return
            except (OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.05)
        raise TimeoutError("Server did not become ready")

    async def stop(crash=False):
        if crash:
            process.kill()
        else:
            process.send_signal(signal.SIGTERM)
        code = await asyncio.wait_for(process.wait(), 15)
        if not crash:
            assert code == 0, f"Graceful shutdown exited {code}: {(directory / 'server.log').read_text()}"

    async def flush(buffer):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            result, _ = await client.flush_buffer(reader, writer, buffer, "RECOVERY", 2, 2, lambda: False)
            assert result == "ok" and not buffer
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        await start()
        first_batch = client.generate_batch("RECOVERY", 3, start_seq=0)
        buffer = deque([first_batch])
        committed = asyncio.Event()

        async def drop_ack(reader, writer):
            upstream_writer = None
            try:
                upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", port)
                upstream_writer.write(await reader.readline())
                await upstream_writer.drain()
                response = json.loads(await upstream_reader.readline())
                assert response == {"status": "ok", "inserted": 3, "duplicates": 0}
                committed.set()
                # Commit is confirmed, but the downstream client never receives its ACK.
            finally:
                if upstream_writer:
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
                writer.close()
                await writer.wait_closed()

        proxy = await asyncio.start_server(drop_ack, "127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.sockets[0].getsockname()[1])
            try:
                try:
                    await client.flush_buffer(reader, writer, buffer, "RECOVERY", 2, 2, lambda: False)
                except ConnectionError:
                    pass
                else:
                    raise AssertionError("Proxy must drop the acknowledgment")
                await asyncio.wait_for(committed.wait(), 2)
                assert list(buffer) == [first_batch]
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            proxy.close()
            await proxy.wait_closed()

        await flush(buffer)
        await stop()
        tracker = client.SequenceTracker(initial_seq=3)
        await client.wait_backoff_period(0.22, 0.05, 2, 2, "RECOVERY", buffer, 100, lambda: False, tracker)
        offline_count = sum(map(len, buffer))
        assert offline_count > 0
        expected = 3 + offline_count
        await start()
        await flush(buffer)
        await stop(crash=True)
        await start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            cursor = await client.fetch_latest_seq(reader, writer, "RECOVERY")
            assert cursor == expected - 1
        finally:
            writer.close()
            await writer.wait_closed()
        await flush(deque([client.generate_batch("RECOVERY", 1, start_seq=cursor + 1)]))
        expected += 1
        await stop()
        with sqlite3.connect(db_path) as db:
            sequences = [row[0] for row in db.execute("SELECT seq FROM readings ORDER BY seq")]
        assert sequences == list(range(expected)), sequences
        return {"verified_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
                "python": platform.python_version(), "platform": platform.platform(),
                "sqlite": sqlite3.sqlite_version,
                "generated_unique": expected, "persisted_unique": len(sequences),
                "replayed_after_lost_ack": 3, "buffered_during_outage": offline_count,
                "missing": 0, "duplicate_rows": 0,
                "scenarios": ["commit then lose ACK", "retry same batch", "graceful SIGTERM",
                              "buffer during outage", "restart with same database",
                              "SIGKILL after acknowledged commit", "resume sequence after restart"],
                "scope": "Process/network failures; not client-process durability or host power loss"}
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="weather-recovery-") as directory:
        report = asyncio.run(verify(Path(directory)))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
