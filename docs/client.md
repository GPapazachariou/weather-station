# Client (station client)

## What this part is
The station client generates synthetic weather readings and sends them to the server over TCP. It is implemented in `station_client/client.py`.

## Data generation
- `generate_reading()` builds one reading with `station_id`, `timestamp`, `temperature`, `humidity`, and `windspeed`.
- `value_for()` produces a deterministic per-station mean (via `stable_hash_int()` and `mean_for()`) and adds random Gaussian noise. The result is clamped to valid ranges.
- `generate_batch()` returns a list of readings for the configured batch size.

This design ensures each station has a stable baseline while still producing realistic variation.

## Sending loop and resilience
- `send_batches()` is an infinite loop that maintains a connection, sends batches, and retries with exponential backoff when disconnected.
- When disconnected, it continues to generate batches and buffers them in memory (`deque`) up to `DEFAULT_MAX_BUFFER_RECORDS`.
- On reconnect, it flushes buffered batches first before resuming normal sending.
- Each batch is sent as a single JSON line ending with `\n` and waits for the server response before proceeding.
- Errors during send/flush (timeouts, broken pipes, connection refused) trigger reconnection and backoff.

## Configuration and CLI
`load_config()` merges defaults, environment variables, and CLI args. CLI arguments override env vars.
Key options:
- `WEATHER_STATION_HOST`, `WEATHER_STATION_PORT`
- `WEATHER_STATION_ID`
- `WEATHER_STATION_BATCH_MIN`, `WEATHER_STATION_BATCH_MAX`
- `WEATHER_STATION_BATCH_INTERVAL`
- `WEATHER_STATION_TIMEOUT`, `WEATHER_STATION_CONNECT_TIMEOUT`

These map to `--host`, `--port`, `--station-id`, `--batch-min`, `--batch-max`, `--batch-interval`, `--timeout`, and `--connect-timeout` in the CLI.

## Related paths
- `station_client/client.py`
- `station_client/Dockerfile`
- `station_client/requirements.txt`
- `consumer_client/consumer.py` (currently empty placeholder)
