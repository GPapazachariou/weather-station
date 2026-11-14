"""Dependency-free Prometheus metrics (text exposition format 0.0.4) and a tiny HTTP listener.

Provides ``Counter``, ``Gauge`` and ``Histogram`` metrics, a ``Registry`` that renders them, and
``start_metrics_server`` which serves ``/metrics`` and ``/healthz`` over asyncio.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import re
from collections.abc import Callable
from typing import TypeVar

DEFAULT_BUCKETS: tuple[float, ...] = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
REQUEST_TIMEOUT = 5.0  # seconds allowed to receive the request head
MAX_REQUEST_HEAD = 8192  # bytes

_METRIC_NAME_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

LabelKey = tuple[str, ...]


def _format_value(value: float) -> str:
    """Format a sample value: whole numbers without a decimal point, others via repr."""
    value = float(value)
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "+Inf" if value > 0 else "-Inf"
    if value.is_integer() and abs(value) < 1e16:
        return str(int(value))
    return repr(value)


def _escape_help(text: str) -> str:
    """Escape a HELP string (backslash and newline)."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _escape_label_value(text: str) -> str:
    """Escape a label value (backslash, double quote and newline)."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _check_name(name: str, labelnames: tuple[str, ...]) -> None:
    """Validate a metric name and its label names."""
    if not _METRIC_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid metric name: {name!r}")
    for label in labelnames:
        if not _LABEL_NAME_RE.fullmatch(label):
            raise ValueError(f"invalid label name: {label!r}")
    if len(set(labelnames)) != len(labelnames):
        raise ValueError("duplicate label names")


class _Metric:
    """Shared name/help/label handling for metric types."""

    kind = ""

    def __init__(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()) -> None:
        labelnames = tuple(labelnames)
        _check_name(name, labelnames)
        self.name = name
        self.help_text = help_text
        self.labelnames = labelnames

    def _key(self, labels: dict[str, str]) -> LabelKey:
        """Turn keyword labels into a value tuple ordered by ``labelnames``."""
        if set(labels) != set(self.labelnames):
            raise ValueError(
                f"{self.name}: expected labels {list(self.labelnames)}, got {sorted(labels)}"
            )
        return tuple(str(labels[n]) for n in self.labelnames)

    def _label_text(self, key: LabelKey) -> str:
        """Render ``{a="x",b="y"}`` for a label key (empty string if unlabelled)."""
        if not self.labelnames:
            return ""
        pairs = ",".join(
            f'{n}="{_escape_label_value(v)}"' for n, v in zip(self.labelnames, key, strict=True)
        )
        return "{" + pairs + "}"

    def _header(self) -> list[str]:
        return [
            f"# HELP {self.name} {_escape_help(self.help_text)}",
            f"# TYPE {self.name} {self.kind}",
        ]

    def _samples(self) -> list[str]:
        raise NotImplementedError  # pragma: no cover

    def render_lines(self) -> list[str]:
        """Return HELP, TYPE and sample lines for this metric."""
        return self._header() + self._samples()

    def reset(self) -> None:
        raise NotImplementedError  # pragma: no cover


class Counter(_Metric):
    """Monotonically increasing value, optionally labelled."""

    kind = "counter"

    def __init__(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[LabelKey, float] = {}

    def inc(self, amount: float = 1, **labels: str) -> None:
        """Increase the counter; ``amount`` must be >= 0."""
        if amount < 0:
            raise ValueError("counter increment must be non-negative")
        key = self._key(labels)
        self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        """Current value for a label set (0 if never touched)."""
        return self._values.get(self._key(labels), 0.0)

    def _samples(self) -> list[str]:
        values = self._values
        if not self.labelnames and not values:
            values = {(): 0.0}
        return [f"{self.name}{self._label_text(k)} {_format_value(v)}" for k, v in values.items()]

    def reset(self) -> None:
        """Forget all recorded values."""
        self._values.clear()


class Gauge(_Metric):
    """Value that can go up and down, optionally labelled."""

    kind = "gauge"

    def __init__(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[LabelKey, float] = {}
        self._fn: Callable[[], float] | None = None

    def set(self, value: float, **labels: str) -> None:
        """Set the gauge to ``value``."""
        self._values[self._key(labels)] = float(value)

    def inc(self, amount: float = 1, **labels: str) -> None:
        """Increase the gauge by ``amount``."""
        key = self._key(labels)
        self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1, **labels: str) -> None:
        """Decrease the gauge by ``amount``."""
        self.inc(-amount, **labels)

    def set_function(self, fn: Callable[[], float]) -> None:
        """Compute the value from ``fn`` at render/value time (unlabelled gauges only)."""
        if self.labelnames:
            raise ValueError("set_function is only valid for gauges without labels")
        self._fn = fn

    def value(self, **labels: str) -> float:
        """Current value for a label set (0 if never touched)."""
        key = self._key(labels)
        if self._fn is not None:
            return float(self._fn())
        return self._values.get(key, 0.0)

    def _samples(self) -> list[str]:
        if self._fn is not None:
            return [f"{self.name} {_format_value(self._fn())}"]
        values = self._values
        if not self.labelnames and not values:
            values = {(): 0.0}
        return [f"{self.name}{self._label_text(k)} {_format_value(v)}" for k, v in values.items()]

    def reset(self) -> None:
        """Forget all recorded values (a set_function callback is kept)."""
        self._values.clear()


class Histogram(_Metric):
    """Unlabelled histogram with fixed cumulative buckets."""

    kind = "histogram"

    def __init__(
        self, name: str, help_text: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> None:
        super().__init__(name, help_text, ())
        bounds = tuple(float(b) for b in buckets)
        if not bounds:
            raise ValueError("histogram needs at least one bucket")
        if any(b >= a for b, a in zip(bounds, bounds[1:], strict=False)) or any(
            b != b or b == float("inf") for b in bounds
        ):
            raise ValueError("histogram buckets must be finite and strictly increasing")
        self._bounds = bounds
        self._counts = [0] * len(bounds)  # per-bucket (non-cumulative); +Inf is derived
        self._count = 0
        self._sum = 0.0

    def observe(self, value: float) -> None:
        """Record one observation."""
        idx = bisect.bisect_left(self._bounds, value)  # first bound with value <= bound
        if idx < len(self._counts):
            self._counts[idx] += 1
        self._count += 1
        self._sum += value

    @property
    def count(self) -> int:
        """Number of observations."""
        return self._count

    @property
    def sum(self) -> float:
        """Sum of observed values."""
        return self._sum

    def _samples(self) -> list[str]:
        lines = []
        cumulative = 0
        for bound, n in zip(self._bounds, self._counts, strict=True):
            cumulative += n
            lines.append(f'{self.name}_bucket{{le="{bound!r}"}} {cumulative}')
        lines.append(f'{self.name}_bucket{{le="+Inf"}} {self._count}')
        lines.append(f"{self.name}_sum {_format_value(self._sum)}")
        lines.append(f"{self.name}_count {self._count}")
        return lines

    def reset(self) -> None:
        """Zero all buckets, the sum and the count."""
        self._counts = [0] * len(self._bounds)
        self._count = 0
        self._sum = 0.0


M = TypeVar("M", bound=_Metric)


class Registry:
    """Holds metrics in registration order and renders them."""

    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}

    def _register(self, metric: M) -> M:
        if metric.name in self._metrics:
            raise ValueError(f"metric already registered: {metric.name}")
        self._metrics[metric.name] = metric
        return metric

    def counter(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()) -> Counter:
        """Create and register a counter."""
        return self._register(Counter(name, help_text, labelnames))

    def gauge(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()) -> Gauge:
        """Create and register a gauge."""
        return self._register(Gauge(name, help_text, labelnames))

    def histogram(
        self, name: str, help_text: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> Histogram:
        """Create and register a histogram."""
        return self._register(Histogram(name, help_text, buckets))

    def render(self) -> str:
        """Render every metric in Prometheus text format 0.0.4."""
        lines: list[str] = []
        for metric in self._metrics.values():
            lines.extend(metric.render_lines())
        return "\n".join(lines) + "\n" if lines else ""

    def reset(self) -> None:
        """Zero every metric while keeping registrations and set_function callbacks."""
        for metric in self._metrics.values():
            metric.reset()


_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}


async def _build_response(
    reader: asyncio.StreamReader, registry: Registry
) -> tuple[int, str, bytes, str]:
    """Read one request head and return ``(status, content_type, body, extra_headers)``."""
    plain = "text/plain; charset=utf-8"
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), REQUEST_TIMEOUT)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return 400, plain, b"bad request\n", ""
    parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        return 400, plain, b"bad request\n", ""
    method, target, _ = parts
    if method != "GET":
        return 405, plain, b"method not allowed\n", "Allow: GET\r\n"
    path = target.split("?", 1)[0]
    if path == "/metrics":
        return 200, CONTENT_TYPE, registry.render().encode("utf-8"), ""
    if path == "/healthz":
        return 200, plain, b"ok\n", ""
    return 404, plain, b"not found\n", ""


async def _handle(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, registry: Registry
) -> None:
    """Serve a single request, then close. Never raises."""
    try:
        with contextlib.suppress(Exception):
            status, ctype, body, extra = await _build_response(reader, registry)
            head = (
                f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
                f"Content-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"{extra}"
                "Connection: close\r\n\r\n"
            )
            writer.write(head.encode("latin-1") + body)
            await writer.drain()
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def start_metrics_server(registry: Registry, host: str, port: int) -> asyncio.Server:
    """Start an HTTP listener serving ``/metrics`` and ``/healthz``."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle(reader, writer, registry)

    return await asyncio.start_server(handler, host, port, limit=MAX_REQUEST_HEAD)
