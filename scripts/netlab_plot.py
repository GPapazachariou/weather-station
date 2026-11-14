"""Turn a network-lab ``results.json`` into static SVG charts (standard library only).

Usage: ``python scripts/netlab_plot.py path/to/results.json [--out DIR]``

Writes ``goodput_vs_rtt.svg``, ``ack_latency_cdf.svg``, ``goodput_vs_stall.svg`` and
``fairness.svg``. Charts whose data is missing or empty are skipped.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

W, H = 760, 440
LEFT, RIGHT, BOTTOM = 76, 28, 56
INK, MUTED, GRID, AXIS = "#333", "#666", "#e5e5e5", "#999"
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
# Colour-blind-safe palette (Okabe-Ito), used in this fixed order.
BLUE, VERMILION, GREEN, PURPLE, ORANGE, SKY, BLACK = (
    "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000")
PALETTE = [BLUE, VERMILION, GREEN, PURPLE, ORANGE, SKY, BLACK]
MAX_CDF_POINTS = 300  # CDFs are thinned to roughly this many vertices per line


# --------------------------------------------------------------------------- number formatting


def esc(value: object) -> str:
    """XML-escape any value for use in text or attributes."""
    return html.escape(str(value), quote=True)


def fmt_num(v: float) -> str:
    """Plain number without trailing zeros or a trailing ``.0``."""
    return f"{round(v, 10) + 0.0:.10g}"


def fmt_log(v: float) -> str:
    """Log-axis label: 10, 100, 1k, 2.5k, 1M."""
    if v >= 1e6:
        return fmt_num(v / 1e6) + "M"
    if v >= 1e3:
        return fmt_num(v / 1e3) + "k"
    return fmt_num(v)


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Evenly spaced ticks (step 1/2/5 x 10^n) covering ``[lo, hi]``."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if raw <= m * mag * (1 + 1e-9))
    start = math.floor(lo / step + 1e-9)
    end = math.ceil(hi / step - 1e-9)
    return [round(i * step, 10) for i in range(start, end + 1)]


# --------------------------------------------------------------------------- axes


@dataclass
class Axis:
    """A scale from data values to a 0..1 fraction, plus its ticks and title."""

    lo: float
    hi: float
    ticks: list[float]
    label: str
    log: bool = False
    fmt: Callable[[float], str] = fmt_num

    def frac(self, v: float) -> float:
        """Position of ``v`` along the axis; values <= 0 on a log axis clamp to ``lo``."""
        if self.log:
            v = max(v, self.lo)
            return (math.log10(v) - math.log10(self.lo)) / (
                math.log10(self.hi) - math.log10(self.lo))
        return (v - self.lo) / (self.hi - self.lo)


def linear_axis(label: str, vmax: float, fmt: Callable[[float], str] = fmt_num) -> Axis:
    """Linear axis starting at 0 with nice ticks up to ``vmax``."""
    ticks = nice_ticks(0, max(vmax, 0.0))
    return Axis(ticks[0], ticks[-1], ticks, label, fmt=fmt)


def log_axis(label: str, values: Sequence[float], steps: Sequence[int] = (1,)) -> Axis:
    """Log10 axis fitted to the positive ``values``; ``steps`` picks tick multiples (1, 2, 5)."""
    pos = [v for v in values if v > 0 and math.isfinite(v)] or [1.0, 10.0]
    mn, mx = min(pos), max(pos)
    if math.log10(mx / mn) > 3:
        steps = (1,)
    d0, d1 = math.floor(math.log10(mn)) - 1, math.ceil(math.log10(mx)) + 1
    marks = sorted(round(s * 10.0**d, 12) for d in range(d0, d1 + 1) for s in steps)
    lo = max(m for m in marks if m <= mn * (1 + 1e-9))
    hi = min(m for m in marks if m >= mx * (1 - 1e-9))
    if hi <= lo:
        hi = min(m for m in marks if m > lo * (1 + 1e-9))
    ticks = [m for m in marks if lo * (1 - 1e-9) <= m <= hi * (1 + 1e-9)]
    return Axis(lo, hi, ticks, label, log=True, fmt=fmt_log)


# --------------------------------------------------------------------------- chart canvas


def _text(x: float, y: float, s: object, size: int = 11, anchor: str = "start",
          fill: str = INK, bold: bool = False, halo: bool = False, extra: str = "") -> str:
    attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{fill}"'
    if bold:
        attrs += ' font-weight="bold"'
    if halo:  # white outline keeps direct labels legible over lines and bars
        attrs += ' stroke="#fff" stroke-width="3" paint-order="stroke"'
    if extra:
        attrs += f" {extra}"
    return f"<text {attrs}>{esc(s)}</text>"


def _dash(dashed: bool) -> str:
    return ' stroke-dasharray="6 4"' if dashed else ""


class Chart:
    """One 760x440 chart: title block, legend, axes, gridlines, then data primitives."""

    def __init__(self, title: str, subtitle: str, x: Axis, y: Axis,
                 legend: Sequence[tuple[str, str, bool]] = ()) -> None:
        self.title, self.x, self.y = title, x, y
        self.data: list[str] = []
        self.front = [_text(24, 28, title, 16, bold=True), _text(24, 46, subtitle, 12, fill=MUTED)]
        rows = self._legend_rows(legend)
        self.top = 64 + 20 * len(rows) if rows else 70
        self.right = W - RIGHT
        self.bottom = H - BOTTOM
        for i, row in enumerate(rows):
            cx = self.right - (sum(item[3] for item in row) - 18)
            cy = 66 + 20 * i
            for label, color, dashed, width in row:
                self.front.append(
                    f'<line x1="{cx:.1f}" x2="{cx + 22:.1f}" y1="{cy}" y2="{cy}" '
                    f'stroke="{color}" stroke-width="2"{_dash(dashed)}/>')
                self.front.append(_text(cx + 28, cy + 4, label, 12))
                cx += width
        self.back = self._frame()

    def _legend_rows(self, items: Sequence[tuple[str, str, bool]]) -> list[list]:
        rows: list[list] = [[]]
        used = 0.0
        for label, color, dashed in items:
            width = 28 + 6.6 * len(label) + 18
            if rows[-1] and used + width > W - RIGHT - LEFT:
                rows.append([])
                used = 0.0
            rows[-1].append((label, color, dashed, width))
            used += width
        return rows if items else []

    def _frame(self) -> list[str]:
        out = [f'<rect width="{W}" height="{H}" fill="#fff"/>']
        for t in self.y.ticks:  # horizontal gridlines only
            y = self.py(t)
            out.append(f'<line x1="{LEFT}" x2="{self.right}" y1="{y:.1f}" y2="{y:.1f}" '
                       f'stroke="{GRID}" stroke-width="1"/>')
            out.append(_text(LEFT - 8, y + 4, self.y.fmt(t), anchor="end"))
        out.append(f'<line x1="{LEFT}" x2="{self.right}" y1="{self.bottom}" y2="{self.bottom}" '
                   f'stroke="{AXIS}" stroke-width="1"/>')
        for t in self.x.ticks:
            x = self.px(t)
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{self.bottom}" y2="{self.bottom + 4}" '
                       f'stroke="{AXIS}" stroke-width="1"/>')
            out.append(_text(x, self.bottom + 18, self.x.fmt(t), anchor="middle"))
        out.append(_text((LEFT + self.right) / 2, H - 12, self.x.label, 12, anchor="middle"))
        mid = (self.top + self.bottom) / 2
        out.append(_text(0, 0, self.y.label, 12, anchor="middle",
                         extra=f'transform="translate(16 {mid:.1f}) rotate(-90)"'))
        return out

    def px(self, v: float) -> float:
        return LEFT + self.x.frac(v) * (self.right - LEFT)

    def py(self, v: float) -> float:
        return self.bottom - self.y.frac(v) * (self.bottom - self.top)

    def line(self, pts: Sequence[tuple[float, float]], color: str, dashed: bool = False,
             title: str = "") -> None:
        """Polyline through data-space points (width 2, round joins)."""
        coords = " ".join(f"{self.px(x):.1f},{self.py(y):.1f}" for x, y in pts)
        tip = f"<title>{esc(title)}</title>" if title else ""
        self.data.append(f'<polyline points="{coords}" fill="none" stroke="{color}" '
                         f'stroke-width="2" stroke-linejoin="round" '
                         f'stroke-linecap="round"{_dash(dashed)}>{tip}</polyline>')

    def markers(self, pts: Sequence[tuple[float, float]], tips: Sequence[str],
                color: str) -> None:
        """Round markers (r=3.5), each with a tooltip carrying its exact values."""
        for (x, y), tip in zip(pts, tips, strict=True):
            self.data.append(
                f'<circle cx="{self.px(x):.1f}" cy="{self.py(y):.1f}" r="3.5" fill="{color}" '
                f'stroke="#fff" stroke-width="1.5"><title>{esc(tip)}</title></circle>')

    def hline(self, value: float, label: str, color: str, below: bool = False) -> None:
        """Faint dashed horizontal reference line with a label at its left end."""
        y = self.py(value)
        self.data.append(f'<line x1="{LEFT}" x2="{self.right}" y1="{y:.1f}" y2="{y:.1f}" '
                         f'stroke="{color}" stroke-width="1.5" stroke-dasharray="4 4"/>')
        self.data.append(_text(LEFT + 6, y + (14 if below else -6), label, 11, halo=True))

    def svg(self) -> str:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                f'viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" '
                f'aria-label="{esc(self.title)}">')
        return "\n".join([head, *self.back, *self.data, *self.front, "</svg>\n"])


# --------------------------------------------------------------------------- the four charts


def _ms(v: float) -> str:
    return fmt_num(round(v, 2))


def goodput_vs_rtt(results: dict) -> str | None:
    """Measured vs modelled goodput against added RTT (log y)."""
    rows = sorted(results.get("rtt_sweep") or [], key=lambda r: r["nominal_rtt_ms"])
    if not rows:
        return None
    meta = results.get("meta") or {}
    batch = meta.get("batch_size", rows[0].get("batch_size", "?"))
    measured = [(r["nominal_rtt_ms"], r["readings_per_s"]) for r in rows]
    has_model = all(r.get("model_readings_per_s") is not None for r in rows)
    model = [(r["nominal_rtt_ms"], r["model_readings_per_s"]) for r in rows] if has_model else []
    x = linear_axis("Added round-trip time (ms)", max(p[0] for p in measured))
    y = log_axis("Goodput (readings/s, log scale)", [p[1] for p in measured + model])
    legend = [("Measured (stop-and-wait)", BLUE, False)]
    if has_model:
        legend.append(("Model: batch / (RTT + service time)", VERMILION, True))
    chart = Chart("Goodput against round-trip time",
                  f"{meta.get('label', 'results')} · batch of {batch} readings · one station",
                  x, y, legend)
    if has_model:
        chart.line(model, VERMILION, dashed=True, title="Model")
    chart.line(measured, BLUE, title="Measured")
    tips = []
    for r in rows:
        ack = r.get("ack_ms") or {}
        extra = f"; ACK p50 {_ms(ack['p50'])} ms, p99 {_ms(ack['p99'])} ms" if ack else ""
        tips.append(f"RTT {_ms(r['nominal_rtt_ms'])} ms: {fmt_num(round(r['readings_per_s'], 1))} "
                    f"readings/s measured{extra}")
    chart.markers(measured, tips, BLUE)
    return chart.svg()


def _thin(pts: list[list[float]]) -> list[list[float]]:
    step = max(1, len(pts) // MAX_CDF_POINTS)
    out = pts[::step]
    return out if out[-1] is pts[-1] else [*out, pts[-1]]


def ack_latency_cdf(results: dict) -> str | None:
    """One CDF line per one-way delay; log x, linear y with a p99 reference line."""
    curves = sorted(((float(k), v) for k, v in (results.get("ack_latency_cdf") or {}).items()
                     if v), key=lambda kv: kv[0])
    if not curves:
        return None
    meta = results.get("meta") or {}
    x = log_axis("ACK latency (ms, log scale)", [p[0] for _, c in curves for p in c], (1, 2, 5))
    y = Axis(0, 1, [0, 0.25, 0.5, 0.75, 1], "Cumulative share of ACKs",
             fmt=lambda v: f"{round(v * 100)}%")
    names = [f"RTT {fmt_num(2 * d)} ms" for d, _ in curves]
    chart = Chart("ACK latency distribution",
                  f"{meta.get('label', 'results')} · fraction of batches acknowledged within "
                  "a given latency", x, y,
                  [(n, PALETTE[i % len(PALETTE)], False) for i, n in enumerate(names)])
    chart.hline(0.99, "p99", AXIS, below=True)
    for i, ((_, pts), name) in enumerate(zip(curves, names, strict=True)):
        chart.line([(p[0], p[1]) for p in _thin(pts)], PALETTE[i % len(PALETTE)], title=name)
    return chart.svg()


def goodput_vs_stall(results: dict) -> str | None:
    """Goodput against stall probability (percent), linear axes from 0."""
    rows = sorted(results.get("stall_sweep") or [], key=lambda r: r["stall_prob"])
    if not rows:
        return None
    meta = results.get("meta") or {}
    pts = [(r["stall_prob"] * 100, r["readings_per_s"]) for r in rows]
    x = linear_axis("Stall probability per chunk (%)", max(p[0] for p in pts))
    y = linear_axis("Goodput (readings/s)", max(p[1] for p in pts))
    rtt = rows[0].get("nominal_rtt_ms", 0)
    stall = max(r.get("stall_ms", 0) for r in rows)
    chart = Chart("Goodput against loss-like stalls",
                  f"{meta.get('label', 'results')} · {fmt_num(rtt)} ms added RTT · "
                  f"{fmt_num(stall)} ms stalls", x, y)
    chart.line(pts, BLUE)
    chart.markers(pts, [f"{fmt_num(round(px_, 3))}% stalls: {fmt_num(round(py_, 1))} readings/s"
                        for px_, py_ in pts], BLUE)
    return chart.svg()


def fairness(results: dict) -> str | None:
    """Sorted per-station goodput bars with a dashed mean line."""
    fair = results.get("fairness") or {}
    vals = sorted(fair.get("per_station_readings_per_s") or [])
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    y = linear_axis("Readings/s", max(max(vals), mean) * 1.08)
    x = Axis(0, 1, [], "Station (sorted by goodput)")
    sub = (f"{fair.get('stations', len(vals))} stations · Jain's index "
           f"{fmt_num(round(fair.get('jain_index', 0), 4))} · total "
           f"{fmt_num(round(fair.get('total_readings_per_s', sum(vals)), 1))} readings/s")
    chart = Chart("Per-station goodput", sub, x, y)
    slot = (chart.right - LEFT) / len(vals)
    base = chart.py(0)
    for i, v in enumerate(vals):
        top = chart.py(v)
        chart.data.append(
            f'<rect class="bar" x="{LEFT + i * slot + 1:.1f}" y="{top:.1f}" '
            f'width="{max(slot - 2, 1):.1f}" height="{max(base - top, 0):.1f}" fill="{BLUE}">'
            f"<title>{esc(f'Station rank {i + 1}: {fmt_num(round(v, 1))} readings/s')}</title>"
            "</rect>")
    chart.hline(mean, f"mean {fmt_num(round(mean, 1))}", VERMILION)
    return chart.svg()


CHARTS = (
    ("goodput_vs_rtt.svg", goodput_vs_rtt),
    ("ack_latency_cdf.svg", ack_latency_cdf),
    ("goodput_vs_stall.svg", goodput_vs_stall),
    ("fairness.svg", fairness),
)


def render_all(results: dict, out_dir: Path) -> list[Path]:
    """Write every chart that has data into ``out_dir``; return the paths written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, build in CHARTS:
        svg = build(results)
        if svg is None:
            continue
        path = out_dir / name
        path.write_text(svg, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Render netlab results.json as SVG charts.")
    parser.add_argument("results", type=Path, help="path to results.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: the JSON's folder)")
    args = parser.parse_args(argv)
    results = json.loads(args.results.read_text(encoding="utf-8"))
    for path in render_all(results, args.out or args.results.parent):
        print(path)


if __name__ == "__main__":
    main()
