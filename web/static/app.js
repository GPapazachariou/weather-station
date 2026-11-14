let chart = null; // Legacy single-chart handle for backward compatibility
const charts = {
    temperature: null,
    humidity: null,
    windspeed: null,
    dew_point: null,
    single: null
};

// Full raw telemetry arrays
let currentRawPoints = [];
let multiSeries = {
    temperature: [],
    humidity: [],
    windspeed: [],
    dew_point: []
};

let eventSource = null;
let reconnectTimer = null;
let reconnectDelay = 1000;
const INITIAL_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_DELAY = 16000;
let lastUpdatedTime = null;
let lastUpdatedTimer = null;
let lastReceivedId = null;
let isRefreshing = false; // In-flight request guard
let pendingRefresh = false; // Coalesce rapid changes during active refresh
let statsRefreshTimer = null; // Debounce/coalesce stats refresh on live updates (S5)

// Race condition & delayed response prevention
let currentFetchId = 0;
let activeAbortController = null;

// User preferences storage key and display mode ('chart' | 'table')
const PREFS_STORAGE_KEY = 'weather_station_preferences';
const THEME_STORAGE_KEY = 'weather_station_theme';
let currentDisplayFormat = 'chart';
let currentTheme = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
const STALE_THRESHOLD_SECONDS = 180; // 3 minutes without new telemetry

const METRIC_CONFIGS = {
    temperature: { id: 'temperature', title: 'Temperature', unit: '°C', canvasId: 'chart-temperature', prefix: 'temp' },
    humidity: { id: 'humidity', title: 'Humidity', unit: '%', canvasId: 'chart-humidity', prefix: 'hum' },
    windspeed: { id: 'windspeed', title: 'Wind speed', unit: 'm/s', canvasId: 'chart-windspeed', prefix: 'wind' },
    dew_point: { id: 'dew_point', title: 'Dew point', unit: '°C', canvasId: 'chart-dewpoint', prefix: 'dew', isCalculated: true }
};

// Aliases for compatibility
METRIC_CONFIGS.dewpoint = METRIC_CONFIGS.dew_point;
METRIC_CONFIGS.wind_speed = METRIC_CONFIGS.windspeed;

const CHART_FONT = '"Epilogue", system-ui, sans-serif';

/**
 * Read a colour token from styles.css for the current theme
 */
