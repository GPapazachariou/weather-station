"""Weather station metric calculations and scientific formulations.

Provides formulas for:
- Magnus-Tetens dew point calculation from paired temperature and relative humidity
- Time-weighted average using trapezoidal integration for irregularly sampled telemetry
- Sample arithmetic mean, min, max, sample count, reading age, and period-over-period comparison
"""

import math
from datetime import datetime, timezone
from typing import Any

# Protocol range limits from protocol.py
TEMPERATURE_MIN = -10.0
TEMPERATURE_MAX = 40.0
HUMIDITY_MIN = 0.0  # exclusive: RH must be > 0 for dew point logarithm
HUMIDITY_MAX = 100.0
WINDSPEED_MIN = 0.0
WINDSPEED_MAX = 50.0

# Magnus-Tetens approximation constants (Alduchov & Eskridge 1996 / Bolton 1980)
# Valid for -45°C <= T <= 60°C and 1% <= RH <= 100%
MAGNUS_A = 17.27
MAGNUS_B = 237.7  # degrees Celsius


def calculate_dew_point(
    temperature: float | None,
    humidity: float | None,
) -> float | None:
    """Calculate dew point in °C from paired temperature (°C) and relative humidity (%).

    Uses the Magnus-Tetens approximation formula:
        gamma(T, RH) = (a * T) / (b + T) + ln(RH / 100.0)
        T_dew = (b * gamma) / (a - gamma)
    where a = 17.27 and b = 237.7 °C.

    Input requirements:
    - temperature must be finite and within [TEMPERATURE_MIN, TEMPERATURE_MAX] (-10°C to 40°C)
    - humidity must be finite, strictly positive (> 0.0%), and <= 100.0%
    Returns None if any input is missing, invalid, or outside valid ranges.
    """
    if temperature is None or humidity is None:
        return None

    if isinstance(temperature, bool) or isinstance(humidity, bool):
        return None

    try:
        t = float(temperature)
        rh = float(humidity)
    except (TypeError, ValueError):
        return None

    if not (math.isfinite(t) and math.isfinite(rh)):
        return None

    # Enforce meteorological / protocol valid ranges
    if not (TEMPERATURE_MIN <= t <= TEMPERATURE_MAX):
        return None
    if not (0.0 < rh <= HUMIDITY_MAX):
        return None

    try:
        alpha = (MAGNUS_A * t) / (MAGNUS_B + t) + math.log(rh / 100.0)
        denom = MAGNUS_A - alpha
        if denom == 0:
            return None
        dew_point = (MAGNUS_B * alpha) / denom
        return round(dew_point, 2)
    except (ValueError, ZeroDivisionError):
        return None


def calculate_sample_mean(values: list[float]) -> float | None:
    """Calculate arithmetic sample mean over a collection of finite numeric values."""
    valid_vals = [v for v in values if v is not None and not isinstance(v, bool) and math.isfinite(v)]
    if not valid_vals:
        return None
    return round(sum(valid_vals) / len(valid_vals), 2)


def calculate_time_weighted_mean(
    series: list[tuple[datetime, float]],
) -> float | None:
    """Calculate time-weighted average for a time-ordered series of (datetime, value) tuples.

    Uses trapezoidal integration:
        Integral = sum_i ((v_i + v_{i+1}) / 2) * (t_{i+1} - t_i)
        TWA = Integral / (t_last - t_first)

    If the series has:
    - 0 points: returns None
    - 1 point: returns the single point's value
    - identical start and end timestamps (zero duration): returns the sample mean
    """
    if not series:
        return None

    clean_series: list[tuple[datetime, float]] = []
    for dt, val in series:
        if val is None or isinstance(val, bool) or not math.isfinite(val):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        clean_series.append((dt, float(val)))

    if not clean_series:
        return None

    if len(clean_series) == 1:
        return round(clean_series[0][1], 2)

    total_seconds = (clean_series[-1][0] - clean_series[0][0]).total_seconds()
    if total_seconds <= 0:
        return calculate_sample_mean([v for _, v in clean_series])

    area = 0.0
    for i in range(len(clean_series) - 1):
        t1, v1 = clean_series[i]
        t2, v2 = clean_series[i + 1]
        dt = (t2 - t1).total_seconds()
        if dt < 0:
            # Series must be in ascending chronological order
            dt = 0.0
        area += ((v1 + v2) / 2.0) * dt

    twa = area / total_seconds
    return round(twa, 2)


def calculate_reading_age(
    latest_timestamp: datetime | str | None,
    reference_time: datetime | None = None,
) -> float | None:
    """Calculate age of the latest reading in elapsed seconds relative to reference_time."""
    if latest_timestamp is None:
        return None

    if isinstance(latest_timestamp, str):
        normalized = (
            latest_timestamp[:-1] + "+00:00"
            if latest_timestamp.endswith(("Z", "z"))
            else latest_timestamp
        )
        try:
            dt = datetime.fromisoformat(normalized)
        except (ValueError, TypeError):
            return None
    elif isinstance(latest_timestamp, datetime):
        dt = latest_timestamp
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    if reference_time is None:
        ref = datetime.now(timezone.utc)
    else:
        ref = reference_time
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

    age_sec = (ref - dt).total_seconds()
    return max(0.0, round(age_sec, 1))


def calculate_period_change(
    current_mean: float | None,
    prev_mean: float | None,
    current_latest: float | None,
    prev_latest: float | None,
    time_range: str | None,
    is_limit: bool = False,
    prev_count: int = 0,
) -> dict[str, Any]:
    """Calculate change against the preceding equal-duration period with clear status and reasons."""
    if is_limit:
        return {
            "available": False,
            "reason": "Comparison unavailable for record count window",
            "mean_change": None,
            "latest_change": None,
            "prev_mean": None,
            "prev_latest": None,
            "prev_count": 0,
        }

    if time_range == "all":
        return {
            "available": False,
            "reason": "Comparison unavailable for all history",
            "mean_change": None,
            "latest_change": None,
            "prev_mean": None,
            "prev_latest": None,
            "prev_count": 0,
        }

    if prev_count == 0 or prev_mean is None or current_mean is None:
        reason = "No readings in preceding period" if prev_count == 0 else "Insufficient data"
        return {
            "available": False,
            "reason": reason,
            "mean_change": None,
            "latest_change": None,
            "prev_mean": prev_mean,
            "prev_latest": prev_latest,
            "prev_count": prev_count,
        }

    mean_change = round(current_mean - prev_mean, 2)
    latest_change = (
        round(current_latest - prev_latest, 2)
        if (current_latest is not None and prev_latest is not None)
        else None
    )

    return {
        "available": True,
        "reason": None,
        "mean_change": mean_change,
        "latest_change": latest_change,
        "prev_mean": prev_mean,
        "prev_latest": prev_latest,
        "prev_count": prev_count,
    }
