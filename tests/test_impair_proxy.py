"""Tests for scripts/impair_proxy.py (the link-impairment proxy used by measurement harnesses).

Timing assertions use lower bounds with some slack for coarse Windows timer resolution and
generous upper bounds so the tests stay stable on a loaded machine.
"""

import asyncio
import os
import socket
import time

import impair_proxy
import pytest
from impair_proxy import Impairment, ImpairmentProxy

HOST = "127.0.0.1"
TIMEOUT = 10.0


async def _echo_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


@pytest.fixture
async def upstream():
    """Local echo server; yields its port."""
    server = await asyncio.start_server(_echo_handler, HOST, 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), 2.0)
    except asyncio.TimeoutError:
        pass


@pytest.fixture
async def make_proxy(upstream):
    """Factory for started proxies in front of the echo server; closed at teardown."""
    proxies: list[ImpairmentProxy] = []

    async def factory(impairment=None, *, upstream_port=None, seed=1) -> ImpairmentProxy:
        proxy = ImpairmentProxy(
            HOST,
            upstream if upstream_port is None else upstream_port,
            impairment=impairment,
            seed=seed,
        )
        await proxy.start()
        proxies.append(proxy)
        return proxy

    yield factory
    for proxy in proxies:
        await proxy.close()


@pytest.fixture
async def connect():
    """Factory opening client connections to a proxy; writers are closed at teardown."""
    writers: list[asyncio.StreamWriter] = []

    async def factory(proxy: ImpairmentProxy):
        reader, writer = await asyncio.open_connection(HOST, proxy.port)
        writers.append(writer)
        return reader, writer

    yield factory
    for writer in writers:
        writer.transport.abort()


async def _round_trip(reader, writer, payload: bytes = b"ping\n") -> float:
    start = time.perf_counter()
    writer.write(payload)
    await writer.drain()
    got = await asyncio.wait_for(reader.readexactly(len(payload)), TIMEOUT)
    assert got == payload
    return time.perf_counter() - start


async def _expect_closed(reader, timeout: float = 3.0) -> None:
    """Assert the peer closes or resets the connection promptly."""
    try:
        data = await asyncio.wait_for(reader.read(1), timeout)
    except ConnectionError:
        return
    assert data == b""


# -- Impairment -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"delay_ms": -1},
        {"jitter_ms": -0.1},
        {"rate_kbps": -5},
        {"stall_prob": -0.1},
        {"stall_ms": -1},
        {"stall_prob": 1.01},
    ],
)
def test_impairment_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        Impairment(**kwargs)


def test_impairment_defaults_and_bounds_are_valid():
    assert Impairment() == Impairment(0, 0, 0, 0, 0)
    Impairment(stall_prob=0.0)
    Impairment(stall_prob=1.0, stall_ms=10)


def test_main_is_importable():
    assert callable(impair_proxy.main)


async def test_partition_rejects_unknown_mode(make_proxy):
    proxy = await make_proxy()
    with pytest.raises(ValueError):
        proxy.partition("explode")
    assert not proxy.partitioned


async def test_port_before_start_raises():
    proxy = ImpairmentProxy(HOST, 1)
    with pytest.raises(RuntimeError):
        _ = proxy.port
    await proxy.close()


# -- Forwarding -------------------------------------------------------------------------------


async def test_passthrough_integrity_and_stats(make_proxy, connect):
    proxy = await make_proxy()
    reader, writer = await connect(proxy)
    payload = os.urandom(1024 * 1024)

    async def send() -> None:
        for i in range(0, len(payload), 100_000):
            writer.write(payload[i : i + 100_000])
            await writer.drain()

    sender = asyncio.create_task(send())
    got = await asyncio.wait_for(reader.readexactly(len(payload)), TIMEOUT)
    await sender
    assert got == payload
    stats = proxy.stats
    assert stats["bytes_up"] == len(payload)
    assert stats["bytes_down"] == len(payload)
    assert stats["connections_total"] == 1
    assert stats["connections_active"] == 1


async def test_delay_adds_latency_in_both_directions(make_proxy, connect):
    proxy = await make_proxy(Impairment(delay_ms=100))
    reader, writer = await connect(proxy)
    rtt = await _round_trip(reader, writer)
    assert 0.17 <= rtt < 3.0


async def test_no_impairment_is_fast(make_proxy, connect):
    proxy = await make_proxy()
    reader, writer = await connect(proxy)
    await _round_trip(reader, writer)  # warm up (includes the upstream connect)
    assert await _round_trip(reader, writer) < 0.15


async def test_order_preserved_under_jitter(make_proxy, connect):
    proxy = await make_proxy(Impairment(delay_ms=20, jitter_ms=20), seed=7)
    reader, writer = await connect(proxy)

    async def send() -> None:
        for batch in range(40):
            for n in range(batch * 5, batch * 5 + 5):
                writer.write(f"{n}\n".encode())
            await writer.drain()
            await asyncio.sleep(0.002)

    sender = asyncio.create_task(send())
    lines = [await asyncio.wait_for(reader.readline(), TIMEOUT) for _ in range(200)]
    await sender
    assert lines == [f"{n}\n".encode() for n in range(200)]