function cssToken(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

/**
 * Return theme-aware chart tokens (all defined once in styles.css)
 */
function getChartThemeTokens() {
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    return {
        isDark,
        legendColor: cssToken('--muted', '#8891a1'),
        tickColor: cssToken('--muted', '#8891a1'),
        gridY: cssToken('--chart-grid', 'rgba(233, 237, 243, 0.1)'),
        gridX: 'transparent',
        pointBorder: cssToken('--surface', '#131824'),
        tooltipBg: cssToken('--tooltip-bg', '#e9edf3'),
        tooltipInk: cssToken('--tooltip-ink', '#0a0d14'),
        tooltipBorder: 'transparent',
        average: cssToken('--chart-average', '#e9edf3')
    };
}

/**
 * Resolve the series colour of a measurement for the current theme
 */
function getThemedMetricColors(config) {
    const color = cssToken(`--series-${config.id || 'temperature'}`, '#3d9be6');
    return {
        color,
        rawBorder: color,
        rawBg: 'transparent',
        rollingBorder: cssToken('--chart-average', '#e9edf3')
    };
}

/**
 * One decimal place and the unit, or a dash when there is no value
 */
function formatValue(value, unit) {
    if (value === null || value === undefined || isNaN(value)) return '--';
    const text = Number(value).toFixed(1);
    return unit ? `${text} ${unit}` : text;
}

/**
 * Write a large reading: the number, then its unit in a smaller size
 */
function setReading(el, value, unit) {
    if (!el) return;
    if (value === null || value === undefined || isNaN(value)) {
        el.textContent = '--';
        return;
    }
    const num = document.createElement('span');
    num.className = 'num';
    num.textContent = Number(value).toFixed(1);
    const unitEl = document.createElement('span');
    unitEl.className = 'unit';
    unitEl.textContent = unit;
    el.replaceChildren(num, document.createTextNode('\u00a0'), unitEl);
}

/**
 * Keep the time-range buttons in step with the select that holds the value
 */
function syncRangeButtons() {
    const value = document.getElementById('range-select')?.value;
    document.querySelectorAll('.range-buttons [data-range]').forEach(btn => {
        const on = btn.dataset.range === value;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}

/**
 * Apply theme ('light' | 'dark') to document, update toggle button UI, and refresh chart styling
 */
function applyTheme(theme, persist = true) {
    currentTheme = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', currentTheme);

    const toggleBtn = document.getElementById('btn-theme-toggle');
    const toggleLabel = document.getElementById('theme-toggle-label');
    if (toggleBtn) {
        const isDark = currentTheme === 'dark';
        toggleBtn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
        toggleBtn.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
    }
    if (toggleLabel) {
        toggleLabel.textContent = currentTheme === 'dark' ? 'Light' : 'Dark';
    }

    if (persist) {
        try {
            localStorage.setItem(THEME_STORAGE_KEY, currentTheme);
        } catch (e) {}
        savePreferences();
    }

    updateActiveChartsTheme();
}

/**
 * Toggle between light and dark theme
 */
function toggleTheme() {
    const next = currentTheme === 'dark' ? 'light' : 'dark';
    applyTheme(next, true);
}

/**
 * Update existing Chart.js instances in-place when theme switches
 */
function updateActiveChartsTheme() {
    const tokens = getChartThemeTokens();
    Object.entries(charts).forEach(([key, inst]) => {
        if (!inst) return;
        const metricKey = key === 'single' ? (document.getElementById('metric-select')?.value || 'temperature') : key;
        const config = METRIC_CONFIGS[metricKey] || METRIC_CONFIGS.temperature;
        const mColors = getThemedMetricColors(config);

        if (inst.options?.plugins?.legend?.labels) {
            inst.options.plugins.legend.labels.color = tokens.legendColor;
        }
        if (inst.options?.plugins?.tooltip) {
            inst.options.plugins.tooltip.backgroundColor = tokens.tooltipBg;
            inst.options.plugins.tooltip.titleColor = tokens.tooltipInk;
            inst.options.plugins.tooltip.bodyColor = tokens.tooltipInk;
        }
        if (inst.options?.scales?.y) {
            if (inst.options.scales.y.grid) inst.options.scales.y.grid.color = tokens.gridY;
            if (inst.options.scales.y.title) inst.options.scales.y.title.color = tokens.tickColor;
            if (inst.options.scales.y.ticks) inst.options.scales.y.ticks.color = tokens.tickColor;
        }
        if (inst.options?.scales?.x) {
            if (inst.options.scales.x.grid) inst.options.scales.x.grid.color = tokens.gridX;
            if (inst.options.scales.x.ticks) inst.options.scales.x.ticks.color = tokens.tickColor;
        }

        if (Array.isArray(inst.data?.datasets)) {
            inst.data.datasets.forEach(ds => {
                if (ds.id === 'raw') {
                    ds.borderColor = mColors.rawBorder;
                    ds.backgroundColor = mColors.rawBg;
                    ds.pointBackgroundColor = mColors.color;
                    ds.pointBorderColor = tokens.pointBorder;
                } else if (ds.id === 'rolling') {
                    ds.borderColor = mColors.rollingBorder;
                }
            });
        }
        inst.update('none');
    });
}

/**
 * Save user control selections to localStorage
 */
function savePreferences() {
    try {
        const station = document.getElementById('station-select')?.value || '';
        const metric = document.getElementById('metric-select')?.value || 'all';
        const range = document.getElementById('range-select')?.value || '24h';
        const limit = document.getElementById('limit-select')?.value || '50';
        const toggleRaw = document.getElementById('toggleRaw')?.checked ?? true;
        const toggleRolling = document.getElementById('toggleRolling')?.checked ?? true;
        const rollingWindow = document.getElementById('rollingWindow')?.value || '10';

        const prefs = {
            station,
            metric,
            range,
            limit,
            toggleRaw,
            toggleRolling,
            rollingWindow,
            displayFormat: currentDisplayFormat,
            theme: currentTheme
        };
        localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(prefs));
    } catch (e) {
        console.warn('[Prefs] Could not save preferences to localStorage:', e);
    }
}

/**
 * Load saved user control selections from localStorage
 */
function loadPreferences() {
    try {
        const raw = localStorage.getItem(PREFS_STORAGE_KEY);
        if (!raw) return null;
        return JSON.parse(raw);
    } catch (e) {
        console.warn('[Prefs] Could not load preferences from localStorage:', e);
        return null;
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    console.log('[Init] Loading dashboard...');

    startLastUpdatedTimer();

    // Restore saved preferences before initial queries (defaulting to dark theme)
    const savedPrefs = loadPreferences();
    const savedTheme = (savedPrefs && savedPrefs.theme) || localStorage.getItem(THEME_STORAGE_KEY);
    if (savedTheme === 'dark' || savedTheme === 'light') {
        applyTheme(savedTheme, false);
    } else {
        applyTheme('dark', false);
    }

    if (savedPrefs) {
        if (savedPrefs.metric) {
            const metricEl = document.getElementById('metric-select');
            if (metricEl) metricEl.value = savedPrefs.metric;
        }
        if (savedPrefs.range) {
            const rangeEl = document.getElementById('range-select');
            if (rangeEl) {
                rangeEl.value = savedPrefs.range;
                if (savedPrefs.range === 'limit') {
                    const limitSelect = document.getElementById('limit-select');
                    const limitField = document.getElementById('limit-field');
                    if (limitSelect) limitSelect.style.display = 'block';
                    if (limitField) limitField.style.display = 'block';
                }
            }
        }
        if (savedPrefs.limit) {
            const limitEl = document.getElementById('limit-select');
            if (limitEl) limitEl.value = savedPrefs.limit;
        }
        if (typeof savedPrefs.toggleRaw === 'boolean') {
            const rawEl = document.getElementById('toggleRaw');
            if (rawEl) rawEl.checked = savedPrefs.toggleRaw;
        }
        if (typeof savedPrefs.toggleRolling === 'boolean') {
            const rollingEl = document.getElementById('toggleRolling');
            if (rollingEl) rollingEl.checked = savedPrefs.toggleRolling;
        }
        if (savedPrefs.rollingWindow) {
            const windowEl = document.getElementById('rollingWindow');
            if (windowEl) windowEl.value = savedPrefs.rollingWindow;
        }
        if (savedPrefs.displayFormat === 'table' || savedPrefs.displayFormat === 'chart') {
            currentDisplayFormat = savedPrefs.displayFormat;
            updateDisplayFormatUI(currentDisplayFormat);
        }
    }

    // Load stations first (respecting saved station preference)
    await loadStations();

    // Setup event listeners
    document.getElementById('station-select').addEventListener('change', onStationChange);
    document.getElementById('metric-select').addEventListener('change', onMetricChange);
    document.getElementById('range-select').addEventListener('change', onRangeChange);
    document.querySelectorAll('.range-buttons [data-range]').forEach(btn => {
        btn.addEventListener('click', () => window.setRangeAndRefresh(btn.dataset.range));
    });
    syncRangeButtons();
    document.getElementById('limit-select').addEventListener('change', () => { savePreferences(); refreshAll(); });
    document.getElementById('toggleRaw').addEventListener('change', onToggleChange);
    document.getElementById('toggleRolling').addEventListener('change', onToggleChange);
    document.getElementById('rollingWindow').addEventListener('change', onToggleChange);

    window.addEventListener('resize', () => {
        const isCompact = window.innerWidth <= 480;
        Object.entries(charts).forEach(([key, inst]) => {
            if (inst && typeof inst.resize === 'function') {
                if (inst.options?.scales?.x?.ticks) {
                    inst.options.scales.x.ticks.maxTicksLimit = isCompact ? 4 : (key === 'single' ? 10 : 8);
                }
                inst.resize();
            }
        });
    });

    // Initial data fetch and start live stream
    await refreshAll();
    initLiveStream();

    // Secondary safety poll: if stream is ever disconnected or in error, poll every 5s
    setInterval(async () => {
        if (!eventSource || eventSource.readyState !== EventSource.OPEN) {
            if (!isRefreshing) {
                console.log('[Poll] SSE stream not open, performing fallback refresh...');
                await refreshAll();
            }
        }
    }, 5000);
});

/**
 * Handle station dropdown change: restart SSE stream and reload data
 */
async function onStationChange() {
    savePreferences();
    lastReceivedId = null;
    closeLiveStream();
    await refreshAll();
    initLiveStream();
}

/**
 * Handle metric change: re-fetch data for new metric
 */
async function onMetricChange() {
    savePreferences();
    await refreshAll();
}

/**
 * Handle toggle or rolling window changes without full network fetch if data exists
 */
function onToggleChange() {
    savePreferences();
    const metricMode = document.getElementById('metric-select').value;
    const range = document.getElementById('range-select').value;
    const rawEnabled = document.getElementById('toggleRaw').checked;
    const rollingEnabled = document.getElementById('toggleRolling').checked;
    const windowSize = parseInt(document.getElementById('rollingWindow').value, 10) || 10;

    if (currentDisplayFormat === 'table') {
        renderAccessibleTable();
    } else if (metricMode === 'all') {
        renderAlignedPanels(multiSeries, rawEnabled, rollingEnabled, windowSize, range);
    } else {
        renderSingleChart(metricMode, currentRawPoints, rawEnabled, rollingEnabled, windowSize, range);
    }
}

/**
 * Set visual connection status badge and reconnect button
 */
function setLiveStatus(status) {
    const badge = document.getElementById('live-indicator');
    const text = document.getElementById('live-status-text');
    const btnReconnect = document.getElementById('btn-reconnect');
    if (!badge || !text) return;

    badge.className = `live-badge ${status}`;
    if (status === 'connected') {
        text.textContent = 'Live';
        if (btnReconnect) btnReconnect.style.display = 'none';
    } else if (status === 'reconnecting') {
        text.textContent = 'Reconnecting';
        if (btnReconnect) btnReconnect.style.display = 'inline-flex';
    } else if (status === 'connecting') {
        text.textContent = 'Connecting';
        if (btnReconnect) btnReconnect.style.display = 'none';
    } else {
        text.textContent = 'Offline';
        if (btnReconnect) btnReconnect.style.display = 'inline-flex';
    }
}

/**
 * User-initiated manual reconnect recovery action
 */
window.manualReconnect = function() {
    console.log('[SSE] Manual reconnect initiated by user');
    reconnectDelay = INITIAL_RECONNECT_DELAY;
    closeLiveStream();
    initLiveStream();
};

/**
 * Start 1-second interval timer for "Last updated: X seconds ago"
 */
function startLastUpdatedTimer() {
    if (lastUpdatedTimer) clearInterval(lastUpdatedTimer);
    lastUpdatedTimer = setInterval(() => {
        updateLastUpdatedDisplay();
        document.querySelectorAll('[data-reading-timestamp]').forEach(el => {
            const timestamp = el.dataset.readingTimestamp;
            const age = timestamp ? Math.max(0, (Date.now() - new Date(timestamp).getTime()) / 1000) : null;
            el.textContent = formatReadingAge(age);
        });
    }, 1000);
}

/**
 * Update the text showing when data was last updated and freshness state
 */
function updateLastUpdatedDisplay() {
    const span = document.getElementById('last-updated');
    const badge = document.getElementById('freshness-badge');
    const text = document.getElementById('freshness-text');
    if (!span) return;

    if (!lastUpdatedTime) {
        span.textContent = 'No readings yet';
        if (badge && text) {
            badge.className = 'freshness-badge waiting';
            text.textContent = 'Waiting for readings';
        }
        return;
    }

    const diffSec = Math.max(0, Math.floor((Date.now() - lastUpdatedTime.getTime()) / 1000));
    let ago;
    if (diffSec < 2) {
        ago = 'just now';
    } else if (diffSec < 60) {
        ago = `${diffSec} s ago`;
    } else if (diffSec < 3600) {
        ago = `${Math.floor(diffSec / 60)} min ago`;
    } else {
        ago = `${Math.floor(diffSec / 3600)} h ago`;
    }
    span.textContent = `Updated ${ago}`;

    if (badge && text) {
        if (diffSec >= STALE_THRESHOLD_SECONDS) {
            badge.className = 'freshness-badge stale';
            const mins = Math.floor(diffSec / 60);
            const gap = mins >= 60 ? `${Math.floor(mins / 60)} h` : `${mins} min`;
            text.textContent = `No new readings for ${gap}`;
        } else {
            badge.className = 'freshness-badge fresh';
            text.textContent = 'Up to date';
        }
        badge.removeAttribute('title');
    }
}

/**
 * Connect to SSE live stream with exponential backoff on disconnect
 */
function initLiveStream() {
    const station = document.getElementById('station-select').value;
    if (!station) return;

    closeLiveStream();
    setLiveStatus('connecting');

    try {
        let streamUrl = `/api/stream?station_id=${encodeURIComponent(station)}`;
        if (lastReceivedId !== null) {
            streamUrl += `&since_id=${encodeURIComponent(lastReceivedId)}`;
        }
        eventSource = new EventSource(streamUrl);

        eventSource.onopen = () => {
            console.log(`[SSE] Connected to live stream for station ${station}`);
            setLiveStatus('connected');
            reconnectDelay = INITIAL_RECONNECT_DELAY;
        };

        eventSource.onmessage = (event) => {
            try {
                if (event.lastEventId) {
                    const parsedId = parseInt(event.lastEventId, 10);
                    if (!isNaN(parsedId)) lastReceivedId = parsedId;
                }
                const reading = JSON.parse(event.data);
                if (reading && reading.id) {
                    lastReceivedId = Math.max(lastReceivedId || 0, reading.id);
                }
                handleLiveReading(reading);
            } catch (err) {
                console.error('[SSE] Failed to parse event data:', err);
            }
        };

        eventSource.onerror = (err) => {
            console.warn('[SSE] Connection error/closed:', err);
            setLiveStatus('reconnecting');
            closeLiveStream();
            scheduleReconnect();
        };
    } catch (err) {
        console.error('[SSE] Failed to initialize EventSource:', err);
        setLiveStatus('reconnecting');
        scheduleReconnect();
    }
}

/**
 * Close existing SSE live stream connection
 */
function closeLiveStream() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
}

/**
 * Schedule reconnect with exponential backoff
 */
function scheduleReconnect() {
    if (reconnectTimer) return;
    console.log(`[SSE] Scheduling reconnect in ${reconnectDelay}ms...`);
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        initLiveStream();
    }, reconnectDelay);
    reconnectDelay = Math.min(MAX_RECONNECT_DELAY, Math.round(reconnectDelay * 1.5));
}

