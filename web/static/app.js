let chart = null; // Legacy single-chart handle for backward compatibility
const charts = {
    temperature: null,
    humidity: null,
    windspeed: null,
    single: null
};

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
 * Handle toggle or rolling window changes without full network fetch if data exists
 */
function onToggleChange() {
    refreshAll();
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

            // Update multi-stat cards & mini stats
            if (m === 'temperature') {
                document.getElementById('stat-temp-latest').textContent = `${valStr} °C`;
                document.getElementById('mini-temp-latest').textContent = `${valStr} °C`;
            } else if (m === 'humidity') {
                document.getElementById('stat-hum-latest').textContent = `${valStr} %`;
                document.getElementById('mini-hum-latest').textContent = `${valStr} %`;
            } else if (m === 'windspeed') {
                document.getElementById('stat-wind-latest').textContent = `${valStr} m/s`;
                document.getElementById('mini-wind-latest').textContent = `${valStr} m/s`;
            }

            multiSeries[m].push({ t: reading.timestamp, v: numVal });
            if (limit && multiSeries[m].length > limit) {
                while (multiSeries[m].length > limit) multiSeries[m].shift();
            }

            // Update chart dynamically
            const c = charts[m];
            if (c && c.data) {
                updateChartDatasets(c, multiSeries[m], rawEnabled, rollingEnabled, windowSize, rangeSelect);
            }
        }
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

        const c = charts.single || chart;
        if (c && c.data) {
            updateChartDatasets(c, currentRawPoints, rawEnabled, rollingEnabled, windowSize, rangeSelect);
        }
    }
}

/**
 * Dynamically update Chart datasets without tearing down the instance
 */
