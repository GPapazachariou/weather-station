#!/usr/bin/env python3
"""Update docker-compose.stress.yml to add PYTHONUNBUFFERED=1 to all station services."""

import re

# Read the file
with open('docker-compose.stress.yml', 'r') as f:
    content = f.read()

# Replace pattern: add PYTHONUNBUFFERED=1 after WEATHER_STATION_MAX_RETRIES=3
pattern = r'(      - WEATHER_STATION_MAX_RETRIES=3)\n(    depends_on:)'
replacement = r'\1\n      - PYTHONUNBUFFERED=1\n\2'
updated_content = re.sub(pattern, replacement, content)

# Write the file back
with open('docker-compose.stress.yml', 'w') as f:
    f.write(updated_content)

print('Successfully updated docker-compose.stress.yml with PYTHONUNBUFFERED=1')
