"""Tests for the dependency-free Prometheus metrics module (server/telemetry.py)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
import telemetry
from telemetry import DEFAULT_BUCKETS, Counter, Gauge, Histogram, Registry

# --------------------------------------------------------------------------- counter


def test_counter_unlabelled_inc_and_value():
    c = Counter("hits_total", "Hits.")
    assert c.value() == 0
    c.inc()
    c.inc(2.5)
    assert c.value() == 3.5


def test_counter_rejects_negative_increment():
    c = Counter("hits_total", "Hits.")
    with pytest.raises(ValueError):
        c.inc(-1)
    assert c.value() == 0


def test_counter_labelled_values_are_independent():
    c = Counter("req_total", "Requests.", ("method", "code"))
    c.inc(method="GET", code=200)  # label values are str()-converted
    c.inc(3, method="GET", code="200")
    c.inc(method="POST", code="500")
    assert c.value(method="GET", code="200") == 4
    assert c.value(method="POST", code="500") == 1
    assert c.value(method="PUT", code="204") == 0


@pytest.mark.parametrize(
    "labels",
    [{}, {"a": "1"}, {"a": "1", "b": "2", "c": "3"}, {"a": "1", "c": "3"}],
)
def test_counter_label_mismatch_raises(labels):
    c = Counter("x_total", "X.", ("a", "b"))
    with pytest.raises(ValueError):
        c.inc(**labels)
    with pytest.raises(ValueError):
        c.value(**labels)


def test_unlabelled_counter_rejects_labels():
    c = Counter("x_total", "X.")
    with pytest.raises(ValueError):
        c.inc(a="1")


# --------------------------------------------------------------------------- gauge


def test_gauge_set_inc_dec():
    g = Gauge("temp", "Temperature.")
    assert g.value() == 0
    g.set(10)
    g.inc()
    g.inc(2)
    g.dec()
    g.dec(0.5)
    assert g.value() == 11.5


def test_gauge_labelled():
    g = Gauge("conn", "Connections.", ("peer",))
    g.set(5, peer="a")
    g.inc(peer="b")
    g.dec(3, peer="a")
    assert g.value(peer="a") == 2
    assert g.value(peer="b") == 1
    assert g.value(peer="c") == 0
    with pytest.raises(ValueError):
        g.set(1)
    with pytest.raises(ValueError):
        g.value()


def test_gauge_set_function_overrides_set_value():
    g = Gauge("queue_depth", "Depth.")
    g.set(99)
    depth = [3]
    g.set_function(lambda: depth[0])
    assert g.value() == 3
    depth[0] = 7
    assert g.value() == 7
    reg = Registry()
    g2 = reg.gauge("fn_gauge", "Fn.")
    g2.set_function(lambda: 4)
    assert "fn_gauge 4\n" in reg.render()


def test_gauge_set_function_requires_no_labels():
    g = Gauge("g", "G.", ("a",))
    with pytest.raises(ValueError):
        g.set_function(lambda: 1)


# --------------------------------------------------------------------------- histogram


def test_histogram_observe_count_sum():
    h = Histogram("lat_seconds", "Latency.")
    assert (h.count, h.sum) == (0, 0.0)
    h.observe(0.5)
    h.observe(1.5)
    assert h.count == 2
    assert h.sum == 2.0


def test_histogram_default_buckets():
    assert DEFAULT_BUCKETS == (
        0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
    )  # fmt: skip
    reg = Registry()
    reg.histogram("h", "H.")
    out = reg.render()
    assert 'h_bucket{le="0.001"} 0\n' in out
    assert 'h_bucket{le="10.0"} 0\n' in out
    assert out.count("h_bucket{") == len(DEFAULT_BUCKETS) + 1


def test_histogram_bucket_boundaries_are_inclusive():
    reg = Registry()
    h = reg.histogram("h", "H.", (0.5, 1.0))
    h.observe(0.5)  # equal to the first bound -> first bucket
    h.observe(0.5000001)  # just above -> second bucket
    h.observe(1.0)  # equal to the second bound -> second bucket
    h.observe(1.0000001)  # beyond the last bound -> only +Inf
    out = reg.render()
    assert 'h_bucket{le="0.5"} 1\n' in out
    assert 'h_bucket{le="1.0"} 3\n' in out
    assert 'h_bucket{le="+Inf"} 4\n' in out
    assert "h_count 4\n" in out


@pytest.mark.parametrize(
    "buckets",
    [(), (1.0, 1.0), (2.0, 1.0), (1.0, float("inf")), (float("nan"),)],
)
def test_histogram_invalid_buckets(buckets):
    with pytest.raises(ValueError):
        Histogram("h", "H.", buckets)


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("name", ["", "1abc", "a-b", "a b", "a.b", "é"])
def test_invalid_metric_names(name):
    with pytest.raises(ValueError):
        Counter(name, "x")
    with pytest.raises(ValueError):
        Gauge(name, "x")
    with pytest.raises(ValueError):
        Histogram(name, "x")


@pytest.mark.parametrize("name", ["a", "_a", "a:b", "ns:metric_1", "A9"])
def test_valid_metric_names(name):
    assert Counter(name, "x").name == name


@pytest.mark.parametrize("label", ["", "1a", "a-b", "a:b", "a b"])
def test_invalid_label_names(label):
    with pytest.raises(ValueError):
        Counter("c_total", "x", (label,))
    with pytest.raises(ValueError):
        Gauge("g", "x", (label,))


def test_duplicate_label_names_rejected():
    with pytest.raises(ValueError):
        Counter("c_total", "x", ("a", "a"))


def test_duplicate_registration_raises():
    reg = Registry()
    reg.counter("dup", "First.")
    with pytest.raises(ValueError):
        reg.counter("dup", "Second.")
    with pytest.raises(ValueError):
        reg.gauge("dup", "Third.")
    with pytest.raises(ValueError):
        reg.histogram("dup", "Fourth.")


# --------------------------------------------------------------------------- rendering


def test_render_exact_text():
    reg = Registry()
    c = reg.counter("req_total", "Total requests.", ("method",))
    c.inc(method="GET")
    c.inc(2, method="POST")
    reg.counter("empty_total", "Never touched.", ("a",))
    g = reg.gauge("up", "Is up.")
    g.set(1)
    reg.gauge("zero", "Starts at zero.")
    h = reg.histogram("lat_seconds", "Latency.", (0.5, 1.0))
    for v in (0.25, 0.5, 1.0, 2.0):
        h.observe(v)

    assert reg.render() == (
        "# HELP req_total Total requests.\n"
        "# TYPE req_total counter\n"
        'req_total{method="GET"} 1\n'
        'req_total{method="POST"} 2\n'
        "# HELP empty_total Never touched.\n"
        "# TYPE empty_total counter\n"
        "# HELP up Is up.\n"
        "# TYPE up gauge\n"
        "up 1\n"
        "# HELP zero Starts at zero.\n"
        "# TYPE zero gauge\n"
        "zero 0\n"
        "# HELP lat_seconds Latency.\n"
        "# TYPE lat_seconds histogram\n"
        'lat_seconds_bucket{le="0.5"} 2\n'
        'lat_seconds_bucket{le="1.0"} 3\n'
        'lat_seconds_bucket{le="+Inf"} 4\n'
        "lat_seconds_sum 3.75\n"
        "lat_seconds_count 4\n"
    )


def test_render_empty_registry_and_unlabelled_zero_counter():
    reg = Registry()
    assert reg.render() == ""
    reg.counter("c_total", "C.")
    assert reg.render() == "# HELP c_total C.\n# TYPE c_total counter\nc_total 0\n"


def test_render_labelled_gauge_without_observations_has_only_header():
    reg = Registry()
    reg.gauge("g", "G.", ("a",))
    assert reg.render() == "# HELP g G.\n# TYPE g gauge\n"


def test_render_label_order_follows_labelnames_and_first_seen():
    reg = Registry()
    c = reg.counter("c_total", "C.", ("b", "a"))
    c.inc(a="1", b="2")
    c.inc(b="0", a="9")
    c.inc(a="1", b="2")
    lines = reg.render().splitlines()
    assert lines[2:] == ['c_total{b="2",a="1"} 2', 'c_total{b="0",a="9"} 1']


def test_render_escaping():
    reg = Registry()
    c = reg.counter("c_total", "back\\slash and\nnewline", ("l",))
    c.inc(l='a\\b"c\nd')
    assert reg.render() == (
        "# HELP c_total back\\\\slash and\\nnewline\n"
        "# TYPE c_total counter\n"
        'c_total{l="a\\\\b\\"c\\nd"} 1\n'
    )


def test_render_registration_order():
    reg = Registry()
    reg.gauge("zzz", "Z.")
    reg.counter("aaa_total", "A.")
    types = [ln for ln in reg.render().splitlines() if ln.startswith("# TYPE")]
    assert types == ["# TYPE zzz gauge", "# TYPE aaa_total counter"]


def test_number_formatting():
    reg = Registry()
    g = reg.gauge("g", "G.", ("k",))
    g.set(3.0, k="int")
    g.set(0.1, k="frac")
    g.set(-2.0, k="neg")
    g.set(1e20, k="big")
    g.set(float("inf"), k="pinf")
    g.set(float("-inf"), k="ninf")
    g.set(float("nan"), k="nan")
    out = reg.render()
    assert 'g{k="int"} 3\n' in out
    assert 'g{k="frac"} 0.1\n' in out
    assert 'g{k="neg"} -2\n' in out
    assert 'g{k="big"} 1e+20\n' in out
    assert 'g{k="pinf"} +Inf\n' in out
    assert 'g{k="ninf"} -Inf\n' in out
    assert 'g{k="nan"} NaN\n' in out


# --------------------------------------------------------------------------- reset


def test_reset_zeroes_values_but_keeps_registrations():
    reg = Registry()
    c = reg.counter("c_total", "C.", ("a",))
    u = reg.counter("u_total", "U.")
    g = reg.gauge("g", "G.")
    gl = reg.gauge("gl", "GL.", ("a",))
    fn = reg.gauge("fn", "Fn.")
    fn.set_function(lambda: 5)
    h = reg.histogram("h", "H.", (1.0,))
    c.inc(a="x")
    u.inc(4)
    g.set(9)
    gl.set(2, a="x")
    h.observe(0.5)

    reg.reset()

    assert c.value(a="x") == 0
    assert u.value() == 0
    assert g.value() == 0
    assert gl.value(a="x") == 0
    assert fn.value() == 5  # callback survives
    assert (h.count, h.sum) == (0, 0.0)
    assert reg.render() == (
        "# HELP c_total C.\n# TYPE c_total counter\n"
        "# HELP u_total U.\n# TYPE u_total counter\nu_total 0\n"
        "# HELP g G.\n# TYPE g gauge\ng 0\n"
        "# HELP gl GL.\n# TYPE gl gauge\n"
        "# HELP fn Fn.\n# TYPE fn gauge\nfn 5\n"
        "# HELP h H.\n# TYPE h histogram\n"
        'h_bucket{le="1.0"} 0\nh_bucket{le="+Inf"} 0\nh_sum 0\nh_count 0\n'
    )
    # still usable after reset
    c.inc(a="x")
    assert c.value(a="x") == 1


# --------------------------------------------------------------------------- HTTP server


@contextlib.asynccontextmanager
async def running(registry: Registry):
    """Start the metrics server on an ephemeral port and always shut it down."""
    server = await telemetry.start_metrics_server(registry, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def http(port: int, raw: bytes, *, half_close: bool = False) -> bytes:
    """Send raw bytes and return everything the server sends until it closes."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(raw)
        await writer.drain()
        if half_close:
            writer.write_eof()
        return await asyncio.wait_for(reader.read(), 5)
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