function updateChartDatasets(chartInstance, points, rawEnabled, rollingEnabled, windowSize, range) {
    const formattedLabels = points.map(p => formatAxisTime(p.t, range));
    chartInstance.data.labels = formattedLabels;

    const rawDs = chartInstance.data.datasets.find(ds => ds.id === 'raw');
    if (rawDs) {
        rawDs.data = rawEnabled ? points.map(p => p.v) : [];
    }

    const rollingDs = chartInstance.data.datasets.find(ds => ds.id === 'rolling');
    if (rollingDs) {
        if (rollingEnabled) {
            const rawVals = points.map(p => p.v);
            rollingDs.data = computeRollingValues(rawVals, windowSize);
        } else {
            rollingDs.data = [];
        }
    }

    chartInstance.update('none');
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

        select.replaceChildren(...stations.map(s => new Option(s, s)));
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
        const range = document.getElementById('range-select').value;
        const limitSelect = document.getElementById('limit-select');

        if (!station) {
            showEmptyState();
            return;
        }

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

        // Toggle visibility of multi-stats vs single-stats containers
        if (metric === 'all') {
            document.getElementById('multi-stats-container').style.display = 'grid';
            document.getElementById('stats-container').style.display = 'none';
            document.getElementById('multi-panels-container').style.display = 'flex';
            document.getElementById('chart-container').style.display = 'none';
        } else {
            document.getElementById('multi-stats-container').style.display = 'none';
            document.getElementById('stats-container').style.display = 'grid';
            document.getElementById('multi-panels-container').style.display = 'none';
            document.getElementById('chart-container').style.display = 'block';
        }

        // Fetch stats and readings in parallel
        await Promise.all([
            fetchStats(queryParams),
            fetchReadings(queryParams)
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
 * Fetch and display stats from full dataset
 */
async function fetchStats(queryParams) {
    try {
        const url = `/api/stats${queryParams}`;
        const response = await fetch(url);
        const data = await response.json();
        const metric = new URLSearchParams(queryParams).get('metric');

        if (metric === 'all') {
            updateMultiMetricStats(data);
        } else {
            updateSingleMetricStats(data, metric);
        }
    } catch (error) {
        console.error('[Error] Failed to fetch stats:', error);
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
}

/**
 * Update single-metric stats cards
 */
function updateSingleMetricStats(data, metric) {
    const config = METRIC_CONFIGS[metric] || { unit: '' };
    document.getElementById('stat-latest').textContent = data.latest !== null && data.latest !== undefined ? `${data.latest.toFixed(2)} ${config.unit}` : '--';
    document.getElementById('stat-latest-time').textContent = data.latest_t ? formatFullTimestamp(data.latest_t) : '';
    document.getElementById('stat-avg').textContent = data.avg !== null && data.avg !== undefined ? `${data.avg.toFixed(2)} ${config.unit}` : '--';
    document.getElementById('stat-min').textContent = data.min !== null && data.min !== undefined ? `${data.min.toFixed(2)} ${config.unit}` : '--';
    document.getElementById('stat-max').textContent = data.max !== null && data.max !== undefined ? `${data.max.toFixed(2)} ${config.unit}` : '--';
}

/**
 * Fetch and display readings for all panels or single panel
 */
async function fetchReadings(queryParams) {
    try {
        const urlParams = new URLSearchParams(queryParams);
        const metric = urlParams.get('metric');
        const range = urlParams.get('range') || '24h';

        // Include downsampling param for fast long-history transmission
        const downsampleParam = '&downsample=350';
        const url = `/api/readings${queryParams}${downsampleParam}`;
        const response = await fetch(url);
        const data = await response.json();

        const rawEnabled = document.getElementById('toggleRaw').checked;
        const rollingEnabled = document.getElementById('toggleRolling').checked;
        const windowSize = parseInt(document.getElementById('rollingWindow').value, 10) || 10;

        if (metric === 'all') {
            const series = data.series || {};
            const totalPoints = data.total_points || (data.points ? data.points.length : 0);

            multiSeries.temperature = series.temperature || [];
            multiSeries.humidity = series.humidity || [];
            multiSeries.windspeed = series.windspeed || [];

            updateMetaChips(totalPoints, data.aggregated);

            if (typeof Chart !== 'undefined') {
                renderAlignedPanels(multiSeries, rawEnabled, rollingEnabled, windowSize, range);
            } else {
                renderTable(multiSeries.temperature || []);
            }
        } else {
            const points = data.points || [];
            currentRawPoints = points;

            updateMetaChips(data.total_points || points.length, data.aggregated);

            if (typeof Chart !== 'undefined') {
                renderSingleChart(metric, points, rawEnabled, rollingEnabled, windowSize, range);
            } else {
                renderTable(points);
            }
        }
    } catch (error) {
        console.error('[Error] Failed to fetch readings:', error);
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
 * Detect missing intervals in time series and insert null gap points with spanGaps: false
 */
function insertMissingGaps(points) {
    if (!points || points.length < 2) return { points: points || [], gapCount: 0 };

    const deltas = [];
    for (let i = 0; i < points.length - 1; i++) {
        const t1 = new Date(points[i].t).getTime();
        const t2 = new Date(points[i + 1].t).getTime();
        const diff = t2 - t1;
        if (diff > 0) deltas.push(diff);
    }
    if (deltas.length === 0) return { points, gapCount: 0 };

    deltas.sort((a, b) => a - b);
    const medianDelta = deltas[Math.floor(deltas.length / 2)];
    // Threshold: 3x median interval and at least 15 minutes
    const threshold = Math.max(medianDelta * 3, 15 * 60 * 1000);

    const result = [];
    let gapCount = 0;
    for (let i = 0; i < points.length; i++) {
        result.push(points[i]);
        if (i < points.length - 1) {
            const t1 = new Date(points[i].t).getTime();
            const t2 = new Date(points[i + 1].t).getTime();
            if ((t2 - t1) > threshold) {
                gapCount++;
                const midIso = new Date(t1 + Math.floor((t2 - t1) / 2)).toISOString();
                result.push({ t: midIso, v: null, isGap: true });
            }
        }
    }
    return { points: result, gapCount };
}

/**
 * Render three vertically aligned panels for Temperature, Humidity, and Wind Speed
 */
function renderAlignedPanels(seriesMap, rawEnabled, rollingEnabled, windowSize, range) {
    let totalGaps = 0;
    const metrics = ['temperature', 'humidity', 'windspeed'];

    // Identify common time labels across all 3 metrics for alignment
    const primarySeries = seriesMap.temperature.length ? seriesMap.temperature :
                         (seriesMap.humidity.length ? seriesMap.humidity : seriesMap.windspeed);
    const alignedTimestamps = primarySeries.map(p => p.t);
    const alignedLabels = alignedTimestamps.map(t => formatAxisTime(t, range));

    for (const m of metrics) {
        const rawPoints = seriesMap[m] || [];
        const { points: processedPoints, gapCount } = insertMissingGaps(rawPoints);
        totalGaps = Math.max(totalGaps, gapCount);

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
                data: processedPoints.map(p => p.v),
                borderColor: config.rawBorder,
                backgroundColor: config.rawBg,
                borderWidth: 2,
                fill: true,
                tension: 0.2,
                spanGaps: false, // Ensures missing data renders as visible gaps
                pointRadius: processedPoints.length > 150 ? 1 : 3,
                pointHoverRadius: 5,
                pointBackgroundColor: config.color,
                pointBorderColor: '#ffffff',
                pointBorderWidth: 1,
            });
        }

        if (rollingEnabled && processedPoints.length > 0) {
            const rawVals = processedPoints.map(p => p.v);
            const rollingVals = computeRollingValues(rawVals, windowSize);
            datasets.push({
                id: 'rolling',
                label: `Rolling Avg (${windowSize})`,
                data: rollingVals,
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

        // Maintain references for custom tooltip details
        const pointsReference = processedPoints;

        charts[m] = new Chart(ctx, {
            type: 'line',
            data: {
                labels: alignedLabels,
                datasets: datasets
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: {
                    mode: 'index',
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
                                const idx = tooltipItems[0].dataIndex;
                                const pt = pointsReference[idx];
                                return pt ? formatFullTimestamp(pt.t) : tooltipItems[0].label;
                            },
                            label: (context) => {
                                const idx = context.dataIndex;
                                const pt = pointsReference[idx];
                                if (!pt || pt.v === null) return `${context.dataset.label}: [Missing / Gap]`;

                                let line = `${context.dataset.label}: ${context.parsed.y.toFixed(2)} ${config.unit}`;
                                if (pt.aggregated && context.dataset.id === 'raw') {
                                    line += ` (Bucket of ${pt.count}: min ${pt.min}, max ${pt.max}, avg ${pt.avg})`;
                                }
                                return line;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: false,
                        grid: { color: 'rgba(0, 0, 0, 0.05)' },
                        title: {
                            display: true,
                            text: config.unit,
                            font: { size: 11, weight: 'bold' }
                        },
                        ticks: {
                            font: { size: 10 }
                        }
                    },
                    x: {
                        grid: { display: false },
                        ticks: {
                            autoSkip: true,
                            maxTicksLimit: 8,
                            maxRotation: 0,
                            font: { size: 10 }
                        }
                    }
                }
            }
        });
    }

    if (totalGaps > 0) {
        updateMetaChips(primarySeries.length, false, totalGaps);
    }
}

/**
 * Render single chart when an individual metric is chosen
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

    const { points: processedPoints, gapCount } = insertMissingGaps(points);
    if (gapCount > 0) {
        updateMetaChips(points.length, false, gapCount);
    }

    const labels = processedPoints.map(p => formatAxisTime(p.t, range));
    const datasets = [];

    if (rawEnabled) {
        datasets.push({
            id: 'raw',
            label: `Raw ${config.title} (${config.unit})`,
            data: processedPoints.map(p => p.v),
            borderColor: config.rawBorder,
            backgroundColor: config.rawBg,
            borderWidth: 2.5,
            fill: true,
            tension: 0.25,
            spanGaps: false,
            pointRadius: processedPoints.length > 200 ? 1 : 3,
            pointHoverRadius: 6,
            pointBackgroundColor: config.color,
            pointBorderColor: '#ffffff',
            pointBorderWidth: 1.5,
        });
    }

    if (rollingEnabled && processedPoints.length > 0) {
        const rawVals = processedPoints.map(p => p.v);
        datasets.push({
            id: 'rolling',
            label: `Rolling Avg (${windowSize})`,
            data: computeRollingValues(rawVals, windowSize),
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

    const pointsReference = processedPoints;

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: {
                mode: 'index',
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
                            const idx = tooltipItems[0].dataIndex;
                            const pt = pointsReference[idx];
                            return pt ? formatFullTimestamp(pt.t) : tooltipItems[0].label;
                        },
                        label: (context) => {
                            const idx = context.dataIndex;
                            const pt = pointsReference[idx];
                            if (!pt || pt.v === null) return `${context.dataset.label}: [Missing / Gap]`;

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
                    grid: { color: 'rgba(0, 0, 0, 0.05)' },
                    title: { display: true, text: config.unit }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        autoSkip: true,
                        maxTicksLimit: 10,
                        maxRotation: 0
                    }
                }
            }
        }
    });

    charts.single = chart;
}

/**
 * Compute rolling average over an array of numeric values.
 */
function computeRollingValues(values, windowSize) {
    if (!values || values.length === 0) return [];
    if (windowSize <= 1) return [...values];
    const result = [];
    let sum = 0;
    let count = 0;
    const window = [];

    for (let i = 0; i < values.length; i++) {
        const val = values[i];
        if (val !== null && val !== undefined && !isNaN(val)) {
            window.push(val);
            sum += val;
            count++;
            if (window.length > windowSize) {
                sum -= window.shift();
                count--;
            }
            result.push(sum / count);
        } else {
            result.push(null);
        }
    }
    return result;
}

/**
 * Render fallback table
 */
function renderTable(points) {
    const tbody = document.getElementById('table-body');
    if (!tbody) return;
    tbody.innerHTML = points
        .map(p => `
            <tr>
                <td>${formatFullTimestamp(p.t)}</td>
                <td>${p.v !== null && p.v !== undefined ? p.v.toFixed(2) : 'N/A'}</td>
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
    document.getElementById('multi-stats-container').style.display = 'none';
    document.getElementById('multi-panels-container').style.display = 'none';
    document.getElementById('chart-container').style.display = 'none';
    const tableCont = document.getElementById('table-container');
    if (tableCont) tableCont.style.display = 'none';
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
