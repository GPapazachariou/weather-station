"""Tests for scripts/netlab_plot.py (SVG chart rendering from netlab results)."""

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import netlab_plot

NS = "{http://www.w3.org/2000/svg}"
NAMES = ["goodput_vs_rtt.svg", "ack_latency_cdf.svg", "goodput_vs_stall.svg", "fairness.svg"]


def _ack(mean: float) -> dict:
    return {"count": 100, "mean": mean, "p50": mean, "p95": mean * 2, "p99": mean * 3,
            "max": mean * 5}


def _row(rtt: int, rate: float, prob: float = 0.0, model: float | None = None) -> dict:
    row = {"delay_ms": rtt / 2, "jitter_ms": 0.0, "stall_prob": prob, "stall_ms": 200.0,
           "nominal_rtt_ms": rtt, "batch_size": 10, "duration_s": 6.0, "batches_acked": 100,
           "readings_per_s": rate, "backpressure": 0, "errors": 0, "ack_ms": _ack(rtt + 1.0)}
    if model is not None:
        row["model_readings_per_s"] = model
    return row


def _cdf(base: float) -> list[list[float]]:
    return [[base + i * 0.5, (i + 1) / 20] for i in range(20)]


def make_results() -> dict:
    rates = {0: 8000.0, 25: 3000.0, 50: 1600.0, 100: 830.0, 200: 420.0}
    return {
        "meta": {"label": "phase0-baseline", "batch_size": 10, "point_duration_s": 6.0},
        "rtt_sweep": [_row(r, v, model=v * 1.02) for r, v in rates.items()],
        "ack_latency_cdf": {"0": _cdf(1.0), "25.0": _cdf(50.0), "100": _cdf(200.0)},
        "stall_sweep": [_row(50, 1500 - 600 * p * 5, prob=p) for p in (0, 0.01, 0.02, 0.05, 0.1)],
        "fairness": {"stations": 12, "delay_ms": 10, "duration_s": 10, "batch_size": 10,
                     "per_station_readings_per_s": [70.0 + i for i in range(12)],
                     "total_readings_per_s": 940.0, "jain_index": 0.99912, "backpressure": 0,
                     "errors": 0, "ack_ms": _ack(5.0),
                     "server": {"queue_depth_max": 12.0, "commit_ms_mean": 0.8}},
        "partition": [],
    }


