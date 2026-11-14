#!/usr/bin/env python3
"""
Network impairment lab: measure the ingest path on a degraded link.

Starts a real server process on a temporary database, puts impair_proxy.py in
front of it, and records:

  rtt_sweep   goodput and ACK latency of one station against added latency
  stall_sweep the same against loss-like stalls at a fixed latency
  fairness    per-station goodput and Jain's index for many concurrent stations
  partition   detection, recovery and replay after a link outage, using the
              real station client process

Senders in the sweeps reuse the station client's own stop-and-wait exchange
(one batch, wait for the reply, next batch) with no pause between batches, so
the numbers are the protocol's ceiling, not the demo's 5 s sample period.

Usage:
    python scripts/netlab.py --label phase0-baseline
    python scripts/netlab.py --label smoke --quick

Writes <out>/<label>/results.json and report.md. Render charts with
scripts/netlab_plot.py. Never touches demo or user data.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import socket
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "station_client"))
sys.path.insert(0, str(ROOT / "scripts"))

import client
from impair_proxy import Impairment, ImpairmentProxy

BATCH_SIZE = 10
REPLY_TIMEOUT = 30.0


# ---------------------------------------------------------------- statistics


def percentile(sorted_values, pct):
    """Nearest-rank percentile of an ascending list (None when empty)."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def jain_index(values):
    """Jain's fairness index: 1.0 = perfectly even, 1/n = one flow takes everything."""
    total = sum(values)
    squares = sum(v * v for v in values)
    if not values or squares == 0:
        return None
    return total * total / (len(values) * squares)


def latency_summary(latencies_s):
    """ACK latency summary in milliseconds."""
    ordered = sorted(x * 1000 for x in latencies_s)
    if not ordered:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None, "max": None}
    return {
        "count": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 3),
        "p50": round(percentile(ordered, 50), 3),
        "p95": round(percentile(ordered, 95), 3),
        "p99": round(percentile(ordered, 99), 3),
        "max": round(ordered[-1], 3),
    }


def cdf_points(latencies_s, points=100):
    """Downsample latencies to evenly spaced quantiles: [[ms, cumulative_fraction], ...]."""
    ordered = sorted(x * 1000 for x in latencies_s)
    if not ordered:
        return []
    steps = min(points, len(ordered))
    return [
        [round(percentile(ordered, 100 * i / steps), 3), round(i / steps, 4)]
        for i in range(1, steps + 1)
    ]


# ---------------------------------------------------------------- server process


def free_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


class ServerProcess:
    """The real server/app.py on a temporary database and loopback ports."""

    def __init__(self, directory: Path):
        self.db_path = directory / "netlab.db"
        self.log_path = directory / "server.log"
        self.port = free_port()
        self.metrics_port = free_port()
        self.process = None
        self._log = None

    async def start(self):
        self._log = self.log_path.open("w")
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "server" / "app.py"),
            env={
                **os.environ,
                "DB_PATH": str(self.db_path),
                "SERVER_HOST": "127.0.0.1",
                "SERVER_PORT": str(self.port),
                "METRICS_HOST": "127.0.0.1",
                "METRICS_PORT": str(self.metrics_port),
                "LOG_LEVEL": "WARNING",
                "PYTHONUNBUFFERED": "1",
            },
            stdout=self._log, stderr=self._log,
        )
        for _ in range(200):
            if self.process.returncode is not None:
                raise RuntimeError(self.log_path.read_text())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                writer.write(b'{"request":"ping"}\n')
                await writer.drain()
                reply = json.loads(await asyncio.wait_for(reader.readline(), 1))
                writer.close()
                await writer.wait_closed()
                if reply.get("pong"):
                    return
            except (OSError, asyncio.TimeoutError, ValueError):
                pass
            await asyncio.sleep(0.05)
        raise TimeoutError("Server did not become ready")

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 15)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._log:
            self._log.close()

    async def metrics(self):
        """Scrape /metrics into {sample_name_with_labels: float}."""
        reader, writer = await asyncio.open_connection("127.0.0.1", self.metrics_port)
        writer.write(b"GET /metrics HTTP/1.1\r\nHost: netlab\r\n\r\n")
        await writer.drain()
        raw = (await asyncio.wait_for(reader.read(), 5)).decode()
        writer.close()
        await writer.wait_closed()
        samples = {}
        for line in raw.split("\r\n\r\n", 1)[-1].splitlines():
            if line and not line.startswith("#"):
                name, _, value = line.rpartition(" ")
                samples[name] = float(value)
        return samples

    def station_rows(self, station_id):
        """(count, max_seq) persisted for one station, read straight from SQLite."""
        with sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True) as db:
            return db.execute(
                "SELECT COUNT(*), MAX(seq) FROM readings WHERE station_id = ?", (station_id,)
            ).fetchone()


