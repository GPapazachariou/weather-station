"""Network lab harness: statistics helpers and one real measured point."""

import json

import netlab
import pytest
from impair_proxy import Impairment


def test_percentile_uses_nearest_rank():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert netlab.percentile(values, 50) == 5
    assert netlab.percentile(values, 99) == 10
    assert netlab.percentile(values, 1) == 1
    assert netlab.percentile([], 50) is None


def test_jain_index_bounds():
    assert netlab.jain_index([10, 10, 10, 10]) == pytest.approx(1.0)
    assert netlab.jain_index([40, 0, 0, 0]) == pytest.approx(0.25)
    assert netlab.jain_index([]) is None
    assert netlab.jain_index([0, 0]) is None


def test_latency_summary_and_cdf_are_in_milliseconds():
    summary = netlab.latency_summary([0.001, 0.002, 0.003, 0.010])
    assert summary == {"count": 4, "mean": 4.0, "p50": 2.0, "p95": 10.0, "p99": 10.0, "max": 10.0}
    assert netlab.latency_summary([])["p99"] is None
    cdf = netlab.cdf_points([0.004, 0.001, 0.002, 0.003])
    assert cdf == [[1.0, 0.25], [2.0, 0.5], [3.0, 0.75], [4.0, 1.0]]
    assert netlab.cdf_points([]) == []
    assert len(netlab.cdf_points([i / 1000 for i in range(1000)], points=100)) == 100


async def test_measured_point_through_impaired_link(tmp_path):
    """Real server process + proxy: added delay must show up in the ACK latency."""
    server = netlab.ServerProcess(tmp_path)
    await server.start()
    try:
        fast, _ = await netlab.measure_point(server, Impairment(), "LAB-TEST-FAST", 0.5)
        slow, latencies = await netlab.measure_point(
            server, Impairment(delay_ms=40), "LAB-TEST-SLOW", 0.5
        )
        metrics = await server.metrics()
        count, max_seq = server.station_rows("LAB-TEST-SLOW")
    finally:
        await server.stop()

    assert fast["errors"] == 0 and slow["errors"] == 0
    assert slow["ack_ms"]["p50"] >= 80  # two one-way delays of 40 ms
    assert fast["readings_per_s"] > slow["readings_per_s"] > 0
    assert count == slow["batches_acked"] * netlab.BATCH_SIZE == max_seq + 1
    assert metrics["weather_readings_inserted_total"] >= count
    assert len(latencies) == slow["batches_acked"]


def test_report_renders_from_results(tmp_path):
    row = {
        "delay_ms": 0, "jitter_ms": 0, "stall_prob": 0.0, "stall_ms": 200, "nominal_rtt_ms": 0,
        "batch_size": 10, "duration_s": 1.0, "batches_acked": 10, "readings_per_s": 100.0,
        "backpressure": 0, "errors": 0, "model_readings_per_s": 100.0,
        "ack_ms": {"count": 10, "mean": 1.0, "p50": 1.0, "p95": 2.0, "p99": 3.0, "max": 3.0},
    }
    results = {
        "meta": {"label": "unit", "recorded_at": "now", "python": "3", "platform": "p",
                 "sqlite": "3", "git": "abc", "point_duration_s": 1, "batch_size": 10},
        "rtt_sweep": [row],
        "ack_latency_cdf": {"0": [[1.0, 1.0]]},
        "stall_sweep": [row],
        "fairness": {
            "stations": 2, "delay_ms": 10, "duration_s": 1, "batch_size": 10,
            "per_station_readings_per_s": [90.0, 110.0], "total_readings_per_s": 200.0,
            "jain_index": 0.99, "backpressure": 0, "errors": 0, "ack_ms": row["ack_ms"],
            "server": {"queue_depth_max": 1.0, "commit_ms_mean": 0.5},
        },
        "partition": [{
            "mode": "reset", "outage_s": 5.0, "batch_interval_s": 0.2, "detect_s": 0.1,
            "first_write_after_heal_s": 1.5, "backlog_drained_s": 1.6, "missing": 0,
            "duplicates_replayed": 0, "sampling_shortfall": 12,
        }],
    }
    report = netlab.render_report(json.loads(json.dumps(results)))
    assert "# Network lab results: unit" in report
    assert "| 0 | 100.0 | 100.0 | 1.0 | 2.0 | 3.0 |" in report
    assert "Jain's fairness index: 0.99" in report
    assert "| reset | 5 | 0.1 | 1.5 | 1.6 | 0 | 0 | 12 |" in report
    results.update(stall_sweep=[], fairness=None, partition=[])
    assert "Fairness" not in netlab.render_report(results)
