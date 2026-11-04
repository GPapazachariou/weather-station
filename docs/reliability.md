# Delivery correctness and failure recovery

The ingestion path acknowledges a batch only after SQLite commits it. If the
connection fails before the station receives that acknowledgment, the station
keeps the batch and retries with the same `(station_id, seq)` identities. SQLite's
unique constraint makes that replay idempotent: the acknowledgment reports both
new inserts and duplicates.

This is **at-least-once retry with idempotent storage**, subject to the buffer
and durability limits below. It is not an unconditional exactly-once guarantee.

## Failure behavior

| Failure | Result |
| --- | --- |
| Commit succeeds, acknowledgment is lost | Retry the identical batch; existing rows count as duplicates. |
| EOF or send/response timeout during buffered replay | Restore the pending batch once, ahead of newer batches. |
| Queue saturation | Keep the batch, back off with jitter, then retry. |
| Writer acknowledgment timeout or database error | Server marks the response `code: retryable`; station keeps the batch. Legacy `server_error: write_ack_timeout` is also recognized. |
| Insert fails partway through a batch | Roll back the transaction before processing another batch; no failed batch rows leak into a later commit. |
| Station restarts while server is unavailable | Collect samples locally; on the first successful connection, query `latest_seq` and assign buffered samples IDs above the persisted cursor before the first send. |
| Sequence synchronization fails | Reconnect and retry synchronization; never treat an error as an empty database. |
| Buffer fills | Evict oldest batches until the new readings fit. The 1,000-reading limit counts readings, including variable-size batches. Drops are logged. |
| Server receives SIGTERM | Stop accepting new connections, allow in-flight handlers to finish, cancel idle handlers after a bounded grace period, then drain/close the writer. Exit successfully. |
| TCP frame exceeds 64 KiB, including newline | Return `line_too_long` and close the connection. |

Validation failures remain permanent rejections. A station does not retry
invalid sensor data forever. During a backoff period, it generates samples at
complete sampling intervals rather than generating an extra sample for a final
fractional interval.

## Reproduce in Docker

Run from the repository root. No host Python, existing database, running Compose
stack, or GitHub Actions run is needed:

```sh
docker build -f Dockerfile.verify -t weather-portfolio-verify .
docker run --rm weather-portfolio-verify ruff check .
docker run --rm weather-portfolio-verify pytest -q
docker run --rm weather-portfolio-verify python scripts/verify_recovery.py
```

Rebuild the image after edits. Tests and the recovery script use temporary
databases and ephemeral loopback ports inside the container. The script launches
actual server subprocesses and uses the production client's buffering and
replay functions. A TCP proxy reads a successful commit acknowledgment, then
closes the downstream connection without delivering it. The script also sends
SIGTERM and SIGKILL to its own server processes and reopens the same database.

The script fails on a nonzero graceful-shutdown exit, a wrong cursor, any missing
sequence, or any extra row. It does not rely on a fixed delay to infer whether
the intentionally lost acknowledgment followed a commit.

To save a report, use `--output /path/to/report.json` with a writable bind mount.
The checked-in [recovery report](evidence/recovery.json) records the observed
environment and results. Eleven targeted [regression tests](../tests/test_reliability.py)
cover replay, rollback, sequence startup, bounded buffering, transient storage
failure, framing, sampling cadence, in-flight shutdown, and shutdown while an
idle peer stays open. The full suite also
covers queue saturation and duplicate/partially duplicate batches.

## Boundaries and tradeoffs

- **One active producer per station ID.** The sequence synchronization is not a
  distributed lease. Two producers sharing an identity can collide; use distinct
  station IDs. Do not renumber a batch once any send has been attempted.
- **The client buffer is volatile.** A client-process crash loses buffered
  readings. An outage exceeding capacity deliberately loses the oldest samples.
  Neither is described as lossless delivery. Durable local spooling is separate
  work.
- **SQLite uses WAL with `synchronous=NORMAL`.** The process-crash check verifies
  recovery with the operating system still running. It does not prove that every
  acknowledged write survives host power failure or storage failure. Stronger
  power-loss durability would require a different sync policy and measurement.
- **Shutdown is bounded.** With default timeouts, handlers have eight seconds
  before cancellation and the writer has ten seconds to exit. The Compose
  twenty-second stop grace period accommodates these bounds. Requests interrupted
  by the deadline remain ambiguous to the client and must be retried.
- **This is a small deterministic correctness scenario.** Twelve stored readings
  and three replayed readings are evidence of these failure paths, not a
  throughput benchmark or a general capacity claim.

## What changed

The original implementation restored an EOF batch twice, discarded write-timeout
and storage-error batches, skipped initial sequence synchronization when offline
samples existed, and counted buffer batches as records. Failed insert batches
also left an open transaction that could be committed by a later batch. SIGTERM
closed the listener's `serve_forever` future with a cancellation traceback.
Each behavior now has a focused regression check; the end-to-end script verifies
the resulting recovery behavior using real processes and sockets.