/**
 * Schedule a debounced/coalesced fetch of full-dataset statistics on live updates (S1, S5)
 */
function scheduleCoalescedStatsRefresh() {
    if (statsRefreshTimer) return;
    statsRefreshTimer = setTimeout(async () => {
        statsRefreshTimer = null;
        try {
            const station = document.getElementById('station-select')?.value;
            const metric = document.getElementById('metric-select')?.value;
            const range = document.getElementById('range-select')?.value;
            if (!station || !metric || !range) return;

            let q = `?station_id=${encodeURIComponent(station)}&metric=${encodeURIComponent(metric)}`;
            if (range === 'limit') {
                const limit = document.getElementById('limit-select')?.value || 50;
                q += `&limit=${encodeURIComponent(limit)}`;
            } else {
                q += `&range=${encodeURIComponent(range)}`;
            }
            const fetchId = currentFetchId;
            await fetchStats(q, null, fetchId, station, metric, range);
        } catch (e) {
            console.warn('[StatsRefresh] Coalesced refresh error:', e);
        }
    }, 1000);
}

/**
 * Handle new live reading received from SSE stream (S2, S3, S5)
 */
function handleLiveReading(reading) {
    const station = document.getElementById('station-select')?.value;
    if (!reading || reading.station_id !== station) {
        return;
    }

    if (reading.timestamp) {
        const d = new Date(reading.timestamp);
        if (!isNaN(d.getTime())) {
            lastUpdatedTime = d;
        } else {
            lastUpdatedTime = new Date();
        }
    } else {
        lastUpdatedTime = new Date();
    }
    updateLastUpdatedDisplay();

    // Restoring content visibility when recovering from an empty window (S2)
    hideStationEmptyState();

    const metricMode = document.getElementById('metric-select')?.value;
    updateContainerVisibility(metricMode, currentDisplayFormat);

    const rangeSelect = document.getElementById('range-select')?.value;
    const limit = rangeSelect === 'limit' ? (parseInt(document.getElementById('limit-select')?.value, 10) || 50) : null;
    const windowSize = parseInt(document.getElementById('rollingWindow')?.value, 10) || 10;
    const rawEnabled = document.getElementById('toggleRaw')?.checked;
    const rollingEnabled = document.getElementById('toggleRolling')?.checked;

    if (metricMode === 'all') {
        const metrics = ['temperature', 'humidity', 'windspeed', 'dew_point'];
        for (const m of metrics) {
            let val = reading[m];
            if (val === undefined && m === 'windspeed') val = reading.wind_speed;
            if (val === undefined || val === null) continue;

            const numVal = typeof val === 'number' ? val : parseFloat(val);

            // Update the latest reading at once
            const liveConfig = METRIC_CONFIGS[m];
            setReading(document.getElementById(`stat-${liveConfig.prefix}-latest`), numVal, liveConfig.unit);

            multiSeries[m].push({ t: reading.timestamp, v: numVal });
            if (limit && multiSeries[m].length > limit) {
                while (multiSeries[m].length > limit) multiSeries[m].shift();
            }
        }

        // Recompute global bounds and update panels or table (S2)
        if (currentDisplayFormat === 'table') {
            renderAccessibleTable();
        } else {
            refreshAllPanelsData(multiSeries, rawEnabled, rollingEnabled, windowSize, rangeSelect);
        }
        scheduleCoalescedStatsRefresh(); // S1, S5
    } else {
        // Single metric mode
        let val = reading[metricMode];
        if (val === undefined && (metricMode === 'windspeed' || metricMode === 'wind_speed')) {
            val = reading.windspeed !== undefined ? reading.windspeed : reading.wind_speed;
        }
        if (val === undefined || val === null) return;

        const numVal = typeof val === 'number' ? val : parseFloat(val);
        setReading(document.getElementById('stat-latest'), numVal, (METRIC_CONFIGS[metricMode] || {}).unit || '');
        document.getElementById('stat-latest-time').textContent = formatFullTimestamp(reading.timestamp);

        currentRawPoints.push({ t: reading.timestamp, v: numVal });
        if (limit && currentRawPoints.length > limit) {
            while (currentRawPoints.length > limit) currentRawPoints.shift();
        }

        if (currentDisplayFormat === 'table') {
            renderAccessibleTable();
        } else {
            renderSingleChart(metricMode, currentRawPoints, rawEnabled, rollingEnabled, windowSize, rangeSelect);
        }
        scheduleCoalescedStatsRefresh(); // S1, S5
    }
}

/**
 * Load available stations from API
 */
async function loadStations() {
    try {
        const response = await fetch('/api/stations');
        const data = await response.json();
        const stations = data.stations || [];

        const select = document.getElementById('station-select');
        const savedPrefs = loadPreferences();
        const preferredStation = (savedPrefs && savedPrefs.station) ? savedPrefs.station : '';
        const currentVal = select ? select.value : '';

        if (stations.length === 0) {
            console.warn('[Stations] No stations found');
            select.innerHTML = '<option value="">No stations</option>';
            showEmptyState();
            return;
        }

        select.replaceChildren(...stations.map(s => new Option(s, s)));

        // Priority 1: Current selection if valid
        // Priority 2: Saved user preference from localStorage
        // Priority 3: First available station
        if (currentVal && stations.includes(currentVal)) {
            select.value = currentVal;
        } else if (preferredStation && stations.includes(preferredStation)) {
            select.value = preferredStation;
        } else {
            select.value = stations[0];
            if (currentVal && select.value !== currentVal) {
                lastReceivedId = null;
            }
        }
        savePreferences();
        console.log(`[Stations] Loaded ${stations.length} stations, selected: ${select.value}`);

    } catch (error) {
        console.error('[Error] Failed to load stations:', error);
        document.getElementById('station-select').innerHTML = '<option value="">Could not load</option>';
        showEmptyState();
    }
}

/**
 * Show error banner
 */
function showErrorBanner(msg) {
    const banner = document.getElementById('error-banner');
    const msgEl = document.getElementById('error-message-text');
    if (banner) {
        if (msgEl && msg) msgEl.textContent = msg;
        banner.style.display = 'flex';
    }
}

/**
 * Hide error banner
 */
function hideErrorBanner() {
    const banner = document.getElementById('error-banner');
    if (banner) banner.style.display = 'none';
}

/**
 * Handle range selector change - show/hide limit selector
 */
function onRangeChange() {
    savePreferences();
    syncRangeButtons();
    const rangeSelect = document.getElementById('range-select').value;
    const limitSelect = document.getElementById('limit-select');
    const limitField = document.getElementById('limit-field');

    if (rangeSelect === 'limit') {
        limitSelect.style.display = 'block';
        if (limitField) limitField.style.display = 'block';
    } else {
        limitSelect.style.display = 'none';
        if (limitField) limitField.style.display = 'none';
    }

    refreshAll();
}

/**
 * Set aria-busy state on dashboard content sections
 */
function setAriaBusy(isBusy) {
    const multiPanels = document.getElementById('multi-stats-container');
    const singleChart = document.getElementById('chart-container');
    const tableContainer = document.getElementById('table-container');
    const val = isBusy ? 'true' : 'false';
    if (multiPanels) multiPanels.setAttribute('aria-busy', val);
    if (singleChart) singleChart.setAttribute('aria-busy', val);
    if (tableContainer) tableContainer.setAttribute('aria-busy', val);
}

/**
 * Update container visibility according to current metric and display format
 */
