# Web (dashboard)

## What this part is
The web dashboard reads from the SQLite database created by the server and visualizes readings. The main implementation lives under `web/` in `web/web.py` and the static assets in `web/templates` and `web/static`.

## Backend API (Flask)
- `web/web.py` is a Flask app with API endpoints and template rendering.
  - `/` serves `web/templates/index.html`.
  - `/api/stations` returns unique station IDs from the `readings` table.
  - `/api/stats` computes latest/avg/min/max for a station + metric + time range.
  - `/api/readings` returns a time series for a station + metric + time range, ordered oldest to newest (Chart.js friendly).
  - It supports time windows (`6h`, `24h`) and metrics (`temperature`, `humidity`, `windspeed`).
  - `detect_wind_column()` checks if the DB column is `windspeed` or `wind_speed` to stay compatible with older schemas.
  - `parse_timestamp()` normalizes ISO 8601 timestamps (and falls back to epoch seconds if needed).
  - Configuration is controlled by `WEATHER_DB_PATH`, `WEB_HOST`, and `WEB_PORT`.

## Frontend behavior
- `web/templates/index.html` defines the layout: station selector, metric selector, time range selector, toggles for raw data and rolling average, and the chart/table containers.
- `web/static/app.js` handles:
  - Loading stations (`loadStations()`), then refreshing stats + readings on selection changes.
  - Fetching stats from `/api/stats` and updating the stat cards.
  - Fetching readings from `/api/readings` and rendering them with Chart.js (or falling back to a table if Chart.js is unavailable).
  - Computing rolling averages (`rollingAverage()`) and optionally overlaying them on the chart.
- `web/static/styles.css` provides the layout and styling (cards, gradients, responsive layout, chart/table styles).

## Alternate/legacy web entrypoint
There is also a root-level `web.py` with a simpler API (`/health`, `/api/metrics`, `/api/readings`, `/api/latest`). It expects `templates/` and `static/` beside the file, which are not present in the root. In practice, the `web/web.py` app aligns with the actual assets under `web/`.

## Related paths
- `web/web.py`
- `web/templates/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `web.py`
