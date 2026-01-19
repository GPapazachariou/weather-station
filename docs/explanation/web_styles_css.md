# web/static/styles.css — Explanation

**Related docs:** [00_overview.md](00_overview.md), [web_index_html.md](web_index_html.md)

## Purpose

The CSS stylesheet defines the visual design and layout of the weather dashboard. It uses CSS Grid for responsive layouts, gradients for visual appeal, and semantic spacing. The design is mobile-first and adapts to different screen sizes. All styles use standard CSS3 features; no frameworks like Bootstrap.

## Key Responsibilities

- Define color scheme and typography (font family, sizes)
- Implement responsive grid layouts for controls, stats cards, and charts
- Style form elements (dropdowns, checkboxes)
- Create stat cards with hover effects and shadows
- Style chart container and fallback table
- Implement visual feedback (hover, focus states)
- Ensure mobile responsiveness without media queries (via auto-fit grid)
- Apply consistent spacing, borders, and shadows

## Important Blocks

### Block: Global Reset & Base Styles

**Where:** `* { ... }` and `body { ... }` selectors

**What it does:**
1. Zero out default margins and padding for all elements
2. Set box-sizing to border-box (include padding in width/height)
3. Apply system font stack to body (Apple/Google fonts first, sans-serif fallback)
4. Set background gradient (purple gradient: 135deg, #667eea → #764ba2)
5. Set min-height 100vh to fill viewport
6. Add padding for edge spacing
7. Set default text color (#333, dark gray)

```css
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
    padding: 20px;
    color: #333;
}
```

**Inputs:** HTML structure

**Outputs:** Consistent base styling across all elements

**Why it matters:**
- Consistent spacing (box-sizing border-box avoids margin collapse issues)
- Gradient background makes page visually appealing
- System fonts load fast (no external font files)
- Font stack prioritizes native fonts for good performance

**Failure modes:** None (baseline styles always apply)

### Block: Container & Header

**Where:** `.container { ... }` and `header { ... }` selectors

**What it does:**
1. Container: max-width 1000px, auto margins (centers on wide screens)
2. Header:
   - White background
   - Rounded corners (12px border-radius)
   - Padding 30px for spacing
   - Margin-bottom 30px to separate from controls
   - Box shadow for depth

```css
.container {
    max-width: 1000px;
    margin: 0 auto;
}

header {
    background: white;
    border-radius: 12px;
    padding: 30px;
    margin-bottom: 30px;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
    text-align: center;
}

header h1 {
    font-size: 2.2em;
    color: #667eea;  /* Match gradient primary color */
    margin-bottom: 8px;
}

.subtitle {
    color: #999;
    font-size: 0.95em;
}
```

**Inputs:** HTML structure (header, h1, subtitle)

**Outputs:** Styled header with branding

**Why it matters:**
- Max-width 1000px prevents layout from being too wide on large screens
- Shadow and white background create visual separation from gradient
- Centered text is standard for headers
- Color harmony: h1 color (#667eea) matches gradient

**Failure modes:** None

### Block: Controls Section (CSS Grid)

**Where:** `.controls-section { ... }` and `.dropdown { ... }` selectors

**What it does:**
1. Controls: CSS Grid with auto-fit columns
   - Min column width 200px
   - Flex: 1fr (equal distribution)
   - Gap 15px between columns
2. Dropdown: styled select/input elements
   - Padding 12px 15px for touch-friendly size
   - White background, dark text
   - 2px white border
   - Hover state: subtle shadow
   - Focus state: blue border + glow

```css
.controls-section {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
    margin-bottom: 30px;
}

.dropdown {
    padding: 12px 15px;
    border: 2px solid white;
    border-radius: 8px;
    background: white;
    color: #333;
    cursor: pointer;
    transition: all 0.3s ease;
}

.dropdown:hover {
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.dropdown:focus {
    outline: none;
    border-color: #667eea;
    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.2);
}
```

**Inputs:** HTML form elements

**Outputs:** Responsive, touch-friendly controls

**Why it matters:**
- `auto-fit` layout is mobile-responsive without media queries
- minmax(200px, 1fr) ensures readable controls even on small screens
- Hover/focus states improve accessibility and user feedback
- Padding 12px is large enough for touch targets

**Failure modes:** None

### Block: Empty State

**Where:** `.empty-state { ... }` selector

**What it does:**
1. White background, centered text
2. Large padding (50px) for visual breathing room
3. Gray text color
4. Subtle shadow
5. Larger font for emphasis

```css
.empty-state {
    background: white;
    border-radius: 12px;
    padding: 50px;
    text-align: center;
    color: #999;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
    font-size: 1.1em;
}
```

**Inputs:** HTML div with id="empty-state"

**Outputs:** Styled placeholder message

**Why it matters:** Guides users on startup; friendly messaging

### Block: Stats Grid (CSS Grid)

**Where:** `.stats-grid { ... }` and `.stat-card { ... }` selectors

**What it does:**
1. Grid: auto-fit columns with min 200px width
2. Stat card:
   - White background, rounded corners, padding 20px
   - Shadow for depth
   - Text center alignment
   - Hover effect: scale transform + shadow change
   - Transition ease for smooth animation

```css
.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
    margin-bottom: 30px;
}

.stat-card {
    background: white;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
    text-align: center;
    transition: transform 0.3s ease, box-shadow 0.3s ease;
}

.stat-card:hover {
    transform: translateY(-5px);  /* Lift on hover */
    box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
}

.stat-label {
    color: #999;
    font-size: 0.9em;
}

.stat-value {
    font-size: 2em;
    font-weight: bold;
    color: #667eea;
    margin: 10px 0;
}

.stat-time {
    color: #ccc;
    font-size: 0.8em;
}
```

**Inputs:** HTML stat cards

**Outputs:** Styled metric cards with hover effects

**Why it matters:**
- Grid layout is responsive; same as controls-section
- Hover effect (lift + shadow) provides visual feedback
- Large font for stat values makes metrics readable at a glance
- Color hierarchy: label gray, value blue (primary color)

**Failure modes:** None

### Block: Chart Container

**Where:** `.chart-section { ... }` selector

**What it does:**
1. White background, padding, rounded corners, shadow
2. Chart canvas inside
3. Loading message initially visible, hidden when chart renders

```css
.chart-section {
    background: white;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
    min-height: 400px;  /* Ensure height even before canvas loads */
}

.loading {
    text-align: center;
    color: #999;
    padding: 50px;
}
```

**Inputs:** HTML chart container and canvas

**Outputs:** Styled container for Chart.js

**Why it matters:**
- min-height 400px prevents layout shift when chart loads
- Loading message placeholder improves UX

### Block: Data Table

**Where:** `.data-table { ... }` selector

**What it does:**
1. Full width table
2. Light gray header background
3. Borders for cell separation
4. Padding for readability
5. Alternating row colors (striping) for readability

```css
.data-table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
}

.data-table thead {
    background-color: #f5f5f5;
}

.data-table th,
.data-table td {
    padding: 12px;
    text-align: left;
    border-bottom: 1px solid #ddd;
}

.data-table tbody tr:hover {
    background-color: #f9f9f9;
}
```

**Inputs:** HTML table

**Outputs:** Styled fallback table

**Why it matters:**
- Fallback display if Chart.js unavailable
- Striping and hover effects improve readability

### Block: Toggle Checkbox Styling

**Where:** `.toggle-label { ... }` selector

**What it does:**
1. Inline-block positioning
2. Margin for spacing
3. Checkbox styling (custom if browser supports)
4. Label text inline with checkbox

```css
.toggle-label {
    display: inline-block;
    margin: 0 15px;
    cursor: pointer;
}

.toggle-label input[type="checkbox"] {
    margin-right: 5px;
    cursor: pointer;
}

.toggle-label span {
    user-select: none;  /* Prevent text selection on click */
}
```

**Inputs:** HTML checkbox + label

**Outputs:** Styled toggle controls

**Why it matters:** Makes checkboxes clickable and visually consistent

## Data & State

### Color Palette

```css
Primary: #667eea (purple/blue)
Secondary: #764ba2 (darker purple)
Text Dark: #333
Text Gray: #999
Text Light: #ccc
Background White: white
Background Light: #f5f5f5
Shadows: rgba(0, 0, 0, 0.1) or rgba(0, 0, 0, 0.15)

Metric-specific:
Temperature: #ff6b6b (red)
Humidity: #4dabf7 (blue)
Windspeed: #51cf66 (green)
```

### Spacing Scale

```css
8px (half unit)
15px (gaps)
20px (padding)
30px (margins, padding)
50px (large padding)
```

### Breakpoints

- **Mobile:** <200px (controls not responsive below this)
- **Small:** 200-500px (1-2 columns)
- **Medium:** 500-1000px (2-3 columns)
- **Large:** >1000px (3-4 columns, constrained by container max-width)

## Control Flow Walkthrough

### Page Load (CSS Cascade)

```
1. Browser loads styles.css
2. Applies * reset (margins, padding, box-sizing)
3. Applies body styles (font, gradient background)
4. Applies .container max-width constraint
5. Applies .controls-section CSS Grid → items laid out responsively
6. Applies .dropdown base + hover/focus states
7. Applies .stats-grid CSS Grid
8. Applies .chart-section min-height
9. JavaScript (app.js) later hides/shows elements via style.display
```

### User Hovers on Stat Card

```
User moves mouse over stat card

1. :hover pseudo-class triggers
2. transform: translateY(-5px) — card lifts up 5px
3. box-shadow increased — shadow becomes more prominent
4. transition: 0.3s ease — animation smooth over 300ms
5. User moves away → :hover ends → transitions back to original state
```

## Interfaces

### CSS Classes Used by HTML/JavaScript

| Class | Purpose |
|-------|---------|
| `container` | Max-width wrapper |
| `header` | Page title section |
| `subtitle` | Tagline text |
| `controls-section` | Filter controls grid |
| `dropdown` | Select/input styling |
| `empty-state` | Placeholder message |
| `stats-grid` | Stats cards grid |
| `stat-card` | Individual stat card |
| `stat-label`, `stat-value`, `stat-time` | Stat card internals |
| `chart-section` | Chart container |
| `loading` | Loading placeholder |
| `data-table` | Fallback table |
| `toggle-label` | Checkbox + label |

### Responsive Features

- **CSS Grid auto-fit:** Controls and stats adapt to screen width
- **No media queries:** Simplicity; grid does the heavy lifting
- **Touch-friendly:** Padding 12px for dropdowns (44px minimum touch target met)
- **Viewport meta tag:** Set in HTML (not CSS)

## Observability & Debugging

### Browser DevTools Tips

1. Inspect element: Right-click element → Inspect
2. See applied styles: Styles tab shows CSS rules in cascade order
3. Edit live: Click on CSS values to edit; changes preview instantly
4. Check grid: Enable grid overlay in DevTools (Grid inspector)
5. Check responsive: Device toolbar (Ctrl+Shift+M in Chrome) to test screen sizes

### Common Issues

| Issue | Cause | Fix |
|-------|-------|---|
| Layout broken on mobile | Viewport meta tag missing | (Set in HTML, not CSS) |
| Colors wrong | CSS not loaded | Check Network tab; check link href |
| Hover effects not working | Transition or transform wrong | Check applied styles in DevTools |
| Cards overlapping | Grid gap too small or padding too large | Adjust gap/padding values |

---

**Key Takeaway:** The CSS is clean, responsive, and performance-conscious. It uses CSS Grid for layout (no media queries for basic responsiveness), consistent spacing, and subtle interactions (hover effects). No frameworks; vanilla CSS3 ensures lightweight and teachable code.