function updateContainerVisibility(metric, format) {
    const multiStats = document.getElementById('multi-stats-container');
    const singleStats = document.getElementById('stats-container');
    const singleChart = document.getElementById('chart-container');
    const tableContainer = document.getElementById('table-container');
    const chartLines = document.getElementById('chart-lines');
    const isTable = format === 'table';

    // The four rows (reading + chart) or the single measurement's figures
    if (multiStats) {
        multiStats.style.display = metric === 'all' ? 'grid' : 'none';
        // In the table view the rows keep their readings and drop their charts
        multiStats.classList.toggle('is-compact', isTable);
    }
    if (singleStats) singleStats.style.display = metric === 'all' ? 'none' : 'grid';

    if (singleChart) singleChart.style.display = (!isTable && metric !== 'all') ? 'block' : 'none';
    if (tableContainer) tableContainer.style.display = isTable ? 'block' : 'none';

    // The line options only apply to charts
    if (chartLines) chartLines.hidden = isTable;
}

/**
 * Refresh stats and chart when selection changes (with in-flight request abort and race-condition guard)
 */
async function refreshAll() {
    // Abort any prior in-flight fetch to prevent delayed responses from older selections
    if (activeAbortController) {
        activeAbortController.abort();
    }
    activeAbortController = new AbortController();
    const abortSignal = activeAbortController.signal;
    const fetchId = ++currentFetchId;

    isRefreshing = true;
    document.querySelectorAll('[data-reading-timestamp]').forEach(el => {
        el.dataset.readingTimestamp = '';
        el.textContent = '--';
    });

    // Show loading state
    const loadingChip = document.getElementById('meta-loading');
    if (loadingChip) loadingChip.style.display = 'inline-flex';
    setAriaBusy(true);

    try {
        const station = document.getElementById('station-select').value;
        const metric = document.getElementById('metric-select').value;
        const range = document.getElementById('range-select').value;
        const limitSelect = document.getElementById('limit-select');

        // Update context bar station and range badges
        const contextStation = document.getElementById('context-station-name');
        if (contextStation) contextStation.textContent = station || 'No station';

        const rangeSelectElem = document.getElementById('range-select');
        const contextRange = document.getElementById('context-range-label');
        if (contextRange && rangeSelectElem) {
            if (range === 'limit') {
                const limitVal = limitSelect ? limitSelect.value : 50;
                contextRange.textContent = `Latest ${limitVal} readings`;
            } else {
                const selectedOpt = rangeSelectElem.options[rangeSelectElem.selectedIndex];
                contextRange.textContent = selectedOpt ? selectedOpt.textContent : range;
            }
        }

        if (!station) {
            showEmptyState();
            return;
        }

        let queryParams = `?station_id=${encodeURIComponent(station)}&metric=${encodeURIComponent(metric)}`;
        if (range === 'limit') {
            const limit = limitSelect ? limitSelect.value : 50;
            if (!limit) {
                showEmptyState();
                return;
            }
            queryParams += `&limit=${encodeURIComponent(limit)}`;
            console.log(`[Refresh #${fetchId}] ${station} / ${metric} / last ${limit} values`);
        } else {
            queryParams += `&range=${encodeURIComponent(range)}`;
            console.log(`[Refresh #${fetchId}] ${station} / ${metric} / ${range}`);
        }

        // Hide empty state and error banner on active refresh
        document.getElementById('empty-state').style.display = 'none';
        hideErrorBanner();
        hideStationEmptyState();

        // Update layout visibility
        updateContainerVisibility(metric, currentDisplayFormat);

        // Fetch stats and readings in parallel with abortSignal and fetchId
        const [, readingsResult] = await Promise.all([
            fetchStats(queryParams, abortSignal, fetchId, station, metric, range),
            fetchReadings(queryParams, abortSignal, fetchId, station, metric, range)
        ]);

        if (fetchId !== currentFetchId) {
            console.log(`[Refresh #${fetchId}] Superceded by #${currentFetchId}, ignoring completion`);
            return;
        }

        // Derive freshness from latest telemetry timestamp (S3)
        if (readingsResult && readingsResult.success) {
            lastUpdatedTime = readingsResult.latestTelemetryTime || null;
            updateLastUpdatedDisplay();
        }
    } catch (err) {
        if (err.name === 'AbortError') {
            console.log(`[Refresh #${fetchId}] Aborted by newer request`);
            return;
        }
        console.error(`[Refresh #${fetchId}] Error:`, err);
        // On error, preserve existing freshness state rather than marking Fresh (S3)
    } finally {
        if (fetchId === currentFetchId) {
            isRefreshing = false;
            if (loadingChip) loadingChip.style.display = 'none';
            setAriaBusy(false);
            if (pendingRefresh) {
                pendingRefresh = false;
                console.log('[Refresh] Running coalesced pending refresh');
                refreshAll();
            }
        }
    }
}

/**
 * Full refresh: reload stations and re-fetch dashboard data (used by Check Again and Retry)
 */
async function refreshAllWithStations() {
    hideErrorBanner();
    await loadStations();
    closeLiveStream();
    await refreshAll();
    initLiveStream();
}

/**
 * Fetch and display stats from full dataset
 */
async function fetchStats(queryParams, signal, fetchId, station, metric, range) {
    try {
        const url = `/api/stats${queryParams}`;
        const response = await fetch(url, { signal });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();

        // Verify that this response is still for the current active request
        if (fetchId && fetchId !== currentFetchId) {
            console.log(`[fetchStats #${fetchId}] Dropping stale response (current is #${currentFetchId})`);
            return null;
        }
        const activeStation = document.getElementById('station-select')?.value;
        const activeMetric = document.getElementById('metric-select')?.value;
        const activeRange = document.getElementById('range-select')?.value;
        if (station !== activeStation || metric !== activeMetric || range !== activeRange) {
            console.log(`[fetchStats #${fetchId}] Selection changed while in-flight, dropping delayed response`);
            return null;
        }

        if (metric === 'all') {
            updateMultiMetricStats(data);
        } else {
            updateSingleMetricStats(data, metric);
        }
        return { success: true };
    } catch (error) {
        if (error.name === 'AbortError') return null;
        console.error('[Error] Failed to fetch stats:', error);
        showErrorBanner('Could not load the figures. Check that the server is running, then try again.');
        throw error;
    }
}

/**
 * Format elapsed reading age in a human-readable form
 */
function formatReadingAge(ageSec) {
    if (ageSec === null || ageSec === undefined || isNaN(ageSec)) return '--';
    if (ageSec < 60) return `${Math.round(ageSec)} s ago`;
    if (ageSec < 3600) return `${Math.floor(ageSec / 60)} min ago`;
    return `${Math.floor(ageSec / 3600)} h ago`;
}

const CHANGE_REASONS = {
    'Comparison unavailable for record count window': 'No comparison for latest readings',
    'Comparison unavailable for all history': 'No comparison for all readings',
    'No readings in preceding period': 'No earlier readings',
    'Insufficient data': 'Too few readings'
};

/**
 * Format the change against the period before, signed and with its unit, or say why there is none
 */
function formatPeriodChange(changeDetails, unit, short = false) {
    if (!changeDetails) return '--';
    if (!changeDetails.available) {
        if (short) return 'No comparison';
        return CHANGE_REASONS[changeDetails.reason] || changeDetails.reason || 'No comparison';
    }
    const delta = changeDetails.mean_change;
    if (delta === null || delta === undefined || isNaN(delta)) return '--';
    const rounded = Number(delta.toFixed(1));
    const sign = rounded > 0 ? '+' : (rounded < 0 ? '\u2212' : '');
    return `${sign}${Math.abs(rounded).toFixed(1)} ${unit}`;
}

/**
 * Update the reading and figures of each of the four rows
 */
function updateMultiMetricStats(data) {
    if (!data) return;

    for (const key of ['temperature', 'humidity', 'windspeed', 'dew_point']) {
        const item = data[key] || {};
        const p = METRIC_CONFIGS[key].prefix;
        const u = METRIC_CONFIGS[key].unit;
        const mean = item.sample_mean !== null && item.sample_mean !== undefined ? item.sample_mean : item.avg;
        const set = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
        };

        setReading(document.getElementById(`stat-${p}-latest`), item.latest, u);
        set(`stat-${p}-min`, formatValue(item.min, ''));
        set(`stat-${p}-max`, formatValue(item.max, u));
        set(`stat-${p}-avg`, formatValue(mean, u));
        set(`stat-${p}-twa`, formatValue(item.time_weighted_mean, u));
        const changeEl = document.getElementById(`stat-${p}-change`);
        if (changeEl) {
            changeEl.textContent = formatPeriodChange(item.change_details, u, true);
            const why = item.change_details && !item.change_details.available
                ? (CHANGE_REASONS[item.change_details.reason] || item.change_details.reason || '')
                : '';
            if (why) changeEl.title = why; else changeEl.removeAttribute('title');
        }
    }
}

/**
 * Update single-metric stats cards
 */
