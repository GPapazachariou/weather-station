# web/templates/index.html — Explanation

**Related docs:** [00_overview.md](00_overview.md), [web_app_js.md](web_app_js.md), [web_styles_css.md](web_styles_css.md)

## Purpose

The HTML template defines the structure and layout of the weather dashboard. It uses Jinja2 templating (minimal) and semantic HTML5 elements. The dashboard includes controls for filtering (station, metric, time range), stat cards for summary metrics (latest, avg, min, max), a chart container for Chart.js rendering, and a fallback table for when Chart.js is unavailable. The page is mobile-responsive using CSS Grid.

## Key Responsibilities

- Define semantic HTML structure for dashboard
- Provide form controls (selects, checkboxes) for filtering and display options
- Create containers for stats cards, chart, and fallback table
- Load Chart.js library from CDN
- Link to Flask static assets (styles.css, app.js) using Jinja2 `url_for()`
- Provide accessible markup with IDs and ARIA labels where appropriate

## Important Blocks

### Block: Head & External Resources

**Where:** `<head>` section

**What it does:**
1. Define meta tags (charset UTF-8, viewport for mobile)
2. Set page title
3. Link to CSS stylesheet (via Flask `url_for()`)
4. Load Chart.js library from CDN (v4.4.0)

**Inputs:** None (static markup)

**Outputs:** Loaded stylesheets and JavaScript library

**Why it matters:**
- Meta viewport ensures mobile responsiveness
- External Chart.js library provides charting capability
- Jinja2 `url_for()` generates correct asset URLs regardless of deployment path

**Failure modes:**
- CDN unavailable → Chart.js not loaded → fallback to table (graceful degradation)
- CSS not found → page renders but unstyled

### Block: Header

**Where:** `<header>` element

**What it does:** Display page title and subtitle

```html
<header>
    <h1>🌤️ Weather Station Dashboard</h1>
    <p class="subtitle">Real-time weather monitoring</p>
</header>
```

**Inputs:** None (static)

**Outputs:** Branded header with emoji icon

**Why it matters:** Visual branding and context for users

### Block: Controls Section

**Where:** `<div class="controls-section">` with nested selects and checkboxes

**What it does:**
1. Station dropdown: populated by JavaScript from API
2. Metric dropdown: hardcoded options (Temperature, Humidity, Wind Speed)
3. Range dropdown: time-range options (6h, 24h, or record-limit mode)
4. Limit dropdown: record-count options (10, 50, 100, 500) — hidden by default, shown when range="limit"
5. Checkbox: Toggle raw data visualization
6. Checkbox: Toggle rolling average visualization
7. Rolling window dropdown: select averaging window size (3, 5, 10, 20, 30 points)

**Inputs:** None (user interacts)

**Outputs:** Selected values; JS event handlers attached by app.js

**Why it matters:**
- User-friendly filtering interface
- Dropdowns have semantic HTML (not custom JS-driven)
- Checkboxes allow granular chart customization
- Conditional display (limit-select only visible when needed)

**Failure modes:**
- JavaScript fails → selects are non-functional but visible
- Station dropdown stays "Loading stations..." if API fails

### Block: Empty State

**Where:** `<div id="empty-state">` — hidden by default

**What it does:** Shows placeholder message when no data is available

```html
<div id="empty-state" class="empty-state" style="display: none;">
    <p>📊 No stations or data available yet.</p>
    <p>Start the weather station client to begin collecting data.</p>
</div>
```

**Inputs:** JavaScript shows/hides based on data state

**Outputs:** User-friendly message

**Why it matters:** Guides users on initial startup (before any station clients send data)

### Block: Stats Cards

**Where:** `<div id="stats-container" class="stats-grid">` with 4 stat-card divs

**What it does:** Display summary statistics in card format

```html
<div class="stat-card">
    <div class="stat-label">Latest</div>
    <div class="stat-value" id="stat-latest">--</div>
    <div class="stat-time" id="stat-latest-time"></div>
</div>
<!-- Similar for avg, min, max -->
```

**Inputs:** JavaScript updates `stat-latest`, `stat-avg`, `stat-min`, `stat-max` with values

**Outputs:** Displayed stats (or `--` if no data)

**Why it matters:**
- Quick glance at current metrics
- JavaScript populates via API `/api/stats`
- IDs allow easy JavaScript manipulation

### Block: Chart Container

**Where:** `<div id="chart-container">` with `<canvas id="data-chart">`

**What it does:** Provide container for Chart.js to render line chart

```html
<div id="chart-container" class="chart-section">
    <div class="loading" id="chart-loading">Loading chart...</div>
    <canvas id="data-chart" style="display: none;"></canvas>
</div>
```

**Inputs:** JavaScript fetches data from `/api/readings`, then calls `renderChart()`

**Outputs:** Rendered Chart.js canvas with line chart + rolling average overlay

**Why it matters:**
- Canvas element is native for Chart.js
- Loading placeholder shown initially
- Canvas hidden until data is ready

### Block: Fallback Table

