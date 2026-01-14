# server/protocol.py Documentation

## Purpose
- Defines protocol constraints and validation logic for batches sent to the TCP server.
- Imported by `server/app.py` to enforce limits and guard database integrity.

## High-level flow
- Acts as a pure validation module: callers provide a batch, `validate_batch()` raises on first error or returns None if valid.
- No runtime loop; used per incoming batch before enqueueing to the writer.

## Key data structures
- Constants: `MAX_LINE_SIZE=65536`, `MAX_BATCH_SIZE=50`; value ranges `TEMPERATURE_MIN/MAX`, `HUMIDITY_MIN/MAX`, `WINDSPEED_MIN/MAX`.
- No global mutable state.

## Classes
- None.

## Functions
### Function: validate_timestamp(timestamp_str)
- Signature: `def validate_timestamp(timestamp_str: str) -> None`
- Parameters: `timestamp_str` expected ISO 8601 string; accepts `Z` or explicit offsets.
- Returns: None on success.
- Side effects: none.
- Errors: ValueError if not a string or not parseable by `datetime.fromisoformat` (after normalizing `Z`).
- Notes: Normalizes trailing `Z` to `+00:00` for compatibility.

### Function: validate_finite_number(value, field_name)
- Signature: `def validate_finite_number(value, field_name: str) -> None`
- Parameters: numeric value; `field_name` for error context.
- Returns: None on success.
- Side effects: none.
- Errors: ValueError if not int/float, if bool, or if not finite (NaN/Inf).

### Function: validate_batch(batch)
- Signature: `def validate_batch(batch: list) -> None`
- Parameters: `batch` expected list of reading dicts.
- Returns: None if valid; raises ValueError on first detected error.
- Side effects: none.
- Errors: ValueError on many conditions: non-list batch, size > `MAX_BATCH_SIZE`, non-dict item, missing required keys, non-string or empty `station_id`, invalid timestamp, non-finite or out-of-range metrics for temperature/humidity/windspeed.
- Important logic: all-or-nothing validation; stops at first failure to keep error messages specific; enforces required fields list `station_id`, `timestamp`, `temperature`, `humidity`, `windspeed`.

## Error handling & edge cases
- Rejects booleans for numeric fields explicitly (bool is int subclass).
- Range enforcement prevents schema-valid but out-of-bounds values.
- Batch size cap plus line size cap (consumed by server) bound memory usage.

## Performance & scalability notes
- Pure in-memory checks; O(n) in batch size (capped at 50), negligible overhead compared to I/O.

## How to test this file
- Call from a REPL:
  ```python
  from server import protocol
  protocol.validate_batch([{ "station_id":"S", "timestamp":"2025-01-01T00:00:00Z", "temperature":20, "humidity":50, "windspeed":5 }])
  ```
- Introduce errors (bad timestamp, NaN, out-of-range) and confirm ValueError messages.