function updateSingleMetricStats(data, metric) {
    const config = METRIC_CONFIGS[metric] || { unit: '', title: metric };
    const u = config.unit || data.unit || '';
    const mean = data.sample_mean !== null && data.sample_mean !== undefined ? data.sample_mean : data.avg;
    const count = data.sample_count !== null && data.sample_count !== undefined ? data.sample_count : data.count;
    const set = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    };

    // The figures take the colour and the name of the measurement they describe
    const container = document.getElementById('stats-container');
    if (container) container.dataset.metric = config.id || metric;
    set('single-metric-title', config.title);

    setReading(document.getElementById('stat-latest'), data.latest, u);
    set('stat-latest-time', data.latest_t ? formatFullTimestamp(data.latest_t) : '');
    set('stat-avg', formatValue(mean, u));
    set('stat-twa', formatValue(data.time_weighted_mean, u));
    set('stat-min', formatValue(data.min, u));
    set('stat-max', formatValue(data.max, u));
    set('stat-count', count !== null && count !== undefined ? `${count}` : '--');
    set('stat-change', formatPeriodChange(data.change_details, u));

    const statAge = document.getElementById('stat-age');
    if (statAge) {
        statAge.textContent = formatReadingAge(data.reading_age_seconds);
        statAge.dataset.readingTimestamp = data.latest_t || '';
    }
    const footnote = document.getElementById('stat-change-footnote');
    if (footnote) {
        footnote.textContent = (data.change_details && data.change_details.available) ? 'against the period before' : '';
    }
}

/**
 * Show station-specific empty state when no telemetry exists in the selected window
 */
function showStationEmptyState(station, range) {
    const el = document.getElementById('station-empty-state');
    const textEl = document.getElementById('station-empty-text');
    if (el) {
        if (textEl) {
            const rangeElem = document.getElementById('range-select');
            const rangeText = (rangeElem && rangeElem.selectedIndex >= 0)
                ? rangeElem.options[rangeElem.selectedIndex].textContent
                : range;
            textEl.textContent = `${station} sent nothing in this time range (${rangeText.toLowerCase()}).`;
        }
        el.style.display = 'block';
    }
    ['multi-stats-container', 'stats-container', 'chart-container', 'table-container'].forEach(id => {
        const part = document.getElementById(id);
        if (part) part.style.display = 'none';
    });
}

/**
 * Hide station-specific empty state
 */
function hideStationEmptyState() {
    const el = document.getElementById('station-empty-state');
    if (el) el.style.display = 'none';
}

/**
 * Recovery action to change range and refresh
 */
window.setRangeAndRefresh = function(rangeVal) {
    const rangeSelect = document.getElementById('range-select');
    if (rangeSelect) {
        rangeSelect.value = rangeVal;
        onRangeChange();
    }
};

/**
 * Fetch and display readings for all panels or single panel
 */
async function fetchReadings(queryParams, signal, fetchId, station, metric, range) {
    try {
        const urlParams = new URLSearchParams(queryParams);
        const url = `/api/readings${queryParams}`;
        const response = await fetch(url, { signal });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();

        // Verify that this response is still for the current active request
        if (fetchId && fetchId !== currentFetchId) {
            console.log(`[fetchReadings #${fetchId}] Dropping stale response (current is #${currentFetchId})`);
            return null;
        }
        const activeStation = document.getElementById('station-select')?.value;
        const activeMetric = document.getElementById('metric-select')?.value;
        const activeRange = document.getElementById('range-select')?.value;
        if (station !== activeStation || metric !== activeMetric || range !== activeRange) {
            console.log(`[fetchReadings #${fetchId}] Selection changed while in-flight, dropping delayed response`);
            return null;
        }

        const rawEnabled = document.getElementById('toggleRaw')?.checked;
        const rollingEnabled = document.getElementById('toggleRolling')?.checked;
        const windowSize = parseInt(document.getElementById('rollingWindow')?.value, 10) || 10;

        let latestTelemetryTime = null;

        if (metric === 'all') {
            const series = data.series || {};
            const totalPoints = data.total_points || (data.points ? data.points.length : 0);

            multiSeries.temperature = series.temperature || [];
            multiSeries.humidity = series.humidity || [];
            multiSeries.windspeed = series.windspeed || [];
            multiSeries.dew_point = series.dew_point || [];

            updateMetaChips(totalPoints, totalPoints > 350);

            // Derive latest telemetry timestamp across all multi-panel series (S3)
            const allPoints = [
                ...(multiSeries.temperature || []),
                ...(multiSeries.humidity || []),
                ...(multiSeries.windspeed || []),
                ...(multiSeries.dew_point || [])
            ];
            for (const pt of allPoints) {
                if (pt && pt.t) {
                    const d = new Date(pt.t);
                    if (!isNaN(d.getTime())) {
                        if (!latestTelemetryTime || d > latestTelemetryTime) {
                            latestTelemetryTime = d;
                        }
                    }
                }
            }

            const hasPoints = (multiSeries.temperature.length > 0 || multiSeries.humidity.length > 0 || multiSeries.windspeed.length > 0 || multiSeries.dew_point.length > 0);
            if (!hasPoints) {
                showStationEmptyState(station, range);
            } else {
                hideStationEmptyState();
                updateContainerVisibility('all', currentDisplayFormat);
                if (currentDisplayFormat === 'table') {
                    renderAccessibleTable();
                } else if (typeof Chart !== 'undefined') {
                    renderAlignedPanels(multiSeries, rawEnabled, rollingEnabled, windowSize, range);
                } else {
                    renderAccessibleTable();
                }
            }
        } else {
            const points = data.points || [];
            currentRawPoints = points;
            const totalPoints = data.total_points || points.length;

            updateMetaChips(totalPoints, totalPoints > 350);

            // Derive latest telemetry timestamp for single metric (S3)
            for (const pt of points) {
                if (pt && pt.t) {
                    const d = new Date(pt.t);
                    if (!isNaN(d.getTime())) {
                        if (!latestTelemetryTime || d > latestTelemetryTime) {
                            latestTelemetryTime = d;
                        }
                    }
                }
            }

            if (points.length === 0) {
                showStationEmptyState(station, range);
            } else {
                hideStationEmptyState();
                updateContainerVisibility(metric, currentDisplayFormat);
                if (currentDisplayFormat === 'table') {
                    renderAccessibleTable();
                } else if (typeof Chart !== 'undefined') {
                    renderSingleChart(metric, points, rawEnabled, rollingEnabled, windowSize, range);
                } else {
                    renderAccessibleTable();
                }
            }
        }
        return { success: true, latestTelemetryTime };
    } catch (error) {
        if (error.name === 'AbortError') return null;
        console.error('[Error] Failed to fetch readings:', error);
        showErrorBanner('Could not load readings. Check that the server is running, then try again.');
        throw error;
    }
}

/**
 * Update metadata chips showing point count, aggregation state, and missing data gaps
 */
function updateMetaChips(totalCount, isAggregated, gapCount = 0) {
    const countChip = document.getElementById('meta-points-count');
    const aggChip = document.getElementById('meta-aggregation');
    const gapChip = document.getElementById('meta-gaps');

    if (countChip) {
        countChip.textContent = `${totalCount} ${totalCount === 1 ? 'reading' : 'readings'}`;
    }

    if (aggChip) {
        aggChip.style.display = isAggregated ? 'inline-flex' : 'none';
        aggChip.textContent = 'Chart simplified, peaks kept';
    }

    if (gapChip) {
        gapChip.style.display = gapCount > 0 ? 'inline-flex' : 'none';
        gapChip.textContent = gapCount === 1 ? '1 gap in the data' : `${gapCount} gaps in the data`;
    }
}

/**
 * Detect missing intervals in time series and insert null gap points with spanGaps: false (S4, S2)
 */
function insertMissingGaps(points) {
    if (!points || points.length < 2) return { points: points || [], gapCount: 0 };

    const deltas = [];
    for (let i = 0; i < points.length - 1; i++) {
        if (points[i].v === null || points[i + 1].v === null) continue;
        const t1 = new Date(points[i].t).getTime();
        const t2 = new Date(points[i + 1].t).getTime();
        const diff = t2 - t1;
        if (diff > 0) deltas.push(diff);
    }
    if (deltas.length === 0) return { points, gapCount: 0 };

    deltas.sort((a, b) => a - b);
    const medianDelta = deltas[Math.floor(deltas.length / 2)];
    const threshold = Math.max(medianDelta * 3, 15 * 60 * 1000);

    const result = [];
    let gapCount = 0;
    for (let i = 0; i < points.length; i++) {
        result.push(points[i]);
        if (points[i].v === null) {
            gapCount++;
        }
        if (i < points.length - 1) {
            if (points[i].v !== null && points[i + 1].v !== null) {
                const t1 = new Date(points[i].t).getTime();
                const t2 = new Date(points[i + 1].t).getTime();
                if ((t2 - t1) > threshold) {
                    gapCount++;
                    const midIso = new Date(t1 + Math.floor((t2 - t1) / 2)).toISOString();
                    result.push({ t: midIso, v: null, isGap: true });
                }
            }
        }
    }
    return { points: result, gapCount };
}

