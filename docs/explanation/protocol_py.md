# server/protocol.py — Explanation

**Related docs:** [00_overview.md](00_overview.md), [app_py.md](app_py.md)

## Purpose

This module defines all protocol-level validation for weather station data. It enforces invariants on batch structure, field presence, data types, value ranges, and message size. All validation is centralized here so the server (`app.py`) calls one function that either succeeds or raises a clear error on the first validation failure. This all-or-nothing approach ensures no partial data is inserted into the database.

## Key Responsibilities

- Define protocol constants: `MAX_LINE_SIZE`, `MAX_BATCH_SIZE`, and valid ranges for temperature, humidity, windspeed
- Implement `validate_batch()` to check entire batch structure and contents
- Implement `validate_timestamp()` to parse and verify ISO 8601 format
- Implement `validate_finite_number()` to reject NaN, Infinity, and non-numeric types
- Provide clear, actionable error messages that guide the client on what failed
- Support all-or-nothing semantics: first error in batch raises immediately, nothing is inserted

## Important Blocks

### Block: Protocol Constants

**Where:** Top-level module constants (MAX_LINE_SIZE, MAX_BATCH_SIZE, ranges)

**What it does:** Defines hard limits and acceptable ranges:
- `MAX_LINE_SIZE = 65536` — Max bytes per NDJSON line
- `MAX_BATCH_SIZE = 50` — Max readings per batch
- `TEMPERATURE_MIN = -10`, `TEMPERATURE_MAX = 40` — °C range
- `HUMIDITY_MIN = 0`, `HUMIDITY_MAX = 100` — % range
- `WINDSPEED_MIN = 0`, `WINDSPEED_MAX = 50` — m/s range

**Inputs:** None (constants)

**Outputs:** Tunable thresholds used by all validation functions

**Why it matters:** Centralizes limits so they're easy to adjust and reason about. Server imports these to configure asyncio StreamReader limit.

**Failure modes:** Constants are just numbers; no runtime validation of constants themselves

### Block: Timestamp Validation

**Where:** `validate_timestamp(timestamp_str: str)`

**What it does:**
1. Check input is a string (not None, not int)
2. Normalize 'Z' suffix to '+00:00' for Python compatibility
3. Parse with `datetime.fromisoformat()`
4. Raise `ValueError` if format is invalid

**Inputs:** Any value (could be string, None, int, etc.)

**Outputs:** None (side effect: raises ValueError or succeeds)

**Why it matters:** 
- Accepts ISO 8601 with Z or explicit offset (e.g., "2025-01-17T14:30:45Z", "2025-01-17T14:30:45+00:00")
- Rejects ambiguous formats early
- Clear error message helps client know to check timestamp format

**Failure modes:** 
- Non-string input → "timestamp must be a string"
- Invalid format → "invalid_timestamp: <value>"

### Block: Finite Number Validation

**Where:** `validate_finite_number(value, field_name: str)`

**What it does:**
1. Check value is int or float (not string, not None, not bool)
2. Reject bool explicitly (bool is subclass of int in Python)
3. Call `math.isfinite()` to reject NaN, Infinity, -Infinity
4. Raise `ValueError` if any check fails

**Inputs:** Any value; field name for error message

**Outputs:** None (side effect: raises ValueError or succeeds)

**Why it matters:** 
- Ensures only valid finite numbers are stored (no IEEE special values that might confuse web layer or databases)
- Catches common mistakes like sending `float('inf')` or JSON-parsing libraries that allow NaN
- Clear field name in error helps client debug which metric is wrong

**Failure modes:**
- Non-numeric type → "<field_name> must be a number"
- Boolean → "<field_name> must be a number" (explicit reject)
- NaN or Infinity → "non_finite_number: <field_name>=nan"

### Block: Batch Structure Validation

**Where:** `validate_batch(batch: list)` — first three checks

**What it does:**
1. Check batch is a list (not dict, not scalar)
2. Check length ≤ MAX_BATCH_SIZE (50)
3. For each item, check is a dict