**Where:** `<div id="table-container">` with `<table class="data-table">`

**What it does:** Provide HTML table for displaying readings when Chart.js fails

```html
<table class="data-table">
    <thead>
        <tr><th>Timestamp</th><th>Value</th></tr>
    </thead>
    <tbody id="table-body"></tbody>
</table>
```

**Inputs:** JavaScript populates `table-body` with `<tr>` rows

**Outputs:** Data grid

**Why it matters:**
- Graceful degradation if Chart.js CDN unavailable
- Simple HTML table, no JavaScript dependencies
- Accessible (semantic table markup)

## Data & State

### DOM IDs (Important for JavaScript)

| ID | Purpose | Updated By |
|----|---------|-----------|
| `station-select` | Station picker | app.js (loadStations) |
| `metric-select` | Metric picker | User selection |
| `range-select` | Time range or limit | User selection |
| `limit-select` | Record count (conditional) | User selection |
| `toggleRaw` | Checkbox: show raw data | User toggle |
| `toggleRolling` | Checkbox: show rolling avg | User toggle |
| `rollingWindow` | Rolling average window size | User selection |
| `stat-latest`, `stat-avg`, `stat-min`, `stat-max` | Stat values | app.js (fetchStats) |
| `stat-latest-time` | Timestamp of latest | app.js (fetchStats) |
| `empty-state` | Message when no data | app.js (showEmptyState) |
| `chart-container` | Chart section | app.js (show/hide) |
| `chart-loading` | Loading placeholder | app.js (show/hide) |
| `data-chart` | Canvas for Chart.js | app.js (renderChart) |
| `table-container` | Table section | app.js (show/hide) |
| `table-body` | Table rows | app.js (renderTable) |

### CSS Classes (Styled by styles.css)

```
container, header, subtitle
controls-section, dropdown
empty-state
stats-grid, stat-card, stat-label, stat-value, stat-time
chart-section, loading
data-table
toggle-label
```

## Control Flow Walkthrough

### Page Load Sequence

```html
<!DOCTYPE html>
<html>
  <head>
    <link rel="stylesheet" href="styles.css">
    <script src="chart.js CDN"></script>
  </head>
  <body>
    <!-- Static HTML structure -->
    <div class="container">
      <!-- Header -->
      <!-- Controls (empty station-select initially) -->
      <!-- Stats cards (empty initially) -->
      <!-- Chart/table containers (hidden initially) -->
    </div>
    
    <!-- JavaScript runs after DOM loads -->
    <script src="app.js"></script>
  </body>
</html>
```

### JavaScript Execution (from app.js)

```
DOMContentLoaded event:
  1. loadStations() — fetch /api/stations, populate select
  2. Setup event listeners on selects/checkboxes
  3. refreshAll() — fetch stats and readings
```

### Conditional Display Logic (app.js)

```
onRangeChange():
  if range-select == "limit":
    limit-select.style.display = "inline-block"  // Show
  else:
    limit-select.style.display = "none"          // Hide

refreshAll():
  if no station selected:
    showEmptyState()  // Show empty-state, hide stats/chart
  else:
    Show stats-container
    Fetch stats + readings
    renderChart() or renderTable() depending on Chart.js availability
```

## Interfaces

### Jinja2 Templating

```html
<!-- Only two instances of Jinja2 -->
<link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}">
<script src="..."></script>  <!-- Chart.js CDN, no Jinja2 needed -->
```

- `url_for()` generates correct URL for static files (handles path prefixes)

### External Dependencies

- **Chart.js v4.4.0** from CDN (https://cdn.jsdelivr.net/...)
- **app.js** — User interaction and API communication
- **styles.css** — Layout and styling

### API Integration Points (called by app.js)

```
GET /api/stations              → populate station-select
GET /api/stats?...             → update stat-latest, stat-avg, etc.
GET /api/readings?...          → data for renderChart()
```

## Observability & Debugging

### Browser Console Logs (from app.js)

```
[Init] Loading dashboard...
[Stations] Loaded 3 stations, selected: STATION-001
[Refresh] STATION-001 / temperature / 24h
[Stats] Latest=22.5, Avg=21.8
[Readings] Fetched 50 points
[Error] Failed to load stations: TypeError: fetch is not defined
```

### Visual States

- **Loading:** "Loading stations..." in select, "Loading chart..." in chart-container
- **Empty:** "No stations or data available yet" message, empty stats
- **With data:** Select populated, stats filled, chart rendered
- **Degraded:** Chart failed to load; fallback to table

### Development Notes

- No build process; pure HTML + CSS + vanilla JavaScript
- Mobile-responsive via CSS Grid (no Bootstrap or framework)
- Graceful degradation: if Chart.js CDN fails, table shown instead
- All JavaScript is in app.js; HTML is just structure

---

**Key Takeaway:** The HTML template is semantic, minimal, and focused on structure. It relies on app.js for interactivity and Flask for dynamic asset URLs. The layout supports multiple display modes (chart, table, empty state) for robustness.