/**
 * Downsample raw points while preserving sharp peak and trough extrema within buckets (S3)
 */
function downsamplePeakTroughClient(points, maxPoints = 350) {
    if (!points || points.length <= maxPoints) return points;

    const segments = [];
    let currentSegment = [];

    for (const p of points) {
        if (p.v === null || p.v === undefined) {
            if (currentSegment.length > 0) {
                segments.push({ type: 'valid', data: currentSegment });
                currentSegment = [];
            }
            if (segments.length === 0 || segments[segments.length - 1].type !== 'gap') {
                segments.push({ type: 'gap', data: p });
            }
        } else {
            currentSegment.push(p);
        }
    }
    if (currentSegment.length > 0) {
        segments.push({ type: 'valid', data: currentSegment });
    }

    const totalValid = segments.reduce((acc, seg) => seg.type === 'valid' ? acc + seg.data.length : acc, 0);
    if (totalValid === 0) {
        return segments.filter(s => s.type === 'gap').map(s => s.data);
    }

    const gapCount = segments.filter(s => s.type === 'gap').length;
    const availableBudget = Math.max(2, maxPoints - gapCount);

    const result = [];
    for (const seg of segments) {
        if (seg.type === 'gap') {
            result.push(seg.data);
            continue;
        }

        const data = seg.data;
        const segBudget = Math.max(2, Math.floor(availableBudget * (data.length / totalValid)));
        if (data.length <= segBudget || segBudget < 2) {
            result.push(...data);
            continue;
        }

        const numBuckets = Math.max(1, Math.floor(segBudget / 2));
        const bucketSize = data.length / numBuckets;

        for (let b = 0; b < numBuckets; b++) {
            const startIdx = Math.floor(b * bucketSize);
            const endIdx = b < numBuckets - 1 ? Math.floor((b + 1) * bucketSize) : data.length;
            const bucket = data.slice(startIdx, endIdx);
            if (bucket.length === 0) continue;

            let minPt = bucket[0];
            let maxPt = bucket[0];
            let sum = 0;

            for (const pt of bucket) {
                sum += pt.v;
                if (pt.v < minPt.v) minPt = pt;
                if (pt.v > maxPt.v) maxPt = pt;
            }

            const avgVal = parseFloat((sum / bucket.length).toFixed(2));
            const minVal = parseFloat(minPt.v.toFixed(2));
            const maxVal = parseFloat(maxPt.v.toFixed(2));
            const count = bucket.length;

            const minTime = new Date(minPt.t).getTime();
            const maxTime = new Date(maxPt.t).getTime();
            const firstPt = minTime <= maxTime ? minPt : maxPt;
            const secondPt = minTime <= maxTime ? maxPt : minPt;

            result.push({
                ...firstPt,
                min: minVal,
                max: maxVal,
                avg: avgVal,
                count: count,
                aggregated: true
            });

            if (secondPt !== firstPt && count > 1) {
                result.push({
                    ...secondPt,
                    min: minVal,
                    max: maxVal,
                    avg: avgVal,
                    count: count,
                    aggregated: true
                });
            }
        }
    }
    return result;
}

/**
 * Downsample rolling averages by bucket midpoint to maintain smooth trendline (S3)
 */
function downsampleRollingClient(points, maxPoints = 350) {
    if (!points || points.length <= maxPoints) return points;

    const segments = [];
    let currentSegment = [];

    for (const p of points) {
        if (p.v === null || p.v === undefined) {
            if (currentSegment.length > 0) {
                segments.push({ type: 'valid', data: currentSegment });
                currentSegment = [];
            }
            if (segments.length === 0 || segments[segments.length - 1].type !== 'gap') {
                segments.push({ type: 'gap', data: p });
            }
        } else {
            currentSegment.push(p);
        }
    }
    if (currentSegment.length > 0) {
        segments.push({ type: 'valid', data: currentSegment });
    }

    const totalValid = segments.reduce((acc, seg) => seg.type === 'valid' ? acc + seg.data.length : acc, 0);
    if (totalValid === 0) {
        return segments.filter(s => s.type === 'gap').map(s => s.data);
    }

    const gapCount = segments.filter(s => s.type === 'gap').length;
    const availableBudget = Math.max(2, maxPoints - gapCount);

    const result = [];
    for (const seg of segments) {
        if (seg.type === 'gap') {
            result.push(seg.data);
            continue;
        }

        const data = seg.data;
        const segBudget = Math.max(2, Math.floor(availableBudget * (data.length / totalValid)));
        if (data.length <= segBudget || segBudget < 2) {
            result.push(...data);
            continue;
        }

        const numBuckets = Math.max(1, segBudget);
        const bucketSize = data.length / numBuckets;

        for (let b = 0; b < numBuckets; b++) {
            const startIdx = Math.floor(b * bucketSize);
            const endIdx = b < numBuckets - 1 ? Math.floor((b + 1) * bucketSize) : data.length;
            const bucket = data.slice(startIdx, endIdx);
            if (bucket.length === 0) continue;

            const midIdx = Math.floor(bucket.length / 2);
            result.push(bucket[midIdx]);
        }
    }
    return result;
}

/**
 * Pre-compute rolling average over FULL raw reading sequence BEFORE downsampling (S3)
 */
function computeRollingValuesOnRawPoints(points, windowSize) {
    if (!points || points.length === 0) return [];
    if (windowSize <= 1) return points.map(p => ({ t: p.t, v: p.v }));

    const result = [];
    let sum = 0;
    const window = [];

    for (let i = 0; i < points.length; i++) {
        const val = points[i].v;
        if (val !== null && val !== undefined && !isNaN(val)) {
            window.push(val);
            sum += val;
            if (window.length > windowSize) {
                sum -= window.shift();
            }
            result.push({ t: points[i].t, v: sum / window.length });
        } else {
            window.length = 0; // Reset rolling buffer on gap/null
            sum = 0;
            result.push({ t: points[i].t, v: null });
        }
    }
    return result;
}

/**
 * Calculate common global continuous time bounds across all series (S1)
 */
function getCommonTimeBounds(seriesMap) {
    let minTime = Infinity;
    let maxTime = -Infinity;

    for (const key of Object.keys(seriesMap)) {
        const pts = seriesMap[key] || [];
        for (const p of pts) {
            if (!p.t) continue;
            const ms = new Date(p.t).getTime();
            if (ms < minTime) minTime = ms;
            if (ms > maxTime) maxTime = ms;
        }
    }

    if (minTime === Infinity || maxTime === -Infinity) {
        const now = Date.now();
        return { minTime: now - 3600000, maxTime: now };
    }
    if (minTime === maxTime) {
        return { minTime: minTime - 60000, maxTime: maxTime + 60000 };
    }
    return { minTime, maxTime };
}

/**
 * Prepare point objects for continuous linear-time axis with raw references (S1, S2, S3, S4)
 */
function prepareSeriesForContinuousAxis(rawSeries, rawEnabled, rollingEnabled, windowSize) {
    // 1. Calculate true rolling averages on the full raw sequence first (S3)
    const rollingSeries = rollingEnabled ? computeRollingValuesOnRawPoints(rawSeries, windowSize) : [];

    // 2. Insert missing gap markers on full raw sequence BEFORE downsampling (S4)
    const { points: gappedRaw, gapCount: rawGaps } = insertMissingGaps(rawSeries);
    const { points: gappedRolling, gapCount: rollingGaps } = insertMissingGaps(rollingSeries);

    // 3. Downsample for canvas display preserving extrema and gap boundaries (S3, S4)
    const displayRaw = gappedRaw.length > 350 ? downsamplePeakTroughClient(gappedRaw, 350) : gappedRaw;
    const displayRolling = gappedRolling.length > 350 ? downsampleRollingClient(gappedRolling, 350) : gappedRolling;

    // 4. Map into { x: timestampMs, y: val, raw: pointRef }
    const rawData = rawEnabled ? displayRaw.map(p => ({
        x: new Date(p.t).getTime(),
        y: p.v,
        raw: p
    })) : [];

    const rollingData = rollingEnabled ? displayRolling.map(p => ({
        x: new Date(p.t).getTime(),
        y: p.v,
        raw: p
    })) : [];

    return {
        rawData,
        rollingData,
        gapCount: Math.max(rawGaps, rollingGaps),
        isAggregated: rawSeries.length > 350
    };
}

/**
 * Update all panel datasets and synchronize time bounds on live telemetry updates (S1, S2, S3)
 */
function refreshAllPanelsData(seriesMap, rawEnabled, rollingEnabled, windowSize, range) {
    const { minTime, maxTime } = getCommonTimeBounds(seriesMap);
    let totalGaps = 0;
    let anyAggregated = false;
    const metrics = ['temperature', 'humidity', 'windspeed', 'dew_point'];

    for (const m of metrics) {
        const c = charts[m];
        if (!c) continue;

        const { rawData, rollingData, gapCount, isAggregated } = prepareSeriesForContinuousAxis(
            seriesMap[m] || [],
            rawEnabled,
            rollingEnabled,
            windowSize
        );
        totalGaps = Math.max(totalGaps, gapCount);
        if (isAggregated) anyAggregated = true;

        // Update scales with common shared bounds (S1)
        c.options.scales.x.min = minTime;
        c.options.scales.x.max = maxTime;

        const rawDs = c.data.datasets.find(ds => ds.id === 'raw');
        if (rawDs) rawDs.data = rawData;

        const rollingDs = c.data.datasets.find(ds => ds.id === 'rolling');
        if (rollingDs) rollingDs.data = rollingData;

        c.update('none');
    }

    const total = (seriesMap.temperature || []).length;
    updateMetaChips(total, anyAggregated, totalGaps);
}

/**
 * The two lines every chart can carry: the readings and their rolling average
 */
function buildChartDatasets(config, rawData, rollingData, rawEnabled, rollingEnabled, windowSize) {
    const mColors = getThemedMetricColors(config);
    const tokens = getChartThemeTokens();
    const datasets = [];

    if (rawEnabled) {
        datasets.push({
            id: 'raw',
            label: config.title,
            data: rawData,
            borderColor: mColors.rawBorder,
            backgroundColor: mColors.rawBg,
            borderWidth: 2,
            fill: false,
            tension: 0.15,
            clip: 4, // a line that touches the top or bottom tick keeps its full width
            spanGaps: false, // a gap in the data stays a break in the line
            pointRadius: rawData.length > 60 ? 0 : 3,
            pointHoverRadius: 5,
            pointBackgroundColor: mColors.color,
            pointHoverBackgroundColor: mColors.color,
            pointBorderColor: tokens.pointBorder,
            pointBorderWidth: 2,
        });
    }

    if (rollingEnabled && rollingData.length > 0) {
        datasets.push({
            id: 'rolling',
            label: `Average of ${windowSize}`,
            data: rollingData,
            borderColor: mColors.rollingBorder,
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            borderDash: [4, 4],
            fill: false,
            tension: 0.25,
            spanGaps: false,
            pointRadius: 0,
            pointHoverRadius: 4,
        });
    }

    return datasets;
}

/**
 * Shared chart options: one quiet look for the four rows and for the single chart
 */
function buildChartOptions(config, range, minTime, maxTime, { showTimeAxis, maxTimeTicks }) {
    const tokens = getChartThemeTokens();
    const tickFont = { family: CHART_FONT, size: 13 };

    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        layout: {
            padding: { top: 8, right: 4, bottom: 0, left: 0 }
        },
        interaction: {
            mode: 'nearest',
            axis: 'x',
            intersect: false,
        },
        plugins: {
            // The line options above the charts are the key, so no legend is drawn per chart
            legend: {
                display: false,
                labels: { color: tokens.legendColor }
            },
            tooltip: {
                enabled: true,
                backgroundColor: tokens.tooltipBg,
                titleColor: tokens.tooltipInk,
                bodyColor: tokens.tooltipInk,
                borderWidth: 0,
                padding: 12,
                cornerRadius: 8,
                boxWidth: 8,
                boxHeight: 8,
                boxPadding: 6,
                usePointStyle: true,
                titleFont: { family: CHART_FONT, size: 13, weight: '400' },
                bodyFont: { family: CHART_FONT, size: 14, weight: '500' },
                callbacks: {
                    labelColor: (context) => {
                        const fill = context.dataset.id === 'rolling' ? tokens.tooltipInk : context.dataset.borderColor;
                        return { borderColor: fill, backgroundColor: context.dataset.id === 'rolling' ? 'transparent' : fill, borderWidth: 2 };
                    },
                    title: (tooltipItems) => {
                        if (!tooltipItems.length) return '';
                        const rawObj = tooltipItems[0].raw;
                        const pt = rawObj ? rawObj.raw : null;
                        return pt ? formatFullTimestamp(pt.t) : formatFullTimestamp(new Date(tooltipItems[0].parsed.x).toISOString());
                    },
                    label: (context) => {
                        const rawObj = context.raw;
                        const pt = rawObj ? rawObj.raw : null;
                        if (!pt || pt.v === null || context.parsed.y === null) {
                            return `${context.dataset.label}: no reading`;
                        }

                        let line = `${context.dataset.label}: ${context.parsed.y.toFixed(1)} ${config.unit}`;
                        if (pt.aggregated && context.dataset.id === 'raw') {
                            line += ` (${pt.count} readings, ${Number(pt.min).toFixed(1)} to ${Number(pt.max).toFixed(1)})`;
                        }
                        return line;
                    }
                }
            }
        },
        scales: {
            y: {
                beginAtZero: false,
                border: { display: false },
                grid: { color: tokens.gridY, drawTicks: false },
                // Every chart keeps the same axis width, so the four time axes line up
                afterFit: (scale) => { scale.width = 52; },
                ticks: {
                    color: tokens.tickColor,
                    font: tickFont,
                    maxTicksLimit: 6,
                    padding: 8
                }
            },
            x: {
                type: 'linear',
                min: minTime,
                max: maxTime,
                border: { display: false },
                grid: { display: false, color: tokens.gridX },
                // No side padding for the end labels: the four plots share their left and right edges
                afterFit: (scale) => { scale.paddingLeft = 0; scale.paddingRight = 0; },
                ticks: {
                    display: showTimeAxis,
                    color: tokens.tickColor,
                    autoSkip: true,
                    align: 'inner',
                    maxTicksLimit: window.innerWidth <= 480 ? 4 : maxTimeTicks,
                    maxRotation: 0,
                    padding: 8,
                    font: tickFont,
                    callback: function(value) {
                        return formatAxisTime(new Date(value).toISOString(), range);
                    }
                }
            }
        }
    };
}

/**
 * Render the four stacked charts for temperature, humidity, wind speed and dew point
 * on one shared continuous time axis (S1, S2, S3)
 */
function renderAlignedPanels(seriesMap, rawEnabled, rollingEnabled, windowSize, range) {
    let totalGaps = 0;
    let anyAggregated = false;
    const metrics = ['temperature', 'humidity', 'windspeed', 'dew_point'];
    const { minTime, maxTime } = getCommonTimeBounds(seriesMap);

    for (const m of metrics) {
        const rawPoints = seriesMap[m] || [];
        const { rawData, rollingData, gapCount, isAggregated } = prepareSeriesForContinuousAxis(
            rawPoints,
            rawEnabled,
            rollingEnabled,
            windowSize
        );
        totalGaps = Math.max(totalGaps, gapCount);
        if (isAggregated) anyAggregated = true;

        const config = METRIC_CONFIGS[m];
        const ctx = document.getElementById(config.canvasId);
        if (!ctx) continue;

        if (charts[m]) {
            charts[m].destroy();
        }

        charts[m] = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: buildChartDatasets(config, rawData, rollingData, rawEnabled, rollingEnabled, windowSize)
            },
            // The times are written once, under the last chart
            options: buildChartOptions(config, range, minTime, maxTime, {
                showTimeAxis: m === 'dew_point',
                maxTimeTicks: 8
            })
        });
    }

    const total = (seriesMap.temperature || []).length;
    updateMetaChips(total, anyAggregated, totalGaps);
}

/**
 * Render single chart when an individual metric is chosen (S1, S2, S3, S4)
 */
function renderSingleChart(metric, points, rawEnabled, rollingEnabled, windowSize, range) {
    const ctx = document.getElementById('data-chart');
    if (!ctx) return;

    if (chart) {
        chart.destroy();
    }
    if (charts.single) {
        charts.single.destroy();
    }

    const config = METRIC_CONFIGS[metric] || {
        id: 'temperature',
        title: metric.charAt(0).toUpperCase() + metric.slice(1),
        unit: ''
    };

    const { rawData, rollingData, gapCount, isAggregated } = prepareSeriesForContinuousAxis(
        points,
        rawEnabled,
        rollingEnabled,
        windowSize
    );

    updateMetaChips(points.length, isAggregated, gapCount);

    let minTime = Infinity;
    let maxTime = -Infinity;
    for (const p of points) {
        if (!p.t) continue;
        const ms = new Date(p.t).getTime();
        if (ms < minTime) minTime = ms;
        if (ms > maxTime) maxTime = ms;
    }
    if (minTime === Infinity || maxTime === -Infinity) {
        const now = Date.now();
        minTime = now - 3600000;
        maxTime = now;
    }

    ctx.style.display = 'block';
    const chartLoading = document.getElementById('chart-loading');
    if (chartLoading) chartLoading.style.display = 'none';

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: buildChartDatasets(config, rawData, rollingData, rawEnabled, rollingEnabled, windowSize)
        },
        options: buildChartOptions(config, range, minTime, maxTime, {
            showTimeAxis: true,
            maxTimeTicks: 10
        })
    });

    charts.single = chart;
}

