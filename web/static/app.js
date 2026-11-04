let chart = null; // Legacy single-chart handle for backward compatibility
const charts = {
    temperature: null,
    humidity: null,
    windspeed: null,
    single: null
};

// Full raw telemetry arrays
let currentRawPoints = [];
let multiSeries = {
    temperature: [],
    humidity: [],
    windspeed: []
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
let currentDisplayFormat = 'chart';
const STALE_THRESHOLD_SECONDS = 180; // 3 minutes without new telemetry

const METRIC_CONFIGS = {
    temperature: {
        id: 'temperature',
        title: 'Temperature',
        unit: '°C',
        canvasId: 'chart-temperature',
        color: '#ef4444',
        rawBorder: '#f87171',
        rawBg: 'rgba(239, 68, 68, 0.12)',
        rollingBorder: '#991b1b',
        dotClass: 'temp-dot'
    },
    humidity: {
        id: 'humidity',
        title: 'Humidity',
        unit: '%',
        canvasId: 'chart-humidity',
        color: '#0284c7',
        rawBorder: '#38bdf8',
        rawBg: 'rgba(2, 132, 199, 0.12)',
        rollingBorder: '#075985',
        dotClass: 'hum-dot'
    },
    windspeed: {
        id: 'windspeed',
        title: 'Wind Speed',
        unit: 'm/s',
        canvasId: 'chart-windspeed',
        color: '#059669',
        rawBorder: '#34d399',
        rawBg: 'rgba(5, 150, 105, 0.12)',
        rollingBorder: '#064e3b',
        dotClass: 'wind-dot'
    }
};

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
            displayFormat: currentDisplayFormat
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

    // Restore saved preferences before initial queries
    const savedPrefs = loadPreferences();
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
    document.getElementById('limit-select').addEventListener('change', () => { savePreferences(); refreshAll(); });
    document.getElementById('toggleRaw').addEventListener('change', onToggleChange);
    document.getElementById('toggleRolling').addEventListener('change', onToggleChange);
    document.getElementById('rollingWindow').addEventListener('change', onToggleChange);

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
        text.textContent = 'Reconnecting...';
        if (btnReconnect) btnReconnect.style.display = 'inline-flex';
    } else if (status === 'connecting') {
        text.textContent = 'Connecting...';
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
    lastUpdatedTimer = setInterval(updateLastUpdatedDisplay, 1000);
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
        span.textContent = 'Last updated: --';
        if (badge && text) {
            badge.className = 'freshness-badge stale';
            text.textContent = 'Waiting';
        }
        return;
    }

    const diffSec = Math.max(0, Math.floor((Date.now() - lastUpdatedTime.getTime()) / 1000));
    if (diffSec < 2) {
        span.textContent = 'Last updated: just now';
    } else if (diffSec < 60) {
        span.textContent = `Last updated: ${diffSec}s ago`;
    } else if (diffSec < 3600) {
        span.textContent = `Last updated: ${Math.floor(diffSec / 60)}m ago`;
    } else {
        span.textContent = `Last updated: ${Math.floor(diffSec / 3600)}h ago`;
    }

    if (badge && text) {
        if (diffSec >= STALE_THRESHOLD_SECONDS) {
            badge.className = 'freshness-badge stale';
            const mins = Math.floor(diffSec / 60);
            text.textContent = mins > 0 ? `Stale (${mins}m)` : `Stale (${diffSec}s)`;
            badge.setAttribute('title', `Data is stale: no updates received for ${mins > 0 ? mins + 'm' : diffSec + 's'}`);
        } else {
            badge.className = 'freshness-badge fresh';
            text.textContent = 'Fresh';
            badge.setAttribute('title', 'Telemetry data is fresh and receiving live updates');
        }
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
 * Schedule a debounced/coalesced fetch of full-dataset statistics on live updates (S5)
 */
function scheduleCoalescedStatsRefresh() {
    if (statsRefreshTimer) return;
    statsRefreshTimer = setTimeout(async () => {
        statsRefreshTimer = null;
        try {
            const station = document.getElementById('station-select').value;
            const metric = document.getElementById('metric-select').value;
            const range = document.getElementById('range-select').value;
            let q = `?station_id=${encodeURIComponent(station)}&metric=${encodeURIComponent(metric)}`;
            if (range === 'limit') {
                const limit = document.getElementById('limit-select').value || 50;
                q += `&limit=${encodeURIComponent(limit)}`;
            } else {
                q += `&range=${encodeURIComponent(range)}`;
            }
            await fetchStats(q);
        } catch (e) {
            console.warn('[StatsRefresh] Coalesced refresh error:', e);
        }
    }, 1000);
}

/**
 * Handle new live reading received from SSE stream (S2, S3, S5)
 */
function handleLiveReading(reading) {
    const station = document.getElementById('station-select').value;
    if (!reading || reading.station_id !== station) {
        return;
    }

    lastUpdatedTime = new Date();
    updateLastUpdatedDisplay();

    const metricMode = document.getElementById('metric-select').value;
    const rangeSelect = document.getElementById('range-select').value;
    const limit = rangeSelect === 'limit' ? (parseInt(document.getElementById('limit-select').value, 10) || 50) : null;
    const windowSize = parseInt(document.getElementById('rollingWindow').value, 10) || 10;
    const rawEnabled = document.getElementById('toggleRaw').checked;
    const rollingEnabled = document.getElementById('toggleRolling').checked;

    if (metricMode === 'all') {
        const metrics = ['temperature', 'humidity', 'windspeed'];
        for (const m of metrics) {
            let val = reading[m];
            if (val === undefined && m === 'windspeed') val = reading.wind_speed;
            if (val === undefined || val === null) continue;

            const numVal = typeof val === 'number' ? val : parseFloat(val);
            const valStr = numVal.toFixed(2);

            // Update latest readings immediately
            if (m === 'temperature') {
                document.getElementById('stat-temp-latest').textContent = `${valStr} °C`;
                document.getElementById('mini-temp-latest').textContent = `${valStr} °C`;
                const heroTemp = document.getElementById('hero-quick-temp');
                if (heroTemp) heroTemp.textContent = `${valStr} °C`;
            } else if (m === 'humidity') {
                document.getElementById('stat-hum-latest').textContent = `${valStr} %`;
                document.getElementById('mini-hum-latest').textContent = `${valStr} %`;
                const heroHum = document.getElementById('hero-quick-hum');
                if (heroHum) heroHum.textContent = `${valStr} %`;
            } else if (m === 'windspeed') {
                document.getElementById('stat-wind-latest').textContent = `${valStr} m/s`;
                document.getElementById('mini-wind-latest').textContent = `${valStr} m/s`;
                const heroWind = document.getElementById('hero-quick-wind');
                if (heroWind) heroWind.textContent = `${valStr} m/s`;
            }

            multiSeries[m].push({ t: reading.timestamp, v: numVal });
            if (limit && multiSeries[m].length > limit) {
                while (multiSeries[m].length > limit) multiSeries[m].shift();
            }
        }

        // Recompute global bounds and update panels with gap/tooltip integrity (S1, S2, S3)
        refreshAllPanelsData(multiSeries, rawEnabled, rollingEnabled, windowSize, rangeSelect);
        scheduleCoalescedStatsRefresh(); // S5
    } else {
        // Single metric mode
        let val = reading[metricMode];
        if (val === undefined && (metricMode === 'windspeed' || metricMode === 'wind_speed')) {
            val = reading.windspeed !== undefined ? reading.windspeed : reading.wind_speed;
        }
        if (val === undefined || val === null) return;

        const numVal = typeof val === 'number' ? val : parseFloat(val);
        document.getElementById('stat-latest').textContent = numVal.toFixed(2);
        document.getElementById('stat-latest-time').textContent = formatFullTimestamp(reading.timestamp);

        currentRawPoints.push({ t: reading.timestamp, v: numVal });
        if (limit && currentRawPoints.length > limit) {
            while (currentRawPoints.length > limit) currentRawPoints.shift();
        }

        renderSingleChart(metricMode, currentRawPoints, rawEnabled, rollingEnabled, windowSize, rangeSelect);
        scheduleCoalescedStatsRefresh(); // S5
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
            select.innerHTML = '<option value="">No stations available</option>';
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
        document.getElementById('station-select').innerHTML = '<option value="">Error loading</option>';
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
    const multiPanels = document.getElementById('multi-panels-container');
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
    const multiPanels = document.getElementById('multi-panels-container');
    const singleChart = document.getElementById('chart-container');
    const tableContainer = document.getElementById('table-container');

    // Stats cards visibility
    if (metric === 'all') {
        if (multiStats) multiStats.style.display = 'grid';
        if (singleStats) singleStats.style.display = 'none';
    } else {
        if (multiStats) multiStats.style.display = 'none';
        if (singleStats) singleStats.style.display = 'grid';
    }

    // Format visibility
    if (format === 'table') {
        if (multiPanels) multiPanels.style.display = 'none';
        if (singleChart) singleChart.style.display = 'none';
        if (tableContainer) tableContainer.style.display = 'block';
    } else {
        if (tableContainer) tableContainer.style.display = 'none';
        if (metric === 'all') {
            if (multiPanels) multiPanels.style.display = 'flex';
            if (singleChart) singleChart.style.display = 'none';
        } else {
            if (multiPanels) multiPanels.style.display = 'none';
            if (singleChart) singleChart.style.display = 'block';
        }
    }
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
        if (contextStation) contextStation.textContent = station || 'None';

        const rangeSelectElem = document.getElementById('range-select');
        const contextRange = document.getElementById('context-range-label');
        if (contextRange && rangeSelectElem) {
            if (range === 'limit') {
                const limitVal = limitSelect ? limitSelect.value : 50;
                contextRange.textContent = `Last ${limitVal} values`;
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
        await Promise.all([
            fetchStats(queryParams, abortSignal, fetchId, station, metric, range),
            fetchReadings(queryParams, abortSignal, fetchId, station, metric, range)
        ]);

        if (fetchId !== currentFetchId) {
            console.log(`[Refresh #${fetchId}] Superceded by #${currentFetchId}, ignoring completion`);
            return;
        }

        lastUpdatedTime = new Date();
        updateLastUpdatedDisplay();
    } catch (err) {
        if (err.name === 'AbortError') {
            console.log(`[Refresh #${fetchId}] Aborted by newer request`);
            return;
        }
        console.error(`[Refresh #${fetchId}] Error:`, err);
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
            return;
        }
        const activeStation = document.getElementById('station-select')?.value;
        const activeMetric = document.getElementById('metric-select')?.value;
        const activeRange = document.getElementById('range-select')?.value;
        if (station !== activeStation || metric !== activeMetric || range !== activeRange) {
            console.log(`[fetchStats #${fetchId}] Selection changed while in-flight, dropping delayed response`);
            return;
        }

        if (metric === 'all') {
            updateMultiMetricStats(data);
        } else {
            updateSingleMetricStats(data, metric);
        }
    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('[Error] Failed to fetch stats:', error);
        showErrorBanner('Failed to fetch statistics. Please check server or database.');
    }
}

/**
 * Update stats cards for All Panels view
 */
function updateMultiMetricStats(data) {
    if (!data) return;

    // Temperature
    const t = data.temperature || {};
    document.getElementById('stat-temp-latest').textContent = t.latest !== null && t.latest !== undefined ? `${t.latest.toFixed(2)} °C` : '--';
    document.getElementById('stat-temp-avg').textContent = t.avg !== null && t.avg !== undefined ? `${t.avg.toFixed(2)} °C` : '--';
    document.getElementById('stat-temp-min').textContent = t.min !== null && t.min !== undefined ? `${t.min.toFixed(2)} °C` : '--';
    document.getElementById('stat-temp-max').textContent = t.max !== null && t.max !== undefined ? `${t.max.toFixed(2)} °C` : '--';

    document.getElementById('mini-temp-latest').textContent = t.latest !== null && t.latest !== undefined ? `${t.latest.toFixed(2)} °C` : '--';
    document.getElementById('mini-temp-avg').textContent = t.avg !== null && t.avg !== undefined ? `${t.avg.toFixed(2)} °C` : '--';
    document.getElementById('mini-temp-min').textContent = t.min !== null && t.min !== undefined ? `${t.min.toFixed(2)} °C` : '--';
    document.getElementById('mini-temp-max').textContent = t.max !== null && t.max !== undefined ? `${t.max.toFixed(2)} °C` : '--';

    // Humidity
    const h = data.humidity || {};
    document.getElementById('stat-hum-latest').textContent = h.latest !== null && h.latest !== undefined ? `${h.latest.toFixed(2)} %` : '--';
    document.getElementById('stat-hum-avg').textContent = h.avg !== null && h.avg !== undefined ? `${h.avg.toFixed(2)} %` : '--';
    document.getElementById('stat-hum-min').textContent = h.min !== null && h.min !== undefined ? `${h.min.toFixed(2)} %` : '--';
    document.getElementById('stat-hum-max').textContent = h.max !== null && h.max !== undefined ? `${h.max.toFixed(2)} %` : '--';

    document.getElementById('mini-hum-latest').textContent = h.latest !== null && h.latest !== undefined ? `${h.latest.toFixed(2)} %` : '--';
    document.getElementById('mini-hum-avg').textContent = h.avg !== null && h.avg !== undefined ? `${h.avg.toFixed(2)} %` : '--';
    document.getElementById('mini-hum-min').textContent = h.min !== null && h.min !== undefined ? `${h.min.toFixed(2)} %` : '--';
    document.getElementById('mini-hum-max').textContent = h.max !== null && h.max !== undefined ? `${h.max.toFixed(2)} %` : '--';

    // Wind Speed
    const w = data.windspeed || {};
    document.getElementById('stat-wind-latest').textContent = w.latest !== null && w.latest !== undefined ? `${w.latest.toFixed(2)} m/s` : '--';
    document.getElementById('stat-wind-avg').textContent = w.avg !== null && w.avg !== undefined ? `${w.avg.toFixed(2)} m/s` : '--';
    document.getElementById('stat-wind-min').textContent = w.min !== null && w.min !== undefined ? `${w.min.toFixed(2)} m/s` : '--';
    document.getElementById('stat-wind-max').textContent = w.max !== null && w.max !== undefined ? `${w.max.toFixed(2)} m/s` : '--';

    document.getElementById('mini-wind-latest').textContent = w.latest !== null && w.latest !== undefined ? `${w.latest.toFixed(2)} m/s` : '--';
    document.getElementById('mini-wind-avg').textContent = w.avg !== null && w.avg !== undefined ? `${w.avg.toFixed(2)} m/s` : '--';
    document.getElementById('mini-wind-min').textContent = w.min !== null && w.min !== undefined ? `${w.min.toFixed(2)} m/s` : '--';
    document.getElementById('mini-wind-max').textContent = w.max !== null && w.max !== undefined ? `${w.max.toFixed(2)} m/s` : '--';

    // Update Hero Bar quick readings
    const heroTemp = document.getElementById('hero-quick-temp');
    if (heroTemp) heroTemp.textContent = t.latest !== null && t.latest !== undefined ? `${t.latest.toFixed(2)} °C` : '--';
    const heroHum = document.getElementById('hero-quick-hum');
    if (heroHum) heroHum.textContent = h.latest !== null && h.latest !== undefined ? `${h.latest.toFixed(2)} %` : '--';
    const heroWind = document.getElementById('hero-quick-wind');
    if (heroWind) heroWind.textContent = w.latest !== null && w.latest !== undefined ? `${w.latest.toFixed(2)} m/s` : '--';
}

/**
 * Update single-metric stats cards
 */
function updateSingleMetricStats(data, metric) {
    const config = METRIC_CONFIGS[metric] || { unit: '' };
    const valFormatted = data.latest !== null && data.latest !== undefined ? `${data.latest.toFixed(2)} ${config.unit}` : '--';
    document.getElementById('stat-latest').textContent = valFormatted;
    document.getElementById('stat-latest-time').textContent = data.latest_t ? formatFullTimestamp(data.latest_t) : '';
    document.getElementById('stat-avg').textContent = data.avg !== null && data.avg !== undefined ? `${data.avg.toFixed(2)} ${config.unit}` : '--';
    document.getElementById('stat-min').textContent = data.min !== null && data.min !== undefined ? `${data.min.toFixed(2)} ${config.unit}` : '--';
    document.getElementById('stat-max').textContent = data.max !== null && data.max !== undefined ? `${data.max.toFixed(2)} ${config.unit}` : '--';

    // Update the selected metric in hero bar and clear unselected metrics to prevent stale station leakage
    const heroTemp = document.getElementById('hero-quick-temp');
    const heroHum = document.getElementById('hero-quick-hum');
    const heroWind = document.getElementById('hero-quick-wind');

    if (heroTemp) heroTemp.textContent = (metric === 'temperature') ? valFormatted : '--';
    if (heroHum) heroHum.textContent = (metric === 'humidity') ? valFormatted : '--';
    if (heroWind) heroWind.textContent = (metric === 'windspeed' || metric === 'wind_speed') ? valFormatted : '--';
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
            textEl.textContent = `No telemetry readings recorded for Station "${station}" in window "${rangeText}".`;
        }
        el.style.display = 'block';
    }
    document.getElementById('multi-panels-container').style.display = 'none';
    document.getElementById('chart-container').style.display = 'none';
    const tableCont = document.getElementById('table-container');
    if (tableCont) tableCont.style.display = 'none';
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
            return;
        }
        const activeStation = document.getElementById('station-select')?.value;
        const activeMetric = document.getElementById('metric-select')?.value;
        const activeRange = document.getElementById('range-select')?.value;
        if (station !== activeStation || metric !== activeMetric || range !== activeRange) {
            console.log(`[fetchReadings #${fetchId}] Selection changed while in-flight, dropping delayed response`);
            return;
        }

        const rawEnabled = document.getElementById('toggleRaw').checked;
        const rollingEnabled = document.getElementById('toggleRolling').checked;
        const windowSize = parseInt(document.getElementById('rollingWindow').value, 10) || 10;

        if (metric === 'all') {
            const series = data.series || {};
            const totalPoints = data.total_points || (data.points ? data.points.length : 0);

            multiSeries.temperature = series.temperature || [];
            multiSeries.humidity = series.humidity || [];
            multiSeries.windspeed = series.windspeed || [];

            updateMetaChips(totalPoints, totalPoints > 350);

            const hasPoints = (multiSeries.temperature.length > 0 || multiSeries.humidity.length > 0 || multiSeries.windspeed.length > 0);
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
    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('[Error] Failed to fetch readings:', error);
        showErrorBanner('Failed to fetch telemetry readings. Please check server or database.');
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
        countChip.textContent = `📊 Total: ${totalCount} readings`;
    }

    if (aggChip) {
        if (isAggregated) {
            aggChip.style.display = 'inline-flex';
            aggChip.textContent = '⚡ Downsampled (Significant Peaks & Troughs Preserved)';
        } else {
            aggChip.style.display = 'none';
        }
    }

    if (gapChip) {
        if (gapCount > 0) {
            gapChip.style.display = 'inline-flex';
            gapChip.textContent = `⚠️ ${gapCount} data gap${gapCount > 1 ? 's' : ''} detected`;
        } else {
            gapChip.style.display = 'none';
        }
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
    const metrics = ['temperature', 'humidity', 'windspeed'];

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
 * Render three vertically aligned panels for Temperature, Humidity, and Wind Speed
 * Using a shared continuous linear time axis (S1, S2, S3)
 */
function renderAlignedPanels(seriesMap, rawEnabled, rollingEnabled, windowSize, range) {
    let totalGaps = 0;
    let anyAggregated = false;
    const metrics = ['temperature', 'humidity', 'windspeed'];
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

        const datasets = [];

        if (rawEnabled) {
            datasets.push({
                id: 'raw',
                label: `Raw ${config.title} (${config.unit})`,
                data: rawData,
                borderColor: config.rawBorder,
                backgroundColor: config.rawBg,
                borderWidth: 2,
                fill: true,
                tension: 0.2,
                spanGaps: false, // Ensures missing data renders as explicit line breaks (S4)
                pointRadius: rawData.length > 150 ? 1 : 3,
                pointHoverRadius: 5,
                pointBackgroundColor: config.color,
                pointBorderColor: '#ffffff',
                pointBorderWidth: 1,
            });
        }

        if (rollingEnabled && rollingData.length > 0) {
            datasets.push({
                id: 'rolling',
                label: `Rolling Avg (${windowSize})`,
                data: rollingData,
                borderColor: config.rollingBorder,
                backgroundColor: 'transparent',
                borderWidth: 2.5,
                borderDash: [6, 3],
                fill: false,
                tension: 0.25,
                spanGaps: false,
                pointRadius: 0,
                pointHoverRadius: 4,
            });
        }

        charts[m] = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: datasets
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: {
                    mode: 'nearest',
                    axis: 'x',
                    intersect: false,
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: {
                            boxWidth: 14,
                            padding: 10,
                            font: { size: 11, weight: '600' }
                        }
                    },
                    tooltip: {
                        enabled: true,
                        backgroundColor: 'rgba(15, 23, 42, 0.94)',
                        titleColor: '#f8fafc',
                        bodyColor: '#e2e8f0',
                        borderColor: 'rgba(255, 255, 255, 0.1)',
                        borderWidth: 1,
                        padding: 10,
                        cornerRadius: 8,
                        callbacks: {
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
                                    return `${context.dataset.label}: [Missing / Gap]`;
                                }

                                let line = `${context.dataset.label}: ${context.parsed.y.toFixed(2)} ${config.unit}`;
                                if (pt.aggregated && context.dataset.id === 'raw') {
                                    line += ` (Bucket: min ${pt.min}, max ${pt.max}, avg ${pt.avg})`;
                                }
                                return line;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: false,
                        grid: { color: 'rgba(226, 232, 240, 0.7)' },
                        title: {
                            display: true,
                            text: config.unit,
                            color: '#64748b',
                            font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', size: 11, weight: 'bold' }
                        },
                        ticks: {
                            color: '#64748b',
                            font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', size: 11 }
                        }
                    },
                    x: {
                        type: 'linear',
                        min: minTime,
                        max: maxTime,
                        grid: { display: false },
                        ticks: {
                            color: '#64748b',
                            autoSkip: true,
                            maxTicksLimit: 8,
                            maxRotation: 0,
                            font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', size: 11 },
                            callback: function(value) {
                                return formatAxisTime(new Date(value).toISOString(), range);
                            }
                        }
                    }
                }
            }
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
        title: metric.charAt(0).toUpperCase() + metric.slice(1),
        unit: '',
        color: '#667eea',
        rawBorder: '#667eea',
        rawBg: 'rgba(102, 126, 234, 0.1)',
        rollingBorder: '#1e293b'
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

    const datasets = [];

    if (rawEnabled) {
        datasets.push({
            id: 'raw',
            label: `Raw ${config.title} (${config.unit})`,
            data: rawData,
            borderColor: config.rawBorder,
            backgroundColor: config.rawBg,
            borderWidth: 2.5,
            fill: true,
            tension: 0.25,
            spanGaps: false,
            pointRadius: rawData.length > 200 ? 1 : 3,
            pointHoverRadius: 6,
            pointBackgroundColor: config.color,
            pointBorderColor: '#ffffff',
            pointBorderWidth: 1.5,
        });
    }

    if (rollingEnabled && rollingData.length > 0) {
        datasets.push({
            id: 'rolling',
            label: `Rolling Avg (${windowSize})`,
            data: rollingData,
            borderColor: config.rollingBorder,
            backgroundColor: 'transparent',
            borderWidth: 3,
            borderDash: [6, 4],
            fill: false,
            tension: 0.3,
            spanGaps: false,
            pointRadius: 0,
            pointHoverRadius: 5,
        });
    }

    ctx.style.display = 'block';
    const chartLoading = document.getElementById('chart-loading');
    if (chartLoading) chartLoading.style.display = 'none';

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false,
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { padding: 15, font: { size: 12, weight: 'bold' } }
                },
                tooltip: {
                    enabled: true,
                    backgroundColor: 'rgba(15, 23, 42, 0.94)',
                    titleColor: '#f8fafc',
                    bodyColor: '#e2e8f0',
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
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
                                return `${context.dataset.label}: [Missing / Gap]`;
                            }

                            let line = `${context.dataset.label}: ${context.parsed.y.toFixed(2)} ${config.unit}`;
                            if (pt.aggregated && context.dataset.id === 'raw') {
                                line += ` (Bucket: min ${pt.min}, max ${pt.max}, avg ${pt.avg})`;
                            }
                            return line;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: false,
                    grid: { color: 'rgba(226, 232, 240, 0.7)' },
                    title: {
                        display: true,
                        text: config.unit,
                        color: '#64748b',
                        font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', size: 11, weight: 'bold' }
                    },
                    ticks: {
                        color: '#64748b',
                        font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', size: 11 }
                    }
                },
                x: {
                    type: 'linear',
                    min: minTime,
                    max: maxTime,
                    grid: { display: false },
                    ticks: {
                        color: '#64748b',
                        autoSkip: true,
                        maxTicksLimit: 10,
                        maxRotation: 0,
                        font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', size: 11 },
                        callback: function(value) {
                            return formatAxisTime(new Date(value).toISOString(), range);
                        }
                    }
                }
            }
        }
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
            <th scope="col">Timestamp (UTC)</th>
            <th scope="col">Temperature (°C)</th>
            <th scope="col">Humidity (%)</th>
            <th scope="col">Wind Speed (m/s)</th>
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

        const sortedTimestamps = Array.from(timeMap.keys()).sort();
        if (summaryChip) summaryChip.textContent = `${sortedTimestamps.length} records`;
        if (captionEl) captionEl.textContent = `Weather telemetry records for all metrics: ${sortedTimestamps.length} entries`;

        if (sortedTimestamps.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding: 24px; color: var(--text-muted);">No records to display</td></tr>`;
            return;
        }

        tbody.innerHTML = sortedTimestamps.map(t => {
            const row = timeMap.get(t);
            const tempStr = (row.temp !== undefined && row.temp !== null) ? row.temp.toFixed(2) : '--';
            const humStr = (row.hum !== undefined && row.hum !== null) ? row.hum.toFixed(2) : '--';
            const windStr = (row.wind !== undefined && row.wind !== null) ? row.wind.toFixed(2) : '--';
            return `<tr>
                <th scope="row">${formatFullTimestamp(t)}</th>
                <td>${tempStr}</td>
                <td>${humStr}</td>
                <td>${windStr}</td>
            </tr>`;
        }).join('');
    } else {
        const config = METRIC_CONFIGS[metricMode] || { title: metricMode, unit: '' };
        theadRow.innerHTML = `
            <th scope="col">Timestamp (UTC)</th>
            <th scope="col">${config.title} (${config.unit})</th>
        `;

        const points = currentRawPoints || [];
        if (summaryChip) summaryChip.textContent = `${points.length} records`;
        if (captionEl) captionEl.textContent = `Weather telemetry records for ${config.title}: ${points.length} entries`;

        if (points.length === 0) {
            tbody.innerHTML = `<tr><td colspan="2" style="text-align:center; padding: 24px; color: var(--text-muted);">No records to display</td></tr>`;
            return;
        }

        tbody.innerHTML = points.map(p => {
            const valStr = (p.v !== undefined && p.v !== null) ? p.v.toFixed(2) : 'N/A';
            return `<tr>
                <th scope="row">${formatFullTimestamp(p.t)}</th>
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
        csv = 'Timestamp,Temperature_C,Humidity_pct,WindSpeed_ms\n';
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
        const sortedTimestamps = Array.from(timeMap.keys()).sort();
        for (const t of sortedTimestamps) {
            const r = timeMap.get(t);
            csv += `"${t}",${r.temp ?? ''},${r.hum ?? ''},${r.wind ?? ''}\n`;
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
                btn.innerHTML = `<span aria-hidden="true">✓</span> Copied!`;
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
    document.getElementById('stats-container').style.display = 'none';
    document.getElementById('multi-stats-container').style.display = 'none';
    document.getElementById('multi-panels-container').style.display = 'none';
    document.getElementById('chart-container').style.display = 'none';
    const tableCont = document.getElementById('table-container');
    if (tableCont) tableCont.style.display = 'none';

    const heroTemp = document.getElementById('hero-quick-temp');
    if (heroTemp) heroTemp.textContent = '--';
    const heroHum = document.getElementById('hero-quick-hum');
    if (heroHum) heroHum.textContent = '--';
    const heroWind = document.getElementById('hero-quick-wind');
    if (heroWind) heroWind.textContent = '--';
    const contextStation = document.getElementById('context-station-name');
    if (contextStation) contextStation.textContent = 'None';
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
            hour12: true
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
