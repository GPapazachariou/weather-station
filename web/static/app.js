let chart = null;
let currentRawPoints = [];
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

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    console.log('[Init] Loading dashboard...');

    startLastUpdatedTimer();

    // Load stations first
    await loadStations();

    // Setup event listeners
    document.getElementById('station-select').addEventListener('change', onStationChange);
    document.getElementById('metric-select').addEventListener('change', onMetricChange);
    document.getElementById('range-select').addEventListener('change', onRangeChange);
    document.getElementById('limit-select').addEventListener('change', refreshAll);
    document.getElementById('toggleRaw').addEventListener('change', refreshAll);
    document.getElementById('toggleRolling').addEventListener('change', refreshAll);
    document.getElementById('rollingWindow').addEventListener('change', refreshAll);

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
    lastReceivedId = null;
    closeLiveStream();
    await refreshAll();
    initLiveStream();
}

/**
 * Handle metric change: re-fetch data for new metric
 */
async function onMetricChange() {
    await refreshAll();
}

/**
 * Set visual connection status badge
 */
function setLiveStatus(status) {
    const badge = document.getElementById('live-indicator');
    const text = document.getElementById('live-status-text');
    if (!badge || !text) return;

    badge.className = `live-badge ${status}`;
    if (status === 'connected') {
        text.textContent = 'Live';
    } else if (status === 'reconnecting') {
        text.textContent = 'Reconnecting...';
    } else if (status === 'connecting') {
        text.textContent = 'Connecting...';
    } else {
        text.textContent = 'Offline';
    }
}

/**
 * Start 1-second interval timer for "Last updated: X seconds ago"
 */
function startLastUpdatedTimer() {
    if (lastUpdatedTimer) clearInterval(lastUpdatedTimer);
    lastUpdatedTimer = setInterval(updateLastUpdatedDisplay, 1000);
}

/**
 * Update the text showing when data was last updated
 */