async def test_rate_cap_throttles(make_proxy, connect):
    proxy = await make_proxy(Impairment(rate_kbps=256))
    reader, writer = await connect(proxy)
    payload = os.urandom(32 * 1024)  # 262144 bits -> about 1.02 s at 256 kbit/s
    start = time.perf_counter()
    writer.write(payload)
    await writer.drain()
    got = await asyncio.wait_for(reader.readexactly(len(payload)), TIMEOUT)
    elapsed = time.perf_counter() - start
    assert got == payload
    assert elapsed >= 0.8


async def test_stall_delays_chunk(make_proxy, connect):
    proxy = await make_proxy(Impairment(stall_prob=1.0, stall_ms=150))
    reader, writer = await connect(proxy)
    rtt = await _round_trip(reader, writer)
    assert 0.26 <= rtt < 3.0  # stalled once in each direction


async def test_set_impairment_applies_to_live_connection(make_proxy, connect):
    proxy = await make_proxy()
    reader, writer = await connect(proxy)
    await _round_trip(reader, writer)
    new = Impairment(delay_ms=100)
    proxy.set_impairment(new)
    assert proxy.impairment == new
    assert await _round_trip(reader, writer) >= 0.17


# -- Partitions -------------------------------------------------------------------------------


async def test_blackhole_holds_bytes_then_delivers_in_order(make_proxy, connect):
    proxy = await make_proxy()
    reader, writer = await connect(proxy)
    await _round_trip(reader, writer)

    proxy.partition("blackhole")
    assert proxy.partitioned
    payload = b"".join(f"line {n}\n".encode() for n in range(500))
    writer.write(payload[:2000])
    await writer.drain()
    await asyncio.sleep(0.05)
    writer.write(payload[2000:])
    await writer.drain()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.read(1), 0.3)

    proxy.heal()
    assert not proxy.partitioned
    got = await asyncio.wait_for(reader.readexactly(len(payload)), TIMEOUT)
    assert got == payload


async def test_connection_opened_during_blackhole_works_after_heal(make_proxy, connect):
    proxy = await make_proxy()
    proxy.partition("blackhole")
    reader, writer = await connect(proxy)
    writer.write(b"hello\n")
    await writer.drain()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.read(1), 0.3)

    proxy.heal()
    assert await asyncio.wait_for(reader.readline(), TIMEOUT) == b"hello\n"


async def test_reset_aborts_existing_and_new_connections(make_proxy, connect):
    proxy = await make_proxy()
    reader, writer = await connect(proxy)
    await _round_trip(reader, writer)

    proxy.partition("reset")
    await _expect_closed(reader)

    reader2, writer2 = await connect(proxy)
    try:
        writer2.write(b"ping\n")
        await writer2.drain()
    except ConnectionError:
        pass
    await _expect_closed(reader2)

    proxy.heal()
    reader3, writer3 = await connect(proxy)
    await _round_trip(reader3, writer3)


# -- Lifecycle --------------------------------------------------------------------------------


async def test_half_close_propagates_both_ways(make_proxy, connect):
    received = asyncio.Event()
    seen: list[bytes] = []

    async def handler(reader, writer):
        data = await reader.read()  # until EOF
        seen.append(data)
        received.set()
        writer.write(b"got:" + data)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, HOST, 0)
    try:
        proxy = await make_proxy(upstream_port=server.sockets[0].getsockname()[1])
        reader, writer = await connect(proxy)
        writer.write(b"abc")
        await writer.drain()
        writer.write_eof()
        await asyncio.wait_for(received.wait(), TIMEOUT)
        assert seen == [b"abc"]
        assert await asyncio.wait_for(reader.read(), TIMEOUT) == b"got:abc"
    finally:
        server.close()


async def test_upstream_unreachable_closes_client(make_proxy, connect):
    with socket.socket() as s:
        s.bind((HOST, 0))
        dead_port = s.getsockname()[1]
    proxy = await make_proxy(upstream_port=dead_port)
    reader, writer = await connect(proxy)
    try:
        writer.write(b"hi\n")
        await writer.drain()
    except ConnectionError:
        pass
    await _expect_closed(reader, 5.0)


async def test_close_is_idempotent_and_clears_connections(make_proxy, connect):
    proxy = await make_proxy()
    r1, w1 = await connect(proxy)
    r2, w2 = await connect(proxy)
    await _round_trip(r1, w1)
    await _round_trip(r2, w2)
    assert proxy.stats["connections_active"] == 2

    await proxy.close()
    await proxy.close()
    assert proxy.stats["connections_active"] == 0
    assert proxy.stats["connections_total"] == 2
    await _expect_closed(r1)
    await _expect_closed(r2)


async def test_close_while_partitioned_and_waiting(make_proxy, connect):
    proxy = await make_proxy()
    proxy.partition("blackhole")
    await connect(proxy)
    await asyncio.sleep(0.05)
    await proxy.close()
    assert proxy.stats["connections_active"] == 0


async def test_async_context_manager(upstream):
    async with ImpairmentProxy(HOST, upstream) as proxy:
        assert proxy.port > 0
        reader, writer = await asyncio.open_connection(HOST, proxy.port)
        try:
            await _round_trip(reader, writer)
        finally:
            writer.transport.abort()
    assert proxy.stats["connections_active"] == 0