# ---------------------------------------------------------------- load generation


async def stop_and_wait_sender(port, station_id, duration, batch_size=BATCH_SIZE):
    """
    Saturating stop-and-wait sender: the same write / drain / readline exchange
    as client.send_batches, minus the sleep between batches.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    latencies, acked, backpressure, errors, seq = [], 0, 0, 0, 0
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + duration
    try:
        while loop.time() < deadline:
            batch = client.generate_batch(station_id, batch_size, start_seq=seq)
            sent = time.perf_counter()
            writer.write((json.dumps(batch) + "\n").encode("utf-8"))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), REPLY_TIMEOUT)
            if not line:
                raise ConnectionError("Server closed connection")
            response = json.loads(line)
            if response.get("status") == "ok":
                latencies.append(time.perf_counter() - sent)
                acked += response["inserted"]
                seq += batch_size
            elif client.is_backpressure_response(response):
                backpressure += 1
                await asyncio.sleep(float(response.get("retry_after", 1)))
            else:
                errors += 1
                seq += batch_size
        elapsed = loop.time() - started
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    return {
        "station_id": station_id,
        "elapsed_s": elapsed,
        "readings_acked": acked,
        "readings_per_s": acked / elapsed if elapsed else 0.0,
        "backpressure": backpressure,
        "errors": errors,
        "latencies": latencies,
    }


async def measure_point(server, impairment, station_id, duration):
    """One station through an impaired link. Returns a result row plus raw latencies."""
    async with ImpairmentProxy("127.0.0.1", server.port, impairment=impairment, seed=1) as proxy:
        run = await stop_and_wait_sender(proxy.port, station_id, duration)
    row = {
        "delay_ms": impairment.delay_ms,
        "jitter_ms": impairment.jitter_ms,
        "stall_prob": impairment.stall_prob,
        "stall_ms": impairment.stall_ms,
        "nominal_rtt_ms": 2 * impairment.delay_ms,
        "batch_size": BATCH_SIZE,
        "duration_s": round(run["elapsed_s"], 3),
        "batches_acked": len(run["latencies"]),
        "readings_per_s": round(run["readings_per_s"], 2),
        "backpressure": run["backpressure"],
        "errors": run["errors"],
        "ack_ms": latency_summary(run["latencies"]),
    }
    return row, run["latencies"]


async def rtt_sweep(server, delays_ms, duration):
    rows, cdfs = [], {}
    for delay in delays_ms:
        row, latencies = await measure_point(
            server, Impairment(delay_ms=delay), f"LAB-RTT-{int(delay)}", duration
        )
        rows.append(row)
        cdfs[str(delay)] = cdf_points(latencies)
        print(f"  rtt_sweep delay={delay:>5} ms -> {row['readings_per_s']:>9.1f} readings/s, "
              f"ack p50={row['ack_ms']['p50']} ms p99={row['ack_ms']['p99']} ms", flush=True)

    # Stop-and-wait model: one batch per (2 * delay + service time). The service
    # time is the measured mean ACK latency on the unimpaired link.
    base = next((r for r in rows if r["delay_ms"] == 0), None)
    if base and base["ack_ms"]["mean"]:
        for row in rows:
            cycle_s = (base["ack_ms"]["mean"] + row["nominal_rtt_ms"]) / 1000
            row["model_readings_per_s"] = round(BATCH_SIZE / cycle_s, 2)
    return rows, cdfs


async def stall_sweep(server, probabilities, delay_ms, stall_ms, duration):
    rows = []
    for index, prob in enumerate(probabilities):
        impairment = Impairment(delay_ms=delay_ms, stall_prob=prob, stall_ms=stall_ms)
        row, _ = await measure_point(server, impairment, f"LAB-STALL-{index}", duration)
        rows.append(row)
        print(f"  stall_sweep p={prob:<5} -> {row['readings_per_s']:>9.1f} readings/s, "
              f"ack p99={row['ack_ms']['p99']} ms", flush=True)
    return rows


async def fairness(server, stations, delay_ms, duration):
    before = await server.metrics()
    queue_depths = []

    async def watch_queue():
        while True:
            queue_depths.append((await server.metrics()).get("weather_write_queue_depth", 0))
            await asyncio.sleep(0.2)

    impairment = Impairment(delay_ms=delay_ms)
    async with ImpairmentProxy("127.0.0.1", server.port, impairment=impairment, seed=2) as proxy:
        watcher = asyncio.create_task(watch_queue())
        try:
            runs = await asyncio.gather(*(
                stop_and_wait_sender(proxy.port, f"LAB-FAIR-{i:03d}", duration)
                for i in range(stations)
            ))
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    after = await server.metrics()

    rates = [run["readings_per_s"] for run in runs]
    commits = after["weather_db_commit_seconds_count"] - before["weather_db_commit_seconds_count"]
    commit_time = after["weather_db_commit_seconds_sum"] - before["weather_db_commit_seconds_sum"]
    result = {
        "stations": stations,
        "delay_ms": delay_ms,
        "duration_s": duration,
        "batch_size": BATCH_SIZE,
        "per_station_readings_per_s": [round(r, 2) for r in sorted(rates)],
        "total_readings_per_s": round(sum(rates), 2),
        "jain_index": round(jain_index(rates), 5),
        "backpressure": sum(run["backpressure"] for run in runs),
        "errors": sum(run["errors"] for run in runs),
        "ack_ms": latency_summary([x for run in runs for x in run["latencies"]]),
        "server": {
            "queue_depth_max": max(queue_depths, default=0),
            "commit_ms_mean": round(commit_time / commits * 1000, 3) if commits else None,
        },
    }
    print(f"  fairness n={stations} -> total {result['total_readings_per_s']} readings/s, "
          f"Jain {result['jain_index']}, queue max {result['server']['queue_depth_max']}",
          flush=True)
    return result


# ---------------------------------------------------------------- partition recovery


async def partition_recovery(server, mode, outage_s, warmup_s=3.0, settle_s=25.0,
                             batch_interval=0.2, reply_timeout=2.0):
    """
    Run the real station client through the proxy, cut the link for outage_s,
    heal it, and time detection, first write after heal and backlog catch-up.
    """
    station_id = f"LAB-PART-{mode.upper()}"
    batch = 5
    loop = asyncio.get_running_loop()
    async with ImpairmentProxy("127.0.0.1", server.port, seed=3) as proxy:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "station_client" / "client.py"),
            "--host", "127.0.0.1", "--port", str(proxy.port), "--station-id", station_id,
            "--batch-min", str(batch), "--batch-max", str(batch),
            "--batch-interval", str(batch_interval), "--timeout", str(reply_timeout),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        failures, flushes = [], []  # loop.time() of client failure / buffer-flushed lines

        async def read_client_log():
            async for raw in process.stdout:
                text = raw.decode("utf-8", "replace")
                if "✗" in text:
                    failures.append(loop.time())
                elif "Buffer flushed successfully" in text:
                    flushes.append(loop.time())

        log_task = asyncio.create_task(read_client_log())
        samples = []  # (loop.time(), persisted_count)

        async def sample_until(deadline, stop_when=None):
            while loop.time() < deadline:
                samples.append((loop.time(), server.station_rows(station_id)[0]))
                if stop_when and stop_when():
                    return
                await asyncio.sleep(0.05)

        try:
            await sample_until(loop.time() + warmup_s)
            before = await server.metrics()
            cut_at = loop.time()
            count_at_cut = samples[-1][1]
            rate = count_at_cut / (cut_at - samples[0][0])  # steady readings/s before the cut
            proxy.partition(mode)
            await sample_until(cut_at + outage_s)
            healed_at = loop.time()
            proxy.heal()

            # The client logs once its whole backlog has been acknowledged.
            await sample_until(
                healed_at + settle_s, stop_when=lambda: any(t > healed_at for t in flushes)
            )
            await sample_until(loop.time() + 1.0)
            ended_at, count_at_end = samples[-1]
        finally:
            process.terminate()
            await process.wait()
            await asyncio.gather(log_task, return_exceptions=True)

    after = await server.metrics()
    count, max_seq = server.station_rows(station_id)
    first_write = next((t for t, c in samples if t > healed_at and c > count_at_cut), None)
    detected = next((t for t in failures if t >= cut_at), None)
    drained_at = next((t for t in flushes if t > healed_at), None)
    # Sampling runs inside the send loop, so the station takes no samples while
    # it is blocked on a reply. Shortfall = samples a fixed-rate sampler would
    # have taken since the cut, minus what was actually produced and stored.
    shortfall = round(rate * (ended_at - cut_at)) - (count_at_end - count_at_cut)
    result = {
        "mode": mode,
        "outage_s": outage_s,
        "batch_interval_s": batch_interval,
        "client_reply_timeout_s": reply_timeout,
        "steady_readings_per_s": round(rate, 2),
        "detect_s": round(detected - cut_at, 3) if detected else None,
        "first_write_after_heal_s": round(first_write - healed_at, 3) if first_write else None,
        "backlog_drained_s": round(drained_at - healed_at, 3) if drained_at else None,
        "sampling_shortfall": max(0, shortfall),
        "persisted": count,
        "highest_seq": max_seq,
        "missing": (max_seq + 1 - count) if max_seq is not None else None,
        "duplicates_replayed": int(
            after["weather_readings_duplicate_total"] - before["weather_readings_duplicate_total"]
        ),
    }
    print(f"  partition {mode}: detect {result['detect_s']} s, first write "
          f"{result['first_write_after_heal_s']} s, drained {result['backlog_drained_s']} s, "
          f"missing {result['missing']}, replayed {result['duplicates_replayed']}, "
          f"shortfall {result['sampling_shortfall']}", flush=True)
    return result


# ---------------------------------------------------------------- report


def render_report(results):
    """Markdown summary of one lab run."""
    meta = results["meta"]
    out = [
        f"# Network lab results: {meta['label']}",
        "",
        f"Recorded {meta['recorded_at']} on Python {meta['python']}, {meta['platform']} "
        f"(SQLite {meta['sqlite']}). Git revision `{meta['git']}`.",
        "",
        "Generated by `scripts/netlab.py`. Impairments come from `scripts/impair_proxy.py`, "
        "an application-level TCP proxy on loopback: latency, jitter and rate caps are applied "
        "to the byte stream, and loss is modelled as a stall of one retransmission timeout. "
        "No packet is actually dropped, so TCP congestion-window effects are not reproduced.",
        "",
        "## Goodput against round-trip time (one station, stop-and-wait)",
        "",
        f"Batches of {BATCH_SIZE} readings sent back to back. Model = batch size / "
        "(added RTT + ACK time measured on the unimpaired link).",
        "",
        "| Added RTT (ms) | Readings/s | Model readings/s | ACK p50 (ms) | ACK p95 (ms) | ACK p99 (ms) |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results["rtt_sweep"]:
        ack = row["ack_ms"]
        out.append(
            f"| {row['nominal_rtt_ms']:g} | {row['readings_per_s']} | "
            f"{row.get('model_readings_per_s', '')} | {ack['p50']} | {ack['p95']} | {ack['p99']} |"
        )
    out += ["", "![Goodput against RTT](goodput_vs_rtt.svg)", "",
            "![ACK latency CDF](ack_latency_cdf.svg)", ""]

    if results["stall_sweep"]:
        first = results["stall_sweep"][0]
        out += [
            "## Goodput against loss-like stalls",
            "",
            f"Added RTT {first['nominal_rtt_ms']:g} ms. Each forwarded chunk stalls for "
            f"{max(r['stall_ms'] for r in results['stall_sweep']):g} ms with the given probability.",
            "",
            "| Stall probability | Readings/s | ACK p50 (ms) | ACK p99 (ms) |",
            "|---:|---:|---:|---:|",
        ]
        for row in results["stall_sweep"]:
            out.append(f"| {row['stall_prob']:g} | {row['readings_per_s']} | "
                       f"{row['ack_ms']['p50']} | {row['ack_ms']['p99']} |")
        out += ["", "![Goodput against stall probability](goodput_vs_stall.svg)", ""]

    fair = results["fairness"]
    if fair:
        rates = fair["per_station_readings_per_s"]
        out += [
            "## Fairness across stations",
            "",
            f"{fair['stations']} stations at once, added RTT {2 * fair['delay_ms']:g} ms, "
            f"{fair['duration_s']:g} s.",
            "",
            f"- Total goodput: {fair['total_readings_per_s']} readings/s",
            f"- Per station: min {rates[0]}, median {rates[len(rates) // 2]}, max {rates[-1]} readings/s",
            f"- Jain's fairness index: {fair['jain_index']}",
            f"- ACK latency p50 / p99: {fair['ack_ms']['p50']} / {fair['ack_ms']['p99']} ms",
            f"- Server: write queue depth max {fair['server']['queue_depth_max']:g}, "
            f"mean commit {fair['server']['commit_ms_mean']} ms, "
            f"backpressure replies {fair['backpressure']}",
            "",
            "![Per-station goodput](fairness.svg)",
            "",
        ]

    if results["partition"]:
        out += [
            "## Recovery after a partition",
            "",
            "The real `station_client/client.py` process sending 5 readings every "
            f"{results['partition'][0]['batch_interval_s']:g} s. `blackhole` silently stops "
            "delivery (bytes are held, as with a dead link); `reset` aborts the connections.",
            "",
            "| Mode | Outage (s) | Detected after (s) | First write after heal (s) | "
            "Backlog flushed after heal (s) | Missing seqs | Replayed duplicates | "
            "Samples not taken |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in results["partition"]:
            out.append(
                f"| {row['mode']} | {row['outage_s']:g} | {row['detect_s']} | "
                f"{row['first_write_after_heal_s']} | {row['backlog_drained_s']} | "
                f"{row['missing']} | {row['duplicates_replayed']} | {row['sampling_shortfall']} |"
            )
        out += [
            "",
            "Missing seqs counts gaps in what the station generated (data loss). Samples not "
            "taken counts readings the station never generated because sampling shares a loop "
            "with sending and stalls while a reply is awaited. First-write times come from "
            "polling the database every 50 ms.",
            "",
        ]
    return "\n".join(out)


def git_revision():
    try:
        import subprocess

        return subprocess.run(
            ["git", "describe", "--always", "--dirty"], cwd=ROOT, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


async def run_lab(args):
    duration = 1.0 if args.quick else args.duration
    delays = [0, 25] if args.quick else [0, 5, 10, 25, 50, 100, 200]
    stalls = [0, 0.1] if args.quick else [0, 0.01, 0.02, 0.05, 0.1]
    with tempfile.TemporaryDirectory(prefix="weather-netlab-", ignore_cleanup_errors=True) as tmp:
        server = ServerProcess(Path(tmp))
        await server.start()
        try:
            print("Running network lab...", flush=True)
            rtt_rows, cdfs = await rtt_sweep(server, delays, duration)
            stall_rows = await stall_sweep(server, stalls, 25, 200, duration)
            fair = await fairness(
                server, 5 if args.quick else args.stations, 10, duration if args.quick else 10
            )
            partitions = []
            if not args.quick:
                for mode in ("reset", "blackhole"):
                    partitions.append(await partition_recovery(server, mode, outage_s=5.0))
        finally:
            await server.stop()
    return {
        "meta": {
            "label": args.label,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
            "git": args.git or git_revision(),
            "point_duration_s": duration,
            "batch_size": BATCH_SIZE,
        },
        "rtt_sweep": rtt_rows,
        "ack_latency_cdf": cdfs,
        "stall_sweep": stall_rows,
        "fairness": fair,
        "partition": partitions,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True, help="Run name, used as the output folder")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evidence" / "netlab")
    parser.add_argument("--duration", type=float, default=6.0, help="Seconds per sweep point")
    parser.add_argument("--stations", type=int, default=50, help="Stations in the fairness run")
    parser.add_argument("--git", help="Revision to record (default: git describe)")
    parser.add_argument("--quick", action="store_true", help="Short smoke run, no partitions")
    args = parser.parse_args(argv)

    results = asyncio.run(run_lab(args))
    target = args.out / args.label
    target.mkdir(parents=True, exist_ok=True)
    (target / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (target / "report.md").write_text(render_report(results) + "\n", encoding="utf-8")
    print(f"Wrote {target / 'results.json'} and report.md")


if __name__ == "__main__":
    main()