function updateLastUpdatedDisplay() {
    const span = document.getElementById('last-updated');
    if (!span) return;
    if (!lastUpdatedTime) {
        span.textContent = 'Last updated: --';
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
            // Explicitly close errored EventSource so backoff schedule handles reconnection cleanly
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
 * Handle new live reading received from SSE stream
 */
function handleLiveReading(reading) {
    const station = document.getElementById('station-select').value;
    if (!reading || reading.station_id !== station) {
        return;
    }

    lastUpdatedTime = new Date();
    updateLastUpdatedDisplay();

    const metric = document.getElementById('metric-select').value;
    let val = reading[metric];
    if (val === undefined && metric === 'windspeed') {
        val = reading.wind_speed;
    }
    if (val === undefined || val === null) {
        return;
    }

    // Update Latest stat card
    document.getElementById('stat-latest').textContent = typeof val === 'number' ? val.toFixed(2) : val;
    document.getElementById('stat-latest-time').textContent = formatTime(reading.timestamp);

    // Maintain raw series array in state
    currentRawPoints.push({ t: reading.timestamp, v: val });

    // Handle limit pruning if Last N Values is active
    const rangeSelect = document.getElementById('range-select').value;
    if (rangeSelect === 'limit') {
        const limit = parseInt(document.getElementById('limit-select').value, 10) || 50;
        while (currentRawPoints.length > limit) {
            currentRawPoints.shift();
        }
    }

    // If Chart.js chart exists, update datasets by role
    if (chart && chart.data) {
        chart.data.labels = currentRawPoints.map(p => formatTime(p.t));

        const rawDataset = chart.data.datasets.find(ds => ds.id === 'raw');
        if (rawDataset) {
            rawDataset.data = currentRawPoints.map(p => p.v);
        }

        const rollingDataset = chart.data.datasets.find(ds => ds.id === 'rolling');
        if (rollingDataset) {
            const windowSize = parseInt(document.getElementById('rollingWindow').value, 10) || 10;
            const rawVals = currentRawPoints.map(p => p.v);
            rollingDataset.data = computeRollingValues(rawVals, windowSize);
        }

        chart.update('none');

        // Update summary stats (avg, min, max) from raw data points
        updateStatsFromRawPoints();
    }
}

/**
 * Update summary stats (avg, min, max) from raw data points
 */
function updateStatsFromRawPoints() {
    if (!currentRawPoints || currentRawPoints.length === 0) return;
    const values = currentRawPoints.map(p => p.v).filter(v => v !== null && !isNaN(v));
    if (values.length === 0) return;

    const sum = values.reduce((acc, curr) => acc + curr, 0);
    const avg = sum / values.length;
    const min = Math.min(...values);
    const max = Math.max(...values);

    document.getElementById('stat-avg').textContent = avg.toFixed(2);
    document.getElementById('stat-min').textContent = min.toFixed(2);
    document.getElementById('stat-max').textContent = max.toFixed(2);
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

        if (stations.length === 0) {
            console.warn('[Stations] No stations found');
            select.innerHTML = '<option value="">No stations available</option>';
            showEmptyState();
            return;
        }

        // Populate dropdown safely using DOM API (prevents stored XSS)
        select.replaceChildren(...stations.map(s => new Option(s, s)));

        // Select first station by default
        select.value = stations[0];
        console.log(`[Stations] Loaded ${stations.length} stations, selected: ${stations[0]}`);

    } catch (error) {
        console.error('[Error] Failed to load stations:', error);
        document.getElementById('station-select').innerHTML = '<option value="">Error loading</option>';
        showEmptyState();
    }
}

/**
 * Handle range selector change - show/hide limit selector
 */
function onRangeChange() {
    const rangeSelect = document.getElementById('range-select').value;
    const limitSelect = document.getElementById('limit-select');

    if (rangeSelect === 'limit') {
        limitSelect.style.display = 'inline-block';
    } else {
        limitSelect.style.display = 'none';
    }

    refreshAll();
}

/**
 * Refresh stats and chart when selection changes (with in-flight request guard and coalescing)
 */
async function refreshAll() {
    if (isRefreshing) {
        pendingRefresh = true;
        console.log('[Refresh] Request already in flight, marked pending refresh');
        return;
    }
    isRefreshing = true;

    try {
        const station = document.getElementById('station-select').value;
        const metric = document.getElementById('metric-select').value;
        const rangeSelect = document.getElementById('range-select');
        const range = rangeSelect.value;
        const limitSelect = document.getElementById('limit-select');

        if (!station) {
            showEmptyState();
            return;
        }

        // Build query params
        let queryParams = `?station_id=${encodeURIComponent(station)}&metric=${encodeURIComponent(metric)}`;
        if (range === 'limit') {
            const limit = limitSelect.value;
            if (!limit) {
                showEmptyState();
                return;
            }
            queryParams += `&limit=${encodeURIComponent(limit)}`;
            console.log(`[Refresh] ${station} / ${metric} / last ${limit} values`);
        } else {
            queryParams += `&range=${encodeURIComponent(range)}`;
            console.log(`[Refresh] ${station} / ${metric} / ${range}`);
        }

        // Hide empty state
        document.getElementById('empty-state').style.display = 'none';
        document.getElementById('stats-container').style.display = 'grid';

        // Store params for API calls
        const apiParams = queryParams;

        // Fetch both in parallel
        await Promise.all([
            fetchStats(apiParams),
            fetchReadings(apiParams)
        ]);

        lastUpdatedTime = new Date();
        updateLastUpdatedDisplay();
    } finally {
        isRefreshing = false;
        if (pendingRefresh) {
            pendingRefresh = false;
            console.log('[Refresh] Running coalesced pending refresh');
            refreshAll();
        }
    }
}

/**
 * Fetch and display stats
 */
async function fetchStats(queryParams) {
    try {
        const url = `/api/stats${queryParams}`;
        const response = await fetch(url);
        const data = await response.json();

        // Update stat cards
        document.getElementById('stat-latest').textContent = data.latest !== null ? data.latest : '--';
        document.getElementById('stat-latest-time').textContent = data.latest_t ? formatTime(data.latest_t) : '';
        document.getElementById('stat-avg').textContent = data.avg !== null ? data.avg : '--';
        document.getElementById('stat-min').textContent = data.min !== null ? data.min : '--';
        document.getElementById('stat-max').textContent = data.max !== null ? data.max : '--';

        console.log(`[Stats] Latest=${data.latest}, Avg=${data.avg}`);
    } catch (error) {
        console.error('[Error] Failed to fetch stats:', error);
        document.getElementById('stat-latest').textContent = '--';
        document.getElementById('stat-avg').textContent = '--';
        document.getElementById('stat-min').textContent = '--';
        document.getElementById('stat-max').textContent = '--';
    }
}

/**
 * Fetch and display readings (chart or table)
 */
async function fetchReadings(queryParams) {
    try {
        const url = `/api/readings${queryParams}`;
        const response = await fetch(url);
        const data = await response.json();
        const points = data.points || [];

        console.log(`[Readings] Fetched ${points.length} points`);
        currentRawPoints = points.map(p => ({ t: p.t, v: p.v }));

        if (points.length === 0) {
            document.getElementById('chart-container').style.display = 'none';
            document.getElementById('table-container').style.display = 'none';
            document.getElementById('chart-loading').style.display = 'block';
            return;
        }

        // Get metric from URL params for chart rendering
        const metric = new URLSearchParams(queryParams).get('metric');

        // Prepare rolling average if enabled
        const rawEnabled = document.getElementById('toggleRaw').checked;
        const rollingEnabled = document.getElementById('toggleRolling').checked;
        const windowSize = parseInt(document.getElementById('rollingWindow').value, 10) || 10;
        const rollingPoints = rollingEnabled ? rollingAverage(points, windowSize) : [];

        // If both raw and rolling are disabled, nothing to show
        if (!rawEnabled && (!rollingEnabled || rollingPoints.length === 0)) {
            document.getElementById('chart-container').style.display = 'none';
            document.getElementById('table-container').style.display = 'none';
            document.getElementById('chart-loading').style.display = 'block';
            return;
        }

        // Try to render chart
        if (typeof Chart !== 'undefined') {
            renderChart(metric, points, rawEnabled, rollingEnabled, rollingPoints, windowSize);
            document.getElementById('chart-container').style.display = 'block';
            document.getElementById('table-container').style.display = 'none';
        } else {
            // Fallback to table
            renderTable(points);
            document.getElementById('chart-container').style.display = 'none';
            document.getElementById('table-container').style.display = 'block';
        }

    } catch (error) {
        console.error('[Error] Failed to fetch readings:', error);
        document.getElementById('chart-container').style.display = 'none';
        document.getElementById('table-container').style.display = 'none';
    }
}

/**
 * Render Chart.js line chart
 */
function renderChart(metric, points, rawEnabled, rollingEnabled, rollingPoints, windowSize) {
    const ctx = document.getElementById('data-chart');

    if (chart) {
        chart.destroy();
    }

    // Determine metric-specific styling
    let borderColor = '#667eea';
    let backgroundColor = 'rgba(102, 126, 234, 0.1)';
    let unit = '';

    if (metric === 'temperature') {
        borderColor = '#ff6b6b';
        backgroundColor = 'rgba(255, 107, 107, 0.1)';
        unit = '°C';
    } else if (metric === 'humidity') {
        borderColor = '#4dabf7';
        backgroundColor = 'rgba(77, 171, 247, 0.1)';
        unit = '%';
    } else if (metric === 'windspeed') {
        borderColor = '#51cf66';
        backgroundColor = 'rgba(81, 207, 102, 0.1)';
        unit = 'm/s';
    }

    const labels = points.map(p => formatTime(p.t));
    const values = points.map(p => p.v);
    const metricLabel = metric.charAt(0).toUpperCase() + metric.slice(1);

    ctx.style.display = 'block';
    document.getElementById('chart-loading').style.display = 'none';

    const datasets = [];

    if (rawEnabled) {
        datasets.push({
            id: 'raw',
            label: `${metricLabel} (${unit})`,
            data: values,
            borderColor: borderColor,
            backgroundColor: backgroundColor,
            borderWidth: 2.5,
            fill: true,
            tension: 0.3,
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: borderColor,
            pointBorderColor: 'white',
            pointBorderWidth: 1,
        });
    }

    if (rollingEnabled && rollingPoints.length > 0) {
        datasets.push({
            id: 'rolling',
            label: `Rolling Avg (${windowSize})`,
            data: rollingPoints.map(p => p.v),
            borderColor: '#1f2937', // dark neutral for contrast
            backgroundColor: 'transparent',
            borderWidth: 3,
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            borderDash: [6, 4],
        });
    }

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { padding: 15, font: { size: 12, weight: 'bold' } }
                },
                filler: { propagate: true }
            },
            scales: {
                y: {
                    beginAtZero: false,
                    grid: { color: 'rgba(0, 0, 0, 0.05)' },
                    title: { display: true, text: unit }
                },
                x: {
                    grid: { display: false }
                }
            }
        }
    });
}