**Inputs:** Any parsed JSON value

**Outputs:** None (side effect: raises ValueError or succeeds)

**Why it matters:** 
- Early structural checks catch malformed requests before field-level processing
- All-or-nothing: fail on first error, don't accumulate error list
- Batch must be a list so server can iterate and insert multiple records

**Failure modes:**
- Batch is dict → "batch is not a list"
- Batch is too large → "batch size exceeds limit"
- Item is not dict → "item at index <i> is not a dict"

### Block: Per-Record Field Validation

**Where:** In `validate_batch()`, the nested loop over each reading

**What it does:**
1. For each reading (enumerate by index):
   - Check all required fields present: station_id, timestamp, temperature, humidity, windspeed
   - Validate station_id: is string, non-empty
   - Validate timestamp: call `validate_timestamp()`
   - Validate temperature: call `validate_finite_number()`, check range [-10, 40]
   - Validate humidity: call `validate_finite_number()`, check range [0, 100]
   - Validate windspeed: call `validate_finite_number()`, check range [0, 50]

**Inputs:** Reading dict (already validated as dict)

**Outputs:** None (side effect: raises ValueError on first error)

**Why it matters:** 
- Comprehensive field-level validation ensures data integrity before insert
- Error message includes reading index so client knows which reading to fix
- Range checks prevent out-of-range values (e.g., -50°C when min is -10°C)

**Failure modes:**
- Missing field → "missing <field> at index <i>"
- Wrong type → "invalid <field> at index <i> (expected <type>)"
- Out of range → "invalid <field> at index <i> (expected <min>..<max>)"

### Block: Error Reporting Strategy

**Where:** Throughout `validate_batch()` error branches

**What it does:** Raises `ValueError` with a structured message that includes:
- Field name or batch structure element
- Index of failing reading (if applicable)
- Expected constraint (e.g., range, type, format)
- Actual value (sometimes, for clarity)

**Inputs:** Validation failure point

**Outputs:** `ValueError` with human-readable message

**Why it matters:** 
- Client can parse error string to understand what to fix
- Index-based errors help locate exact reading in batch
- Range information helps client know valid values

**Failure modes:** Error string is clear and deterministic; no random or platform-specific messages

## Data & State

### Module-Level Constants

```python
MAX_LINE_SIZE = 65536           # Max NDJSON line size (enforced by server)
MAX_BATCH_SIZE = 50             # Max readings per batch

TEMPERATURE_MIN = -10           # °C lower bound
TEMPERATURE_MAX = 40            # °C upper bound
HUMIDITY_MIN = 0                # % lower bound
HUMIDITY_MAX = 100              # % upper bound
WINDSPEED_MIN = 0               # m/s lower bound
WINDSPEED_MAX = 50              # m/s upper bound
```

### Required Fields Per Reading

```python
required_fields = [
    "station_id",       # String, non-empty
    "timestamp",        # ISO 8601 string
    "temperature",      # Finite number in range
    "humidity",         # Finite number in range
    "windspeed",        # Finite number in range
]
```

### No Global State

- Protocol module is stateless; all functions are pure (only side effect: raising errors)
- No caches, buffers, or connections; suitable for concurrent calls

## Control Flow Walkthrough

### Full Validation Path

```
validate_batch(batch):
  ├─ assert isinstance(batch, list)                       // Check is list
  ├─ assert len(batch) <= MAX_BATCH_SIZE                  // Check size
  └─ for index, item in enumerate(batch):
       ├─ assert isinstance(item, dict)                    // Check is dict
       ├─ for field in required_fields:
       │   └─ assert field in item                         // Check present
       ├─ validate_station_id(item["station_id"])         // Must be non-empty string
       ├─ validate_timestamp(item["timestamp"])           // Must be valid ISO 8601
       ├─ validate_finite_number(item["temperature"], "temperature")  // Must be finite
       ├─ assert -10 <= item["temperature"] <= 40        // Check range
       ├─ validate_finite_number(item["humidity"], "humidity")       // Must be finite
       ├─ assert 0 <= item["humidity"] <= 100            // Check range
       ├─ validate_finite_number(item["windspeed"], "windspeed")    // Must be finite
       └─ assert 0 <= item["windspeed"] <= 50            // Check range
  └─ return None  // Success, no exception raised
```

