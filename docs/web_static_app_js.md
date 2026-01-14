# web/static/app.js Documentation

## Purpose
- Front-end logic for the dashboard: fetches station list, stats, and readings from Flask APIs and renders chart/table UI interactions.
- Used by the dashboard page `web/templates/index.html`; drives Chart.js rendering and UI state.

## High-level flow
- On DOMContentLoaded: loads stations, wires change handlers, then triggers an initial refresh.
- User interactions (dropdowns/toggles) call `refreshAll()` which fetches stats and readings in parallel, then renders chart or table.
- Handles empty or no-data states by hiding visuals and showing a loading/empty indicator.

## Key data structures
- Global `chart` variable holding the current Chart.js instance.
- DOM elements: station/metric/range selectors, raw/rolling toggles, rolling window select, stat cards, chart canvas, table.
- API payloads: `/api/stations` → `{stations: [...]}`; `/api/stats` → stats object; `/api/readings` → `{points: [{t, v}]}`.

## Classes
- None.

## Functions
### Function: loadStations()
- Signature: `async function loadStations()`
- Parameters: none.
- Returns: Promise<void>.
- Side effects: fetches station list, populates dropdown, auto-selects first station, logs status, shows empty state on failure/empty.
- Errors: caught; dropdown shows error text and empty state when failing.

### Function: refreshAll()
- Signature: `async function refreshAll()`
- Parameters: none.
- Returns: Promise<void>.
- Side effects: reads selection; toggles empty vs stats; triggers `fetchStats` and `fetchReadings` concurrently.
- Errors: relies on callee handling; no throws expected.

### Function: fetchStats(station, metric, range)
- Signature: `async function fetchStats(station, metric, range)`
- Parameters: station string, metric string, range string.
- Returns: Promise<void>.
- Side effects: updates stat cards (latest, time, avg, min, max); logs results; placeholders on failure.
- Errors: caught; sets stat values to `--` on error.

### Function: fetchReadings(station, metric, range)
- Signature: `async function fetchReadings(station, metric, range)`
- Parameters: station string, metric string, range string.
- Returns: Promise<void>.
- Side effects: fetches points, computes rolling average if enabled, renders chart or table, toggles visibility/loading.
- Errors: caught; hides visuals on failure.

### Function: renderChart(metric, points, rawEnabled, rollingEnabled, rollingPoints, windowSize)
- Signature: `function renderChart(metric, points, rawEnabled, rollingEnabled, rollingPoints, windowSize)`
- Parameters: metric name; point arrays; booleans toggling datasets; rolling window size.
- Returns: void.
- Side effects: destroys prior `chart`, creates new Chart.js line chart with metric-specific colors/units; updates DOM display flags.

### Function: rollingAverage(points, windowSize)
- Signature: `function rollingAverage(points, windowSize)`
- Parameters: list of `{t,v}` points; integer window size.
- Returns: list of averaged points aligned to input timestamps.
- Side effects: none.
- Notes: Uses running sum for O(n) complexity; returns input when window <= 1.

### Function: renderTable(points)
- Signature: `function renderTable(points)`
- Parameters: list of `{t,v}` points.
- Returns: void.
- Side effects: writes table body HTML rows.

### Function: showEmptyState()
- Signature: `function showEmptyState()`
- Parameters: none.
- Returns: void.
- Side effects: shows empty-state panel; hides stats/chart/table containers.

### Function: formatTime(isoString)
- Signature: `function formatTime(isoString)`
- Parameters: ISO timestamp string.
- Returns: localized string (month/day time) or original on parse failure.
- Side effects: none.

## Error handling & edge cases
- No stations returned: dropdown shows placeholder; empty state displayed.
- Zero points: chart/table hidden, loading message shown.
- Chart.js unavailable: falls back to table rendering when points exist.
- All fetches wrapped in try/catch with console logging; UI degrades gracefully without throwing.

## Performance & scalability notes
- Client-side rendering; Chart.js may slow on very large datasets—use shorter ranges or disable raw data.
- Rolling average O(n); acceptable for moderate point counts typical of dashboard views.

## How to test this file
- Run the web app and open the dashboard; watch browser console for `[Init]`, `[Stations]`, `[Stats]`, `[Readings]` logs.
- Disconnect server or clear DB to verify empty-state behavior and error logging.
- Toggle raw/rolling switches and rolling window to confirm chart updates without errors.