/**
 * Set display format ('chart' or 'table') and update view
 */
window.setDisplayFormat = function(format) {
    currentDisplayFormat = format;
    savePreferences();
    updateDisplayFormatUI(format);

    const metric = document.getElementById('metric-select').value;
    updateContainerVisibility(metric, format);

    if (format === 'table') {
        renderAccessibleTable();
    } else {
        const rawEnabled = document.getElementById('toggleRaw').checked;
        const rollingEnabled = document.getElementById('toggleRolling').checked;
        const windowSize = parseInt(document.getElementById('rollingWindow').value, 10) || 10;
        const range = document.getElementById('range-select').value;
        if (metric === 'all') {
            renderAlignedPanels(multiSeries, rawEnabled, rollingEnabled, windowSize, range);
        } else {
            renderSingleChart(metric, currentRawPoints, rawEnabled, rollingEnabled, windowSize, range);
        }
    }
};

/**
 * Update UI buttons for display mode
 */
function updateDisplayFormatUI(format) {
    const btnCharts = document.getElementById('btn-view-charts');
    const btnTable = document.getElementById('btn-view-table');
    if (btnCharts && btnTable) {
        btnCharts.classList.toggle('active', format === 'chart');
        btnCharts.setAttribute('aria-pressed', format === 'chart');
        btnTable.classList.toggle('active', format === 'table');
        btnTable.setAttribute('aria-pressed', format === 'table');
    }
}

/**
 * Render accessible data table with semantics, units, and clear structure
 */
function renderAccessibleTable() {
    const metricMode = document.getElementById('metric-select')?.value || 'all';
    const theadRow = document.getElementById('table-head-row');
    const tbody = document.getElementById('table-body');
    const summaryChip = document.getElementById('table-caption-summary');
    const captionEl = document.getElementById('table-accessible-caption');
    if (!tbody || !theadRow) return;

    if (metricMode === 'all') {
        theadRow.innerHTML = `
            <th scope="col">Time (UTC)</th>
            <th scope="col">Temperature, °C</th>
            <th scope="col">Humidity, %</th>
            <th scope="col">Wind speed, m/s</th>
            <th scope="col">Dew point, °C</th>
        `;

        // Align rows by timestamp
        const timeMap = new Map();
        (multiSeries.temperature || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).temp = p.v;
        });
        (multiSeries.humidity || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).hum = p.v;
        });
        (multiSeries.windspeed || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).wind = p.v;
        });
        (multiSeries.dew_point || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).dew = p.v;
        });

        const sortedTimestamps = Array.from(timeMap.keys()).sort();
        if (summaryChip) summaryChip.textContent = `${sortedTimestamps.length} readings`;
        if (captionEl) captionEl.textContent = `Readings for all four measurements: ${sortedTimestamps.length} rows`;

        if (sortedTimestamps.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="table-empty">No readings in this time range</td></tr>`;
            return;
        }

        tbody.innerHTML = sortedTimestamps.map(t => {
            const row = timeMap.get(t);
            const tempStr = (row.temp !== undefined && row.temp !== null) ? row.temp.toFixed(2) : '--';
            const humStr = (row.hum !== undefined && row.hum !== null) ? row.hum.toFixed(2) : '--';
            const windStr = (row.wind !== undefined && row.wind !== null) ? row.wind.toFixed(2) : '--';
            const dewStr = (row.dew !== undefined && row.dew !== null) ? row.dew.toFixed(2) : '--';
            return `<tr>
                <th scope="row">${formatUtcTimestamp(t)}</th>
                <td>${tempStr}</td>
                <td>${humStr}</td>
                <td>${windStr}</td>
                <td>${dewStr}</td>
            </tr>`;
        }).join('');
    } else {
        const config = METRIC_CONFIGS[metricMode] || { title: metricMode, unit: '' };
        theadRow.innerHTML = `
            <th scope="col">Time (UTC)</th>
            <th scope="col">${config.title}, ${config.unit}</th>
        `;

        const points = currentRawPoints || [];
        if (summaryChip) summaryChip.textContent = `${points.length} readings`;
        if (captionEl) captionEl.textContent = `${config.title} readings: ${points.length} rows`;

        if (points.length === 0) {
            tbody.innerHTML = `<tr><td colspan="2" class="table-empty">No readings in this time range</td></tr>`;
            return;
        }

        tbody.innerHTML = points.map(p => {
            const valStr = (p.v !== undefined && p.v !== null) ? p.v.toFixed(2) : 'N/A';
            return `<tr>
                <th scope="row">${formatUtcTimestamp(p.t)}</th>
                <td>${valStr}</td>
            </tr>`;
        }).join('');
    }
}

/**
 * Legacy renderTable wrapper for backward compatibility
 */
function renderTable(points) {
    if (points && points.length > 0 && (!currentRawPoints || currentRawPoints.length === 0)) {
        currentRawPoints = points;
    }
    renderAccessibleTable();
}

/**
 * Copy data table as CSV to clipboard
 */
window.copyTableAsCsv = function() {
    const metricMode = document.getElementById('metric-select')?.value || 'all';
    let csv = '';

    if (metricMode === 'all') {
        csv = 'Timestamp,Temperature_C,Humidity_pct,WindSpeed_ms,DewPoint_C\n';
        const timeMap = new Map();
        (multiSeries.temperature || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).temp = p.v;
        });
        (multiSeries.humidity || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).hum = p.v;
        });
        (multiSeries.windspeed || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).wind = p.v;
        });
        (multiSeries.dew_point || []).forEach(p => {
            if (!timeMap.has(p.t)) timeMap.set(p.t, {});
            timeMap.get(p.t).dew = p.v;
        });
        const sortedTimestamps = Array.from(timeMap.keys()).sort();
        for (const t of sortedTimestamps) {
            const r = timeMap.get(t);
            csv += `"${t}",${r.temp ?? ''},${r.hum ?? ''},${r.wind ?? ''},${r.dew ?? ''}\n`;
        }
    } else {
        const config = METRIC_CONFIGS[metricMode] || { title: metricMode, unit: '' };
        csv = `Timestamp,${config.title}_${config.unit}\n`;
        for (const p of (currentRawPoints || [])) {
            csv += `"${p.t}",${p.v ?? ''}\n`;
        }
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(csv).then(() => {
            const btn = document.getElementById('btn-copy-csv');
            if (btn) {
                const orig = btn.innerHTML;
                btn.textContent = 'Copied';
                setTimeout(() => { btn.innerHTML = orig; }, 2000);
            }
        }).catch(err => {
            console.error('Failed to copy CSV:', err);
        });
    }
};

/**
 * Show global empty state message
 */
function showEmptyState() {
    document.getElementById('empty-state').style.display = 'block';
    hideStationEmptyState();
    ['multi-stats-container', 'stats-container', 'chart-container', 'table-container'].forEach(id => {
        const part = document.getElementById(id);
        if (part) part.style.display = 'none';
    });
    const contextStation = document.getElementById('context-station-name');
    if (contextStation) contextStation.textContent = 'No station';
    const count = document.getElementById('meta-points-count');
    if (count) count.textContent = 'no readings';
}

/**
 * Format timestamp for readable axis ticks according to selected range
 */
function formatAxisTime(isoString, range) {
    if (!isoString) return '';
    try {
        const date = new Date(isoString);
        if (range === '1h' || range === '6h' || range === '24h' || range === 'limit') {
            return date.toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            });
        }
        return date.toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        });
    } catch (e) {
        return isoString;
    }
}

/**
 * Full formatted timestamp for tooltips and cards
 */
function formatFullTimestamp(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleString('en-US', {
            month: 'short',
            day: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
    } catch (e) {
        return isoString;
    }
}

/**
 * Legacy formatTime backward compatibility alias
 */
function formatTime(isoString) {
    return formatFullTimestamp(isoString);
}

/**
 * Explicit UTC timestamp formatter for table and data presentation (S4)
 */
function formatUtcTimestamp(isoString) {
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return String(isoString || '');
        const pad = (n) => String(n).padStart(2, '0');
        const year = date.getUTCFullYear();
        const month = pad(date.getUTCMonth() + 1);
        const day = pad(date.getUTCDate());
        const hours = pad(date.getUTCHours());
        const minutes = pad(date.getUTCMinutes());
        const seconds = pad(date.getUTCSeconds());
        return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
    } catch (e) {
        return String(isoString || '');
    }
}
