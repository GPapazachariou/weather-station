# web/static/app.js — Explanation

**Related docs:** [00_overview.md](00_overview.md), [web_web_py.md](web_web_py.md), [web_index_html.md](web_index_html.md)

## Purpose

This JavaScript module runs in the browser and orchestrates the dashboard's interactivity. It handles API communication with Flask, processes query parameters, renders Chart.js charts, computes rolling averages, and manages UI state transitions. The code is vanilla JavaScript with no frameworks, making dependencies minimal and logic explicit.

## Key Responsibilities

- Initialize dashboard on page load; load available stations from API
- Setup event listeners for user interactions (station/metric/range selection, toggles)
- Fetch stats and readings from Flask API based on user selections
- Compute rolling averages over a configurable window
- Render Chart.js line charts with overlaid raw + rolling average data
- Fallback to HTML table rendering if Chart.js unavailable
- Show/hide UI elements based on data availability and user choices
- Format timestamps for display; handle timezone parsing

## Important Blocks

### Block: Initialization & Event Binding

**Where:** `DOMContentLoaded` event listener at top of file

**What it does:**
1. Log "[Init] Loading dashboard..."
2. Call `loadStations()` to populate station dropdown
3. Setup listeners for change events on select/checkbox elements
4. Call `refreshAll()` for initial data load

```javascript
document.addEventListener('DOMContentLoaded', async () => {
    await loadStations();
    document.getElementById('station-select').addEventListener('change', refreshAll);
    document.getElementById('metric-select').addEventListener('change', refreshAll);
    // ... more listeners
    await refreshAll();
});
```

**Inputs:** DOM ready event; API available

**Outputs:** Event listeners bound; stations loaded; initial refresh triggered

**Why it matters:**
- Ensures all event handlers are set up before user interaction
- Initial refresh populates stats/chart on page load
- Async/await ensures stations load before refreshAll tries to use them

**Failure modes:**
- Network error in loadStations → error logged, empty state shown
- API timeout → error logged, empty state shown

### Block: Load Stations from API

**Where:** `loadStations()` function

**What it does:**
1. Fetch `/api/stations` endpoint
2. Parse JSON response
3. Populate `station-select` dropdown with option per station
4. Select first station by default
5. Log count and first selection
6. On error: show error message

**Inputs:** Flask API `/api/stations`

**Outputs:** Populated select element

**Why it matters:**
- First data loaded on page; drives subsequent API calls
- Empty list triggers empty state (no stations yet)

**Failure modes:**
- Network error → logged, dropdown shows "Error loading"
- No stations in DB → dropdown shows "No stations available"

### Block: Handle Range Selection Change

**Where:** `onRangeChange()` function

**What it does:**
1. Check if `range-select` value is "limit"
2. If yes: show `limit-select` (display inline-block)
3. If no: hide `limit-select` (display none)
4. Call `refreshAll()` to fetch new data

**Inputs:** User selects range option

**Outputs:** Limit select visibility toggled; refresh triggered

**Why it matters:**
- Conditional UI: "limit" mode needs a second dropdown for record count
- Time-range modes (6h, 24h) don't need the limit dropdown
- Simplifies UX by hiding irrelevant controls

**Failure modes:** None (pure UI logic)

### Block: Refresh All (Main Controller)

**Where:** `refreshAll()` async function

**What it does:**
1. Read current selections: station, metric, range, limit
2. Build query params: `?station_id=...&metric=...&range=...` or `...&limit=...`
3. Hide empty state; show stats container
4. Fetch stats and readings in parallel (Promise.all)
5. Log refresh params

```javascript
async function refreshAll() {
    const station = document.getElementById('station-select').value;
    if (!station) {
        showEmptyState();
        return;
    }
    // Build params
    const apiParams = queryParams;  // Built from selections
    // Fetch in parallel
    await Promise.all([
        fetchStats(apiParams),
        fetchReadings(apiParams)
    ]);
}
```

**Inputs:** User selections (via DOM)

**Outputs:** Stats + chart/table updated

**Why it matters:**
- Central coordinator; calls other fetch functions
- Parallel fetching improves responsiveness
- Checks for empty station; bails early

**Failure modes:**
- Either fetch fails → logged, UI shows partial data
- Both fail → empty state shown

### Block: Fetch Stats from API

**Where:** `fetchStats(queryParams)` function

**What it does:**
1. Fetch `/api/stats?...` with params
2. Parse response JSON: latest, latest_t, avg, min, max
3. Update DOM elements with values (or "--" if null)
4. Format timestamp with `formatTime()`
5. Log fetched stats
6. On error: log error, show "--" in all cards

**Inputs:** Query params string; API endpoint

**Outputs:** Updated DOM (stat cards)

