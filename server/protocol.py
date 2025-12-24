"""
Weather Station Protocol
Defines protocol constants and validation for weather data batches.
"""

import math
from datetime import datetime


# Protocol limits
MAX_LINE_SIZE = 65536  # Maximum size of a single line (bytes)
MAX_BATCH_SIZE = 50    # Maximum number of readings per batch

# Valid ranges for weather measurements
TEMPERATURE_MIN = -10
TEMPERATURE_MAX = 40
HUMIDITY_MIN = 0
HUMIDITY_MAX = 100
WINDSPEED_MIN = 0
WINDSPEED_MAX = 50


def validate_timestamp(timestamp_str: str) -> None:
    """
    Validate timestamp is in valid ISO 8601 format.
    Accepts 'Z' suffix or explicit timezone offsets.
    Raises ValueError if invalid.
    """
    if not isinstance(timestamp_str, str):
        raise ValueError("timestamp must be a string")
    
    # Normalize 'Z' suffix to '+00:00' for fromisoformat compatibility
    normalized = timestamp_str.replace('Z', '+00:00') if timestamp_str.endswith('Z') else timestamp_str
    
    try:
        datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        raise ValueError(f"invalid_timestamp: {timestamp_str}")


def validate_finite_number(value, field_name: str) -> None:
    """
    Validate that value is a finite number (int or float, not NaN or Infinity).
    Raises ValueError if not finite.
    """
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    
    if isinstance(value, bool):  # bool is subclass of int, reject explicitly
        raise ValueError(f"{field_name} must be a number")
    
    if not math.isfinite(value):
        raise ValueError(f"non_finite_number: {field_name}={value}")


def validate_batch(batch: list) -> None:
    """
    Validate a batch of weather readings.
    Raise ValueError with a clear error message on the FIRST error encountered.
    Return None if valid.
    
    Validation is all-or-nothing: either the entire batch is valid,
    or an error is raised and nothing should be inserted.
    
    Checks:
    - Batch is a list within size limit
    - Each record has required fields with correct types
    - Timestamps are valid ISO 8601
    - Numbers (temperature, humidity, windspeed) are finite
    - Values are within valid ranges
    """
    # Rule 1: batch must be a list
    if not isinstance(batch, list):
        raise ValueError("batch is not a list")
    
    # Rule 2: batch length must not exceed MAX_BATCH_SIZE
    if len(batch) > MAX_BATCH_SIZE:
        raise ValueError("batch size exceeds limit")
    
    # Required fields for each reading
    required_fields = ["station_id", "timestamp", "temperature", "humidity", "windspeed"]
    
    # Validate each item in the batch
    for index, item in enumerate(batch):
        # Rule 3: each item must be a dict
        if not isinstance(item, dict):
            raise ValueError(f"item at index {index} is not a dict")
        
        # Rule 4: check for required keys
        for field in required_fields:
            if field not in item:
                raise ValueError(f"missing {field} at index {index}")
        
        # Rule 5: station_id must be non-empty string
        if not isinstance(item["station_id"], str):
            raise ValueError(f"invalid station_id at index {index} (expected string)")
        if not item["station_id"]:
            raise ValueError(f"empty station_id at index {index}")
        
        # Rule 6: timestamp must be valid ISO 8601 format
        try:
            validate_timestamp(item["timestamp"])
        except ValueError as e:
            raise ValueError(f"invalid_timestamp at index {index}: {str(e)}")
        
        # Rule 7: temperature must be finite number within range
        try:
            validate_finite_number(item["temperature"], "temperature")
        except ValueError as e:
            raise ValueError(f"invalid temperature at index {index}: {str(e)}")
        
        if not (TEMPERATURE_MIN <= item["temperature"] <= TEMPERATURE_MAX):
            raise ValueError(
                f"invalid temperature at index {index} "
                f"(expected {TEMPERATURE_MIN}..{TEMPERATURE_MAX})"
            )
        
        # Rule 8: humidity must be finite number within range
        try:
            validate_finite_number(item["humidity"], "humidity")
        except ValueError as e:
            raise ValueError(f"invalid humidity at index {index}: {str(e)}")
        
        if not (HUMIDITY_MIN <= item["humidity"] <= HUMIDITY_MAX):
            raise ValueError(
                f"invalid humidity at index {index} "
                f"(expected {HUMIDITY_MIN}..{HUMIDITY_MAX})"
            )
        
        # Rule 9: windspeed must be finite number within range
        try:
            validate_finite_number(item["windspeed"], "windspeed")
        except ValueError as e:
            raise ValueError(f"invalid windspeed at index {index}: {str(e)}")
        
        if not (WINDSPEED_MIN <= item["windspeed"] <= WINDSPEED_MAX):
            raise ValueError(
                f"invalid windspeed at index {index} "
                f"(expected {WINDSPEED_MIN}..{WINDSPEED_MAX})"
            )
    
    # All validation passed
    return None
