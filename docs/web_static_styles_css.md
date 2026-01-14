# web/static/styles.css Documentation

## Purpose
- Defines the visual styling for the dashboard: layout, cards, controls, chart container, table, and responsive tweaks.
- Consumed by `web/templates/index.html` for the web UI appearance.

## High-level flow
- Pure CSS; applied when the dashboard page loads. No runtime logic.

## Key data structures
- Variables/constants: none (no CSS variables). Uses standard selectors for body, container, header, controls, cards, chart, table, and media queries.
- Shared state: none.

## Classes
- `.container`: centers content with max width.
- `.controls-section`: grid for dropdowns and toggles.
- `.dropdown`: styled selects.
- `.empty-state`: placeholder card when no data.
- `.stats-grid`, `.stat-card`, `.stat-label`, `.stat-value`, `.stat-time`: stat cards styling.
- `.chart-section`, `.loading`: chart wrapper and loading text.
- `.data-table` and child selectors: table styling.
- Responsive: media query at max-width 768px adjusts typography, grid columns, card padding, chart height.

## Functions
- None.

## Error handling & edge cases
- Not applicable (static CSS). Uses responsive adjustments for small screens to prevent overflow.

## Performance & scalability notes
- Lightweight; gradient background on body; box shadows on cards. No heavy animations.

## How to test this file
- Open the dashboard in a browser and visually confirm: gradient background, white cards, grid controls, stat hover lift, chart card sizing, table formatting, and responsive stacking on narrow viewports.