**Why it matters:**
- Populates summary stats cards shown above chart
- Safe handling of null values (no data for range)

**Failure modes:**
- API returns error → logged, stats show "--"
- Network timeout → logged, stats show "--"

### Block: Fetch Readings from API

**Where:** `fetchReadings(queryParams)` async function

**What it does:**
1. Fetch `/api/readings?...` with params
2. Parse response JSON: array of {t: timestamp, v: value} objects
3. Check if raw data toggle and rolling average toggle are enabled
4. If enabled, compute rolling average: `rollingAverage(points, windowSize)`
5. Determine if Chart.js is available globally (typeof Chart !== 'undefined')
6. If available: call `renderChart()` with both raw + rolling data
7. If not available: call `renderTable()` as fallback
8. Show/hide chart/table containers accordingly
9. On error: log error, hide containers

**Inputs:** Query params; user toggles (raw/rolling checkboxes, window size)

**Outputs:** Rendered chart or table in DOM

**Why it matters:**
- Orchestrates chart rendering; handles both data series (raw + rolling)
- Graceful degradation: fallback to table if Chart.js fails
- Respects user toggle preferences

**Failure modes:**
- No data points → hides containers
- Chart.js not loaded → table shown
- API error → containers hidden

### Block: Compute Rolling Average

**Where:** `rollingAverage(points, windowSize)` function

**What it does:**
1. Iterate through points array
2. Maintain running sum of last N values
3. For each point, compute average of window
4. Slide window one point at a time
5. Return new array of {t, v} with averaged values

```javascript
function rollingAverage(points, windowSize) {
    let result = [];
    let sum = 0;
    for (let i = 0; i < points.length; i++) {
        sum += points[i].v;
        if (i >= windowSize) {
            sum -= points[i - windowSize].v;  // Slide window
        }
        const count = Math.min(windowSize, i + 1);  // Partial window at start
        result.push({ t: points[i].t, v: sum / count });
    }
    return result;
}
```

**Inputs:** Array of {t, v}; window size (3, 5, 10, 20, or 30 points)

**Outputs:** Smoothed array of {t, v}

**Why it matters:**
- Reduces noise in raw sensor data
- Partial window at start (e.g., window=10 starts with count=1, then 2, ..., 10)
- Running sum for efficiency (O(n) not O(n²))

**Failure modes:** None (pure array processing)

### Block: Render Chart.js Chart

**Where:** `renderChart(metric, points, rawEnabled, rollingEnabled, rollingPoints, windowSize)` function

**What it does:**
1. Destroy previous chart if exists (avoid memory leak)
2. Determine colors/styling based on metric (temperature=red, humidity=blue, windspeed=green)
3. Format timestamps for x-axis labels
4. Build datasets array:
   - If rawEnabled: add raw data series
   - If rollingEnabled: add rolling average series (dashed line)
5. Create Chart.js instance with line chart config
6. Set chart options: responsive, legend, grid styling
7. Show canvas, hide loading message

```javascript
function renderChart(metric, points, rawEnabled, rollingEnabled, rollingPoints, windowSize) {
    const ctx = document.getElementById('data-chart');
    if (chart) chart.destroy();  // Clean up old chart
    
    // Determine colors based on metric
    let borderColor = '#667eea';  // default
    if (metric === 'temperature') borderColor = '#ff6b6b';
    else if (metric === 'humidity') borderColor = '#4dabf7';
    else if (metric === 'windspeed') borderColor = '#51cf66';
    
    const datasets = [];
    if (rawEnabled) {
        datasets.push({
            label: `${metricLabel} (${unit})`,
            data: values,
            borderColor, backgroundColor, // ...
        });
    }
    if (rollingEnabled && rollingPoints.length > 0) {
        datasets.push({
            label: `Rolling Avg (${windowSize})`,
            data: rollingPoints.map(p => p.v),
            borderColor: '#1f2937', borderDash: [6, 4],
            // ...
        });
    }
    
    chart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: { /* ... */ }
    });
}
```

**Inputs:** Metric name; raw points; rolling points; toggles and window size

**Outputs:** Chart.js instance rendered on canvas

**Why it matters:**
- Charts are the primary visual element
- Metric-specific colors improve readability
- Overlaying raw + rolling helps identify noise vs trend

**Failure modes:**
- Chart.js library not loaded → try/catch not explicit, but error would occur before this function
- Canvas element not found → Chart.js throws error (would be caught at higher level)

### Block: Render Fallback Table

**Where:** `renderTable(points)` function

**What it does:**
1. Get `table-body` element
2. Generate HTML rows from points: `<tr><td>timestamp</td><td>value</td></tr>`
3. Set as innerHTML
4. Show table container

**Inputs:** Array of {t, v}

**Outputs:** HTML table populated

**Why it matters:**
- Simple fallback when Chart.js unavailable
- No charting library needed; pure HTML table

**Failure modes:** None (basic HTML generation)

### Block: Timestamp Formatting

**Where:** `formatTime(isoString)` function

**What it does:**
1. Parse ISO 8601 string to Date object
2. Format using toLocaleString with options:
   - Month: short (Jan, Feb, ...)
   - Day: 2-digit
   - Hour/minute: 2-digit
   - 12-hour format (AM/PM)
3. Return formatted string
4. On error: return original string

**Inputs:** ISO 8601 timestamp string

**Outputs:** Formatted human-readable timestamp

**Why it matters:**
- x-axis labels on chart are readable
- Locale-aware formatting (adapts to browser locale)
- Error handling prevents blank labels

**Failure modes:**
- Invalid date string → returned as-is
- Browser timezone handling is automatic

### Block: Show/Hide UI States

**Where:** `showEmptyState()` function

**What it does:**
1. Show `empty-state` div
2. Hide `stats-container`, `chart-container`, `table-container`

**Inputs:** None (DOM manipulation only)

**Outputs:** UI transitioned to empty state

**Why it matters:**
- Guides users when no data available (startup, no stations running)

**Failure modes:** None (pure UI logic)

## Data & State

### Global Variables

```javascript
let chart = null;  // Chart.js instance (destroyed/recreated on refresh)
```

### No Persistent State

- All data is fetched fresh on each refresh
- No local caching; always reflects current DB state
- Stateless design simplifies reasoning

## Control Flow Walkthrough

### User Selects Different Station

```
User clicks station-select, chooses STATION-002

1. change event fires
2. Event listener calls refreshAll()
3. refreshAll():
   - Read new station value
   - Build query params with new station_id
   - Promise.all([fetchStats(), fetchReadings()])
4. fetchStats():
   - Fetch /api/stats?station_id=STATION-002&metric=temperature&range=24h
   - Update stat-latest, stat-avg, stat-min, stat-max
5. fetchReadings():
   - Fetch /api/readings?...
   - Check toggles (raw, rolling)
   - Call renderChart(...) or renderTable(...)
6. Chart re-rendered with new station data
```

### User Toggles Rolling Average

```
User checks toggleRolling checkbox

1. change event fires
2. Event listener calls refreshAll()
3. refreshAll():
   - Same API calls as above
4. fetchReadings():
   - Compute rollingAverage(points, windowSize)
   - Since toggleRolling is now checked, add rolling dataset to chart
5. renderChart() adds dashed rolling average line to existing chart
```

## Interfaces

### API Endpoints Called

```
GET /api/stations
  Response: {"stations": ["STATION-001", "STATION-002", ...]}

GET /api/stats?station_id=STATION-001&metric=temperature&range=24h
  Response: {"latest": 22.5, "latest_t": "2025-01-17T14:30:45Z", "avg": 21.8, "min": 20.0, "max": 25.5}

GET /api/readings?station_id=STATION-001&metric=temperature&range=24h
  Response: {"points": [{"t": "2025-01-17T14:30:45Z", "v": 22.5}, ...]}
```

### DOM Element IDs (Dependencies)

```
station-select, metric-select, range-select, limit-select
toggleRaw, toggleRolling, rollingWindow
stat-latest, stat-avg, stat-min, stat-max, stat-latest-time
empty-state, stats-container, chart-container, chart-loading, data-chart
table-container, table-body
```

## Observability & Debugging

### Browser Console Logs

```javascript
console.log('[Init] Loading dashboard...');
console.log('[Stations] Loaded 3 stations, selected: STATION-001');
console.log('[Refresh] STATION-001 / temperature / 24h');
console.log('[Stats] Latest=22.5, Avg=21.8');
console.log('[Readings] Fetched 50 points');
console.error('[Error] Failed to load stations:', error);
```

### Debugging Tips

1. Open browser DevTools (F12)
2. Check Console tab for logs
3. Check Network tab to see API requests
4. Check Application/Storage for no cookies/local storage (stateless)
5. Inspect element to see current visible/hidden state

### Common Issues

| Issue | Cause | Debug Steps |
|-------|-------|---|
| Empty station dropdown | API not responding or DB empty | Check Network tab; check server logs |
| Chart not rendering | Chart.js CDN failed | Check Network tab; see if fallback table shown |
| Stats show "--" | No data for selected station/range | Try wider time range (24h); ensure data is being ingested |
| UI not responding to clicks | Event listeners not bound | Check console for errors; reload page |

---

**Key Takeaway:** The JavaScript is event-driven, asynchronous, and responsive. It fetches data on demand, renders charts with flexibility (raw + rolling), and gracefully degrades if libraries fail. No frameworks; plain ES6+ with async/await.
