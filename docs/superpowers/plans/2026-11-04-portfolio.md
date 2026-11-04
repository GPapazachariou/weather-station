# Failure recovery implementation plan

> Execute inline using superpowers:executing-plans; review with sol-loop before integration.

**Goal:** Prove and strengthen telemetry delivery through retries, outages and process restarts.
**Architecture:** Keep asyncio TCP and single-writer SQLite. Repair the shared retry and transaction paths, then exercise them using real processes inside Docker.
**Tech stack:** Python 3.11+, pytest, Ruff, Docker. GitHub CI is not the proof mechanism.
**Spec:** Portfolio reliability recommendation and the user's full-send request, subsequently narrowed to merge the first PR and stop.

## Constraints and decisions
- Use the existing isolated feature checkout.
- Preserve protocol callers and dashboard behavior; no new runtime dependencies.
- Use temporary databases, ephemeral ports and only the recovery script's own processes.
- No public deployment, production changes or deleting existing data.
- Implement directly under the user's delegated authority; independent sol-loop review before push.
- Stop after the first PR merges; observability, deployment, benchmarks, demo and broader README work are deferred.

## Review focus
Lost acknowledgments, failed transactions, offline startup sequences, queue saturation, variable-size buffer bounds, in-flight shutdown and overstated reliability claims.

## Task 1: Delivery correctness and recovery
Files: station_client/client.py, server/app.py, tests/test_reliability.py.
- [x] Run baseline tests and Ruff in Docker: 352 passed; lint passed.
- [x] Reproduce EOF replay duplication, dropped timeout/storage-error batches, partial transaction leakage, sampling cadence, sequence sync failure, offline startup collision, framing and record-capacity defects with failing tests.
- [x] Fix shared producer/replay paths, rollback failed transactions, bound by readings, and synchronize before the first send.
- [x] Verify SIGTERM exits normally and commits/acknowledges already queued work. The shutdown test fails on the pre-change Docker image with CancelledError and passes with the fixes.

## Task 2: Reproducible evidence and documentation
Files: Dockerfile.verify, .dockerignore, scripts/verify_recovery.py, docs/reliability.md, docs/evidence/recovery.json, README.md.
- [x] Add a standalone Docker verification image with existing development dependencies.
- [x] Drop a post-commit acknowledgment using a real TCP proxy; replay, buffer during an outage, restart, crash and reconcile every stored sequence.
- [x] Document bounded at-least-once retry, volatile buffers, one producer per station identity, and the power-loss limits of WAL/NORMAL.
- [x] Final Docker image: 362 tests passed, Ruff passed, real-process recovery reconciled 12/12 readings with no missing or duplicate rows. Python 3.12.16 / SQLite 3.46.1; report committed.

## Task 3: Review and integration
- [ ] Obtain independent sol-loop review; fix substantive findings and rerun Docker checks.
- [ ] File, review and merge the first PR under full-send authority, without bypassing required branch protection.
- [ ] Stop after merge; do not start another category.

## Progress ledger
- Initial state: clean feature branch t3/improve-portfolio-readiness at c452473; Docker Desktop Linux engine available.
- Ruling: Keep SQLite and volatile station buffering. This PR makes existing guarantees accurate and testable; durable client spooling remains outside scope.
- Ruling: One PR covers the reliability category. The user's later stop instruction supersedes the earlier all-category roadmap.
- Evidence: 362 tests passed in the final Docker image; ten targeted reliability tests pass. The baseline Docker image reproduces the shutdown CancelledError. No GitHub workflow changes or CI runs were used for this proof.

- Review round 1 (gpt-6.1-sol, high): S1 blocker confirmed. Python 3.12 server context-manager exit waited for idle connections before the cancellation deadline. Added idle-peer regression (observed timeout), removed the context manager and moved wait_closed after handler cleanup. All 363 Docker tests and Ruff pass; recovery script passes again. No findings declined. Review-only usage was not returned.
