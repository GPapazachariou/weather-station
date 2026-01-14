# web/templates/index.html Documentation

## Purpose
- Dashboard HTML shell for the weather UI. Hosts controls, stat cards, chart canvas, table fallback, and includes CSS/JS assets.
- Served by Flask route `/` in `web/web.py`; uses `static/styles.css` and `static/app.js` plus Chart.js CDN.

## High-level flow
- Page loads: links stylesheet and Chart.js, builds static DOM structure (controls, empty state, stats, chart, table). `app.js` attaches listeners and populates data after DOMContentLoaded.
- User interacts via selects and toggles; rendering handled in JS.

## Key data structures
- DOM IDs: `station-select`, `metric-select`, `range-select`, `toggleRaw`, `toggleRolling`, `rollingWindow`, `empty-state`, `stats-container`, `stat-*`, `chart-container`, `chart-loading`, `data-chart`, `table-container`, `table-body`.
- Controls: three dropdowns (station, metric, range), two checkboxes (raw, rolling), rolling window select.
- Layout wrappers: `.container`, `.controls-section`, `.stats-grid`, `.chart-section`, `.data-table`.

## Classes
- Semantic groupings: header with title/subtitle; controls section; empty state block; stat cards grid; chart section; table section.
- No custom classes beyond layout/styling; behavior driven by IDs in JS.

## Functions
- None defined here; behavior delegated to `static/app.js`.

## Error handling & edge cases
- Empty state div initially hidden; shown by JS when no stations.
- Chart canvas initially hidden; JS toggles display based on data availability.

## Performance & scalability notes
- Includes Chart.js from CDN; only one canvas element; table fallback for environments without Chart.js.

## How to test this file
- Serve via Flask (`python web/web.py`) and open http://127.0.0.1:8000.
- Verify controls render, title/subtitle visible, stat cards placeholders `--`, chart loading text, and empty state toggles when no data.