def parse(response: bytes) -> tuple[str, dict[str, str], bytes]:
    head, _, body = response.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {k.lower(): v for k, v in (ln.split(": ", 1) for ln in lines[1:])}
    return lines[0], headers, body


def sample_registry() -> Registry:
    reg = Registry()
    reg.counter("hits_total", "Hits.", ("path",)).inc(path="/x")
    reg.gauge("up", "Up.").set(1)
    reg.histogram("lat", "Lat.", (0.1,)).observe(0.05)
    return reg


async def test_server_metrics_endpoint():
    reg = sample_registry()
    async with running(reg) as port:
        status, headers, body = parse(await http(port, b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n"))
    assert status == "HTTP/1.1 200 OK"
    assert headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert headers["connection"] == "close"
    assert int(headers["content-length"]) == len(body)
    assert body.decode() == reg.render()


async def test_server_metrics_reflects_live_values_and_ignores_query_string():
    reg = Registry()
    c = reg.counter("n_total", "N.")
    async with running(reg) as port:
        _, _, body1 = parse(await http(port, b"GET /metrics?x=1 HTTP/1.0\r\n\r\n"))
        c.inc(5)
        _, _, body2 = parse(await http(port, b"GET /metrics?debug HTTP/1.1\r\n\r\n"))
    assert b"n_total 0\n" in body1
    assert b"n_total 5\n" in body2


async def test_server_non_ascii_metric_output_content_length_is_in_bytes():
    reg = Registry()
    reg.counter("c_total", "C.", ("l",)).inc(l="é")
    async with running(reg) as port:
        _, headers, body = parse(await http(port, b"GET /metrics HTTP/1.1\r\n\r\n"))
    assert int(headers["content-length"]) == len(body)
    assert body.decode("utf-8") == reg.render()


async def test_server_healthz():
    async with running(Registry()) as port:
        status, headers, body = parse(await http(port, b"GET /healthz HTTP/1.1\r\n\r\n"))
    assert status == "HTTP/1.1 200 OK"
    assert body == b"ok\n"
    assert int(headers["content-length"]) == 3


async def test_server_unknown_path_is_404():
    async with running(Registry()) as port:
        status, headers, body = parse(await http(port, b"GET /nope HTTP/1.1\r\n\r\n"))
    assert status == "HTTP/1.1 404 Not Found"
    assert int(headers["content-length"]) == len(body)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD"])
async def test_server_non_get_is_405(method):
    async with running(sample_registry()) as port:
        raw = f"{method} /metrics HTTP/1.1\r\n\r\n".encode()
        status, headers, body = parse(await http(port, raw))
    assert status == "HTTP/1.1 405 Method Not Allowed"
    assert headers["allow"] == "GET"
    assert int(headers["content-length"]) == len(body)


@pytest.mark.parametrize(
    "raw",
    [b"garbage\r\n\r\n", b"GET /metrics\r\n\r\n", b"GET /metrics FTP/1.0\r\n\r\n", b"\r\n\r\n"],
)
async def test_server_garbage_request_is_400(raw):
    async with running(Registry()) as port:
        status, headers, body = parse(await http(port, raw))
    assert status == "HTTP/1.1 400 Bad Request"
    assert int(headers["content-length"]) == len(body)


async def test_server_truncated_request_is_400():
    async with running(Registry()) as port:
        resp = await http(port, b"GET /metrics HTTP/1.1\r\n", half_close=True)
    assert parse(resp)[0] == "HTTP/1.1 400 Bad Request"


async def test_server_oversized_request_is_400():
    async with running(Registry()) as port:
        resp = await http(port, b"GET /" + b"a" * 9000)
    assert parse(resp)[0] == "HTTP/1.1 400 Bad Request"


async def test_server_slow_request_times_out_with_400(monkeypatch):
    monkeypatch.setattr(telemetry, "REQUEST_TIMEOUT", 0.05)
    async with running(Registry()) as port:
        resp = await http(port, b"GET /metrics HTTP/1.1\r\n")  # never finishes the head
    assert parse(resp)[0] == "HTTP/1.1 400 Bad Request"


async def test_server_survives_handler_errors_and_closes_connection():
    class Boom(Registry):
        def render(self) -> str:
            raise RuntimeError("boom")

    reg = Boom()
    async with running(reg) as port:
        assert await http(port, b"GET /metrics HTTP/1.1\r\n\r\n") == b""
        # still serving afterwards
        status, _, body = parse(await http(port, b"GET /healthz HTTP/1.1\r\n\r\n"))
    assert status == "HTTP/1.1 200 OK"
    assert body == b"ok\n"


async def test_server_client_disconnect_does_not_break_server():
    async with running(Registry()) as port:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        status, _, _ = parse(await http(port, b"GET /healthz HTTP/1.1\r\n\r\n"))
    assert status == "HTTP/1.1 200 OK"