/**
 * Compute rolling average over a trailing window of point objects.
 */
function rollingAverage(points, windowSize) {
    if (!points || points.length === 0) return [];
    if (windowSize <= 1) return points;
    const result = [];
    let sum = 0;
    for (let i = 0; i < points.length; i++) {
        sum += points[i].v;
        if (i >= windowSize) {
            sum -= points[i - windowSize].v;
        }
        const count = Math.min(windowSize, i + 1);
        result.push({ t: points[i].t, v: sum / count });
    }
    return result;
}

/**
 * Compute rolling average over an array of numeric values.
 */
function computeRollingValues(values, windowSize) {
    if (!values || values.length === 0) return [];
    if (windowSize <= 1) return [...values];
    const result = [];
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
        sum += values[i];
        if (i >= windowSize) {
            sum -= values[i - windowSize];
        }
        const count = Math.min(windowSize, i + 1);
        result.push(sum / count);
    }
    return result;
}

/**
 * Render fallback table
 */
function renderTable(points) {
    const tbody = document.getElementById('table-body');
    tbody.innerHTML = points
        .map(p => `
            <tr>
                <td>${formatTime(p.t)}</td>
                <td>${p.v.toFixed(2)}</td>
            </tr>
        `)
        .join('');
}

/**
 * Show empty state message
 */
function showEmptyState() {
    document.getElementById('empty-state').style.display = 'block';
    document.getElementById('stats-container').style.display = 'none';
    document.getElementById('chart-container').style.display = 'none';
    document.getElementById('table-container').style.display = 'none';
}

/**
 * Format timestamp for display
 */
function formatTime(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleString('en-US', {
            month: 'short',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: true
        });
    } catch (e) {
        return isoString;
    }
}