### Error on First Failure

```
Example: Batch with 3 readings, 2nd reading has invalid temperature

validate_batch([
  {"station_id": "S1", ...},
  {"station_id": "S2", ..., "temperature": 99},  // Out of range (> 40)
  {"station_id": "S3", ...}
])

Execution:
  ├─ Check batch is list ✓
  ├─ Check batch size ✓
  ├─ Validate reading 0 ✓
  ├─ Validate reading 1:
  │   ├─ Check required fields ✓
  │   ├─ Validate station_id ✓
  │   ├─ Validate timestamp ✓
  │   ├─ Validate temperature: 99 > 40
  │   └─ Raise ValueError("invalid temperature at index 1 (expected -10..40)")
  │       ↑ STOP HERE, do not validate reading 2
  └─ Function exits with exception

Result: Entire batch rejected, nothing inserted into DB
```

## Interfaces

### Imports & Dependencies

```python
import math                # For math.isfinite()
from datetime import datetime  # For datetime.fromisoformat()
```

### Functions Called by Server

```python
# Primary entry point
validate_batch(batch: list) -> None  # Raises ValueError on first error

# May be called by validate_batch, public but also used internally
validate_timestamp(timestamp_str: str) -> None
validate_finite_number(value, field_name: str) -> None
```

### Request Format (Input to validate_batch)

Batch is a JSON array of reading objects:
```json
[
  {
    "station_id": "STATION-001",
    "timestamp": "2025-01-17T14:30:45Z",
    "temperature": 22.5,
    "humidity": 58.3,
    "windspeed": 4.2
  }
]
```

### Error Format (Output from validate_batch)

Raises `ValueError` with string message:
```python
ValueError("invalid_temperature at index 0 (expected -10..40)")
ValueError("missing station_id at index 2")
ValueError("batch size exceeds limit")
```

Server catches and formats as JSON response:
```json
{"status": "error", "reason": "invalid_temperature at index 0 (expected -10..40)"}
```

## Observability & Debugging

### No Logging in Protocol Module

- Protocol module is pure; no print/log statements
- Errors are communicated via exceptions
- Server logs the exception and sends to client

### Error Message Anatomy

Example: `"invalid_timestamp at index 1: invalid_timestamp: 2025-13-45T00:00:00Z"`

- First part: context ("at index 1")
- Second part: error category ("invalid_timestamp")
- Third part: details (the actual bad value)

### Testing Validation

**Valid batch:**
```python
validate_batch([
  {
    "station_id": "STATION-001",
    "timestamp": "2025-01-17T14:30:45Z",
    "temperature": 22.5,
    "humidity": 58.3,
    "windspeed": 4.2
  }
])
# Returns None (no exception)
```

**Invalid batch (bad range):**
```python
validate_batch([{"station_id": "S1", "timestamp": "2025-01-17T14:30:45Z", "temperature": 99, "humidity": 50, "windspeed": 10}])
# Raises: ValueError("invalid temperature at index 0 (expected -10..40)")
```

**Invalid batch (NaN):**
```python
# JSON library would parse this, but protocol rejects:
validate_batch([{"station_id": "S1", "timestamp": "2025-01-17T14:30:45Z", "temperature": float('nan'), "humidity": 50, "windspeed": 10}])
# Raises: ValueError("non_finite_number: temperature=nan")
```

**Invalid batch (missing field):**
```python
validate_batch([{"station_id": "S1", "timestamp": "2025-01-17T14:30:45Z", "temperature": 22.5}])  # Missing humidity, windspeed
# Raises: ValueError("missing humidity at index 0")
```

---

**Key Takeaway:** Protocol validation is all-or-nothing, comprehensive, and generates actionable error messages. The centralized module makes it easy to audit what data is allowed and ensures consistency across all code paths that process batches.