def parse(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def texts(root: ET.Element) -> list[str]:
    return ["".join(t.itertext()) for t in root.iter(f"{NS}text")]


def test_renders_four_valid_svgs(tmp_path: Path):
    written = netlab_plot.render_all(make_results(), tmp_path)
    assert [p.name for p in written] == NAMES
    for path in written:
        root = parse(path)
        assert path.parent == tmp_path
        assert root.tag == f"{NS}svg"
        assert root.get("viewBox") == "0 0 760 440"


def test_goodput_vs_rtt_content(tmp_path: Path):
    results = make_results()
    netlab_plot.render_all(results, tmp_path)
    root = parse(tmp_path / "goodput_vs_rtt.svg")
    labels = texts(root)
    assert "Goodput against round-trip time" in labels
    assert "phase0-baseline · batch of 10 readings · one station" in labels
    assert "Measured (stop-and-wait)" in labels
    assert "Model: batch / (RTT + service time)" in labels
    assert len(list(root.iter(f"{NS}circle"))) == len(results["rtt_sweep"])
    assert {"100", "1k", "10k"} <= set(labels)  # decade labels on the log axis
    assert len(list(root.iter(f"{NS}polyline"))) == 2


def test_model_series_dropped_when_any_row_lacks_it(tmp_path: Path):
    results = make_results()
    del results["rtt_sweep"][2]["model_readings_per_s"]
    netlab_plot.render_all(results, tmp_path)
    root = parse(tmp_path / "goodput_vs_rtt.svg")
    assert not any(t.startswith("Model") for t in texts(root))
    assert len(list(root.iter(f"{NS}polyline"))) == 1


def test_cdf_lines_legend_and_p99(tmp_path: Path):
    netlab_plot.render_all(make_results(), tmp_path)
    root = parse(tmp_path / "ack_latency_cdf.svg")
    labels = texts(root)
    assert "ACK latency distribution" in labels
    legend = [t for t in labels if t.startswith("RTT ")]
    assert legend == ["RTT 0 ms", "RTT 50 ms", "RTT 200 ms"]
    assert "p99" in labels
    assert {"0%", "25%", "50%", "75%", "100%"} <= set(labels)
    assert len(list(root.iter(f"{NS}polyline"))) == 3
    assert not list(root.iter(f"{NS}circle"))


def test_goodput_vs_stall_content(tmp_path: Path):
    netlab_plot.render_all(make_results(), tmp_path)
    root = parse(tmp_path / "goodput_vs_stall.svg")
    labels = texts(root)
    assert "Goodput against loss-like stalls" in labels
    assert any("50 ms added RTT" in t and "200 ms stalls" in t for t in labels)
    assert len(list(root.iter(f"{NS}circle"))) == 5


def test_fairness_bars_and_mean(tmp_path: Path):
    results = make_results()
    netlab_plot.render_all(results, tmp_path)
    root = parse(tmp_path / "fairness.svg")
    bars = [r for r in root.iter(f"{NS}rect") if r.get("class") == "bar"]
    assert len(bars) == len(results["fairness"]["per_station_readings_per_s"])
    heights = [float(b.get("height", "0")) for b in bars]
    assert heights == sorted(heights)
    labels = texts(root)
    assert "Per-station goodput" in labels
    assert "12 stations · Jain's index 0.9991 · total 940 readings/s" in labels
    assert "mean 75.5" in labels


def test_empty_stall_and_missing_fairness_are_skipped(tmp_path: Path):
    results = make_results()
    results["stall_sweep"] = []
    results["fairness"] = None
    written = netlab_plot.render_all(results, tmp_path)
    assert [p.name for p in written] == ["goodput_vs_rtt.svg", "ack_latency_cdf.svg"]
    assert not (tmp_path / "goodput_vs_stall.svg").exists()
    assert not (tmp_path / "fairness.svg").exists()

    del results["fairness"]
    del results["stall_sweep"]
    assert len(netlab_plot.render_all(results, tmp_path / "again")) == 2


def test_zero_values_on_log_axes_do_not_crash(tmp_path: Path):
    results = make_results()
    results["rtt_sweep"][-1]["readings_per_s"] = 0.0
    results["rtt_sweep"][-1]["model_readings_per_s"] = 0
    results["ack_latency_cdf"]["0"][0] = [0.0, 0.05]
    written = netlab_plot.render_all(results, tmp_path)
    assert len(written) == 4
    for path in written:
        parse(path)


def test_text_is_xml_escaped(tmp_path: Path):
    results = make_results()
    results["meta"]["label"] = 'a<b & "c"'
    netlab_plot.render_all(results, tmp_path)
    root = parse(tmp_path / "goodput_vs_rtt.svg")  # would raise on bad markup
    assert 'a<b & "c" · batch of 10 readings · one station' in texts(root)


def test_nice_ticks_and_formatting():
    assert netlab_plot.nice_ticks(0, 200) == [0, 50, 100, 150, 200]
    assert netlab_plot.nice_ticks(0, 8333) == [0, 2000, 4000, 6000, 8000, 10000]
    assert netlab_plot.nice_ticks(0, 10) == [0, 2, 4, 6, 8, 10]
    assert netlab_plot.nice_ticks(0, 0) == [0, 0.2, 0.4, 0.6, 0.8, 1]
    assert netlab_plot.fmt_num(25.0) == "25"
    assert netlab_plot.fmt_num(0.1 + 0.2) == "0.3"
    assert [netlab_plot.fmt_log(v) for v in (10, 100, 1000, 2500, 10000, 2e6)] == [
        "10", "100", "1k", "2.5k", "10k", "2M"]


def test_log_axis_decades_and_guard():
    axis = netlab_plot.log_axis("y", [0, 830.0, 8000.0])
    assert (axis.lo, axis.hi) == (100, 10000)
    assert axis.ticks == [100, 1000, 10000]
    assert axis.frac(0) == 0.0  # non-positive values clamp to the bottom
    assert netlab_plot.log_axis("y", [0, -1]).ticks  # nothing positive: still a valid axis


def test_main_writes_next_to_json(tmp_path: Path, capsys):
    src = tmp_path / "run" / "results.json"
    src.parent.mkdir()
    src.write_text(json.dumps(make_results()), encoding="utf-8")
    netlab_plot.main([str(src)])
    printed = capsys.readouterr().out.split()
    assert sorted(Path(p).name for p in printed) == sorted(NAMES)
    assert all((src.parent / n).exists() for n in NAMES)

    out = tmp_path / "elsewhere"
    netlab_plot.main([str(src), "--out", str(out)])
    assert all((out / n).exists() for n in NAMES)


def test_render_all_does_not_mutate_input(tmp_path: Path):
    results = make_results()
    before = copy.deepcopy(results)
    netlab_plot.render_all(results, tmp_path)
    assert results == before
