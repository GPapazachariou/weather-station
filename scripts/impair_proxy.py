"""Impairment proxy: an asyncio TCP proxy that adds latency, jitter, a rate cap, stalls and partitions.

Sits between a client and an upstream server. Byte stream integrity and ordering are preserved
in each direction under every impairment; the link behaves like a pipe with latency.

Library use::

    async with ImpairmentProxy("127.0.0.1", 12345, impairment=Impairment(delay_ms=50)) as proxy:
        ...  # point clients at proxy.port
        proxy.partition("blackhole")
        proxy.heal()

CLI use::

    python scripts/impair_proxy.py --listen-port 12346 --upstream-host server \\
        --upstream-port 12345 --delay-ms 80 --jitter-ms 20
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import random
import signal
import sys
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

_READ_SIZE = 65536
_QUEUE_CHUNKS = 256
_RATE_PIECE = 1024
_PARTITION_MODES = ("blackhole", "reset")


@dataclass(frozen=True)
class Impairment:
    """Link impairment settings. All values are non-negative; 0 disables the effect."""

    delay_ms: float = 0.0  # one-way latency added in each direction
    jitter_ms: float = 0.0  # uniform +/- jitter added to the delay per chunk
    rate_kbps: float = 0.0  # per-direction, per-connection bandwidth cap; 0 = unlimited
    stall_prob: float = 0.0  # per-chunk probability of an extra stall (retransmission timeout)
    stall_ms: float = 0.0  # length of that stall

    def __post_init__(self) -> None:
        for name in ("delay_ms", "jitter_ms", "rate_kbps", "stall_prob", "stall_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)!r}")
        if self.stall_prob > 1:
            raise ValueError(f"stall_prob must be <= 1, got {self.stall_prob!r}")


class _Conn:
    """One proxied client connection (client side plus, once connected, upstream side)."""

    def __init__(self, cwriter: asyncio.StreamWriter, task: asyncio.Task[Any]) -> None:
        self.cwriter = cwriter
        self.uwriter: asyncio.StreamWriter | None = None
        self.task = task

    def abort(self) -> None:
        """Abort both transports and cancel the handler task."""
        for writer in (self.cwriter, self.uwriter):
            if writer is not None:
                writer.transport.abort()
        self.task.cancel()


class ImpairmentProxy:
    """TCP proxy that impairs the link between clients and one upstream server."""

    def __init__(
        self,
        upstream_host: str,
        upstream_port: int,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 0,
        impairment: Impairment | None = None,
        seed: int | None = None,
    ) -> None:
        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._impairment = impairment or Impairment()
        self._rng = random.Random(seed)
        self._server: asyncio.Server | None = None
        self._port: int | None = None
        self._closed = False
        self._partitioned = False
        self._mode = "blackhole"
        self._healed = asyncio.Event()
        self._healed.set()
        self._conns: set[_Conn] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._connections_total = 0
        self._bytes = {"up": 0, "down": 0}

    # -- public API ---------------------------------------------------------------------------

    async def start(self) -> None:
        """Bind and start listening (``listen_port=0`` picks a free port)."""
        self._server = await asyncio.start_server(
            self._handle, self._listen_host, self._listen_port
        )
        self._port = self._server.sockets[0].getsockname()[1]

    @property
    def port(self) -> int:
        """The actual bound port (valid after ``start()``)."""
        if self._port is None:
            raise RuntimeError("proxy not started")
        return self._port

    async def close(self) -> None:
        """Stop listening, abort every connection and await all internal tasks. Idempotent."""
        self._closed = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        await asyncio.sleep(0)  # let handlers of just-accepted connections start (and bail out)
        for conn in list(self._conns):
            conn.abort()
        while pending := [t for t in self._tasks if not t.done()]:
            await asyncio.gather(*pending, return_exceptions=True)
        if server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), 2.0)

    def set_impairment(self, impairment: Impairment) -> None:
        """Apply new settings to data forwarded from now on (including live connections)."""
        self._impairment = impairment

    @property
    def impairment(self) -> Impairment:
        """The current impairment settings."""
        return self._impairment

    def partition(self, mode: str = "blackhole") -> None:
        """Start a partition: ``"blackhole"`` holds bytes, ``"reset"`` aborts connections."""
        if mode not in _PARTITION_MODES:
            raise ValueError(f"mode must be one of {_PARTITION_MODES}, got {mode!r}")
        self._partitioned = True
        self._mode = mode
        self._healed.clear()
        if mode == "reset":
            for conn in list(self._conns):
                conn.abort()

    def heal(self) -> None:
        """End the partition; held bytes and waiting connections proceed in order."""
        self._partitioned = False
        self._healed.set()

    @property
    def partitioned(self) -> bool:
        """True while a partition is active."""
        return self._partitioned

    @property
    def stats(self) -> dict[str, int]:
        """Counters; ``up`` is client->upstream. Bytes are counted when written to the peer."""
        return {
            "connections_total": self._connections_total,
            "connections_active": len(self._conns),
            "bytes_up": self._bytes["up"],
            "bytes_down": self._bytes["down"],
        }

    async def __aenter__(self) -> ImpairmentProxy:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- internals ----------------------------------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._track(task)
        return task

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()  # mark retrieved; errors are handled by the connection owner

    async def _handle(self, creader: asyncio.StreamReader, cwriter: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._track(task)
        conn = _Conn(cwriter, task)
        self._conns.add(conn)
        self._connections_total += 1
        try:
            await self._serve(conn, creader, cwriter)
        except Exception:
            cwriter.transport.abort()
        finally:
            self._conns.discard(conn)

    async def _serve(
        self, conn: _Conn, creader: asyncio.StreamReader, cwriter: asyncio.StreamWriter
    ) -> None:
        if self._closed:
            cwriter.transport.abort()
            return
        while self._partitioned:
            if self._mode == "reset":
                cwriter.transport.abort()
                return
            await self._healed.wait()
        try:
            ureader, uwriter = await asyncio.open_connection(
                self._upstream_host, self._upstream_port
            )
        except OSError:
            cwriter.transport.abort()
            return
        conn.uwriter = uwriter
        up_q: asyncio.Queue[tuple[float, bytes] | None] = asyncio.Queue(_QUEUE_CHUNKS)
        down_q: asyncio.Queue[tuple[float, bytes] | None] = asyncio.Queue(_QUEUE_CHUNKS)
        children = [
            self._spawn(self._read_loop(creader, up_q)),
            self._spawn(self._write_loop(up_q, uwriter, "up")),
            self._spawn(self._read_loop(ureader, down_q)),
            self._spawn(self._write_loop(down_q, cwriter, "down")),
        ]
        clean = False
        try:
            await asyncio.wait(children, return_when=asyncio.FIRST_EXCEPTION)
            clean = all(not t.cancelled() and t.done() and t.exception() is None for t in children)
        finally:
            for t in children:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            for writer in (cwriter, uwriter):
                if clean:
                    writer.close()
                else:
                    writer.transport.abort()

    async def _read_loop(
        self, reader: asyncio.StreamReader, queue: asyncio.Queue[tuple[float, bytes] | None]
    ) -> None:
        """Read chunks, stamp each with a release time and queue it (``None`` marks EOF)."""
        loop = asyncio.get_running_loop()
        last_release = 0.0
        while True:
            data = await reader.read(_READ_SIZE)
            if not data:
                await queue.put(None)
                return
            imp = self._impairment
            delay_ms = imp.delay_ms
            if imp.jitter_ms:
                delay_ms += self._rng.uniform(-imp.jitter_ms, imp.jitter_ms)
            release = max(last_release, loop.time() + max(delay_ms, 0.0) / 1000.0)
            if imp.stall_prob and self._rng.random() < imp.stall_prob:
                release += imp.stall_ms / 1000.0
            last_release = release
            await queue.put((release, data))

    async def _write_loop(
        self,
        queue: asyncio.Queue[tuple[float, bytes] | None],
        writer: asyncio.StreamWriter,
        key: str,
    ) -> None:
        """Deliver queued chunks at their release time, honouring partitions and the rate cap."""
        loop = asyncio.get_running_loop()
        not_before = 0.0
        while True:
            item = await queue.get()
            if item is None:
                break
            release, data = item
            if (wait := release - loop.time()) > 0:
                await asyncio.sleep(wait)
            await self._healed.wait()
            if self._impairment.rate_kbps <= 0:
                self._emit(writer, data, key)
                await writer.drain()
                continue
            for i in range(0, len(data), _RATE_PIECE):
                piece = data[i : i + _RATE_PIECE]
                if (wait := not_before - loop.time()) > 0:
                    await asyncio.sleep(wait)
                await self._healed.wait()
                self._emit(writer, piece, key)
                await writer.drain()
                rate = self._impairment.rate_kbps
                if rate > 0:
                    not_before = max(loop.time(), not_before) + len(piece) * 8 / (rate * 1000)
        await self._healed.wait()
        if writer.can_write_eof():
            writer.write_eof()

    def _emit(self, writer: asyncio.StreamWriter, data: bytes, key: str) -> None:
        writer.write(data)
        self._bytes[key] += len(data)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TCP proxy that impairs the link to an upstream.")
    p.add_argument("--listen-host", default="0.0.0.0")
    p.add_argument("--listen-port", type=int, required=True)
    p.add_argument("--upstream-host", required=True)
    p.add_argument("--upstream-port", type=int, required=True)
    p.add_argument("--delay-ms", type=float, default=0.0, help="one-way delay per direction")
    p.add_argument("--jitter-ms", type=float, default=0.0, help="uniform +/- jitter per chunk")
    p.add_argument("--rate-kbps", type=float, default=0.0, help="per-direction cap; 0 = none")
    p.add_argument("--stall-prob", type=float, default=0.0, help="per-chunk stall probability")
    p.add_argument("--stall-ms", type=float, default=0.0, help="stall length")
    p.add_argument("--seed", type=int, default=None)
    return p


async def _run(args: argparse.Namespace, impairment: Impairment) -> None:
    proxy = ImpairmentProxy(
        args.upstream_host,
        args.upstream_port,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        impairment=impairment,
        seed=args.seed,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, stop.set)
    async with proxy:
        print(
            f"impair_proxy listening on {args.listen_host}:{proxy.port} -> "
            f"{args.upstream_host}:{args.upstream_port} "
            f"delay_ms={impairment.delay_ms} jitter_ms={impairment.jitter_ms} "
            f"rate_kbps={impairment.rate_kbps} stall_prob={impairment.stall_prob} "
            f"stall_ms={impairment.stall_ms} seed={args.seed}",
            flush=True,
        )
        await stop.wait()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: run the proxy until interrupted."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        impairment = Impairment(
            delay_ms=args.delay_ms,
            jitter_ms=args.jitter_ms,
            rate_kbps=args.rate_kbps,
            stall_prob=args.stall_prob,
            stall_ms=args.stall_ms,
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        asyncio.run(_run(args, impairment))
    except KeyboardInterrupt:
        pass
    sys.stdout.flush()


if __name__ == "__main__":
    main()
