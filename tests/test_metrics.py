"""Independently calculated fixtures and unit tests for weather station metrics.

Tests cover:
- Magnus-Tetens dew point calculation against meteorological ground truth
- Boundary conditions, input validation, and rejection of out-of-range values
- Time-weighted mean vs sample mean across irregular sampling intervals
- UTC boundary transitions (midnight, month, leap year)
- Reading age calculation
- Period-over-period change comparison and unavailable edge cases
- Handling of missing data, null pairs, and empty periods
"""

import math
from datetime import datetime, timedelta, timezone

import pytest
from metrics import (
    calculate_dew_point,
    calculate_period_change,
    calculate_reading_age,
    calculate_sample_mean,
    calculate_time_weighted_mean,
)


class TestDewPointCalculation:
    """Tests for calculate_dew_point() against meteorological formulas and ground truth."""

    def test_dew_point_equals_temperature_at_100_percent_humidity(self):
        """At 100% relative humidity, dew point is mathematically and physically identical to temperature."""
        for temp in [-10.0, -5.0, 0.0, 10.0, 20.0, 30.0, 40.0]:
            dp = calculate_dew_point(temp, 100.0)
            assert dp is not None
            assert math.isclose(dp, temp, abs_tol=0.02), f"Expected {temp} at 100% RH, got {dp}"

    @pytest.mark.parametrize(
        "temp,humidity,expected_dew_point",
        [
            # Reference meteorological benchmarks calculated using Magnus-Tetens (a=17.27, b=237.7)
            (20.0, 50.0, 9.25),
            (30.0, 70.0, 23.91),
            (0.0, 50.0, -9.17),
            (-10.0, 80.0, -12.78),
            (25.0, 20.0, 0.47),
            (35.0, 90.0, 33.09),
            (15.0, 60.0, 7.30),
        ],
    )
    def test_dew_point_standard_benchmarks(self, temp, humidity, expected_dew_point):
        """Verify dew point against independent benchmark calculations."""
        dp = calculate_dew_point(temp, humidity)
        assert dp is not None
        assert math.isclose(dp, expected_dew_point, abs_tol=0.03)

    @pytest.mark.parametrize(
        "invalid_temp,valid_humidity",
        [
            (-10.01, 50.0),
            (-50.0, 50.0),
            (40.01, 50.0),
            (60.0, 50.0),
            (float("nan"), 50.0),
            (float("inf"), 50.0),
            (None, 50.0),
            ("invalid", 50.0),
            (True, 50.0),  # Booleans rejected
        ],
    )
    def test_dew_point_rejects_out_of_range_or_invalid_temperature(self, invalid_temp, valid_humidity):
        """Temperatures outside [-10°C, 40°C] or non-numeric/non-finite must return None."""
        assert calculate_dew_point(invalid_temp, valid_humidity) is None

    @pytest.mark.parametrize(
        "valid_temp,invalid_humidity",
        [
            (20.0, 0.0),  # RH must be > 0 (log(0) undefined)
            (20.0, -0.1),
            (20.0, -15.0),
            (20.0, 100.01),
            (20.0, 150.0),
            (20.0, float("nan")),
            (20.0, float("inf")),
            (20.0, None),
            (20.0, "high"),
            (20.0, False),  # Booleans rejected
        ],
    )
    def test_dew_point_rejects_out_of_range_or_invalid_humidity(self, valid_temp, invalid_humidity):
        """Humidity values <= 0, > 100, or non-finite/non-numeric must return None."""
        assert calculate_dew_point(valid_temp, invalid_humidity) is None

    def test_dew_point_handles_paired_missing_telemetry(self):
        """If either temperature or humidity is missing in a pair, result is None."""
        assert calculate_dew_point(None, 60.0) is None
        assert calculate_dew_point(22.0, None) is None
        assert calculate_dew_point(None, None) is None


class TestTimeWeightedMean:
    """Tests for calculate_time_weighted_mean() with irregular sampling and boundaries."""

    def test_uniform_sampling_matches_arithmetic_mean(self):
        """When samples are uniformly spaced, time-weighted average closely tracks arithmetic mean."""
        base = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        series = [(base + timedelta(minutes=i * 10), 10.0 + i * 2.0) for i in range(5)]
        # Values: 10, 12, 14, 16, 18 -> Arithmetic mean = 14.0
        # Linear progression with uniform intervals: trapezoidal mean = 14.0
        twa = calculate_time_weighted_mean(series)
        s_mean = calculate_sample_mean([v for _, v in series])
        assert twa == 14.0
        assert s_mean == 14.0

    def test_irregular_sampling_demonstrates_weighting_difference(self):
        """
        Demonstrate difference between sample mean and time-weighted mean on irregular telemetry:
        Station stayed at 10.0°C for 100 seconds, then recorded 30.0°C over 10 seconds.
        Sample mean: (10 + 10 + 30) / 3 = 16.67
        Time-weighted mean: (100*10 + 10*(10+30)/2) / 110 = 1200 / 110 = 10.91
        """
        base = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        series = [
            (base, 10.0),
            (base + timedelta(seconds=100), 10.0),
            (base + timedelta(seconds=110), 30.0),
        ]
        twa = calculate_time_weighted_mean(series)
        s_mean = calculate_sample_mean([v for _, v in series])

        assert s_mean == 16.67
        assert twa == 10.91
        assert twa != s_mean

    def test_single_point_returns_point_value(self):
        """Single telemetry reading returns that value as time-weighted average."""
        dt = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        assert calculate_time_weighted_mean([(dt, 23.45)]) == 23.45

    def test_zero_duration_returns_sample_mean(self):
        """Multiple readings at exact same timestamp return arithmetic mean."""
        dt = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        series = [(dt, 20.0), (dt, 24.0)]
        assert calculate_time_weighted_mean(series) == 22.0

    def test_empty_or_all_invalid_series_returns_none(self):
        """Empty series or series with non-finite values returns None."""
        assert calculate_time_weighted_mean([]) is None
        dt = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        assert calculate_time_weighted_mean([(dt, float("nan"))]) is None
        assert calculate_time_weighted_mean([(dt, None)]) is None

    def test_utc_boundary_crossing(self):
        """Time-weighted integration works seamlessly across UTC midnight and month boundaries."""
        t1 = datetime(2026, 10, 31, 23, 50, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 11, 1, 0, 10, 0, tzinfo=timezone.utc)  # 20 min later (1200s)
        series = [(t1, 15.0), (t2, 25.0)]
        # Trapezoid: (15 + 25)/2 * 1200 / 1200 = 20.0
        assert calculate_time_weighted_mean(series) == 20.0


class TestReadingAge:
    """Tests for calculate_reading_age()."""

    def test_reading_age_from_datetime(self):
        """Calculate age from datetime object relative to fixed reference time."""
        ref = datetime(2026, 11, 4, 12, 0, 30, tzinfo=timezone.utc)
        reading_dt = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        assert calculate_reading_age(reading_dt, ref) == 30.0

    def test_reading_age_from_iso_string_with_z(self):
        """Parse ISO string with Z suffix and compute elapsed age."""
        ref = datetime(2026, 11, 4, 12, 5, 0, tzinfo=timezone.utc)
        ts_str = "2026-11-04T12:00:00Z"
        assert calculate_reading_age(ts_str, ref) == 300.0

    def test_reading_age_clamped_to_zero_for_slight_clock_skew(self):
        """Future timestamp due to minor clock skew clamps age to 0.0."""
        ref = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        future_ts = datetime(2026, 11, 4, 12, 0, 5, tzinfo=timezone.utc)
        assert calculate_reading_age(future_ts, ref) == 0.0

    def test_reading_age_invalid_or_none(self):
        """None or malformed timestamp returns None."""
        assert calculate_reading_age(None) is None
        assert calculate_reading_age("not-a-timestamp") is None


class TestPeriodChange:
    """Tests for calculate_period_change() and unavailable comparison handling."""

    def test_period_change_available_positive_and_negative(self):
        """Compute positive and negative changes against preceding period."""
        res_up = calculate_period_change(
            current_mean=25.0,
            prev_mean=22.5,
            current_latest=26.0,
            prev_latest=23.0,
            time_range="24h",
            prev_count=50,
        )
        assert res_up["available"] is True
        assert res_up["mean_change"] == 2.50
        assert res_up["latest_change"] == 3.00
        assert res_up["prev_mean"] == 22.5
        assert res_up["prev_count"] == 50

        res_down = calculate_period_change(
            current_mean=18.25,
            prev_mean=21.50,
            current_latest=17.0,
            prev_latest=22.0,
            time_range="6h",
            prev_count=30,
        )
        assert res_down["available"] is True
        assert res_down["mean_change"] == -3.25
        assert res_down["latest_change"] == -5.00

    def test_period_change_unavailable_for_limit(self):
        """Comparisons are explicitly unavailable for record-count windows (limit mode)."""
        res = calculate_period_change(
            current_mean=20.0,
            prev_mean=18.0,
            current_latest=21.0,
            prev_latest=19.0,
            time_range="24h",
            is_limit=True,
            prev_count=50,
        )
        assert res["available"] is False
        assert "record count window" in res["reason"]
        assert res["mean_change"] is None

    def test_period_change_unavailable_for_all_history(self):
        """Comparisons are explicitly unavailable for 'all' history range."""
        res = calculate_period_change(
            current_mean=20.0,
            prev_mean=18.0,
            current_latest=21.0,
            prev_latest=19.0,
            time_range="all",
            prev_count=50,
        )
        assert res["available"] is False
        assert "all history" in res["reason"]
        assert res["mean_change"] is None

    def test_period_change_unavailable_when_no_prior_readings(self):
        """When preceding equal-duration period contains 0 samples, comparison is unavailable."""
        res = calculate_period_change(
            current_mean=20.0,
            prev_mean=None,
            current_latest=20.0,
            prev_latest=None,
            time_range="24h",
            prev_count=0,
        )
        assert res["available"] is False
        assert "No readings in preceding period" in res["reason"]
        assert res["mean_change"] is None
