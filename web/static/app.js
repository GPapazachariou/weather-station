let chart = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    console.log('[Init] Loading dashboard...');
    
    // Load stations first
    await loadStations();
    
    // Setup event listeners
    document.getElementById('station-select').addEventListener('change', refreshAll);
    document.getElementById('metric-select').addEventListener('change', refreshAll);
    document.getElementById('range-select').addEventListener('change', onRangeChange);
    document.getElementById('limit-select').addEventListener('change', refreshAll);
    document.getElementById('toggleRaw').addEventListener('change', refreshAll);
    document.getElementById('toggleRolling').addEventListener('change', refreshAll);
    document.getElementById('rollingWindow').addEventListener('change', refreshAll);
    
    // Initial refresh
    await refreshAll();
});

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
        
        // Populate dropdown
        select.innerHTML = stations
            .map(s => `<option value="${s}">${s}</option>`)
            .join('');
        
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
 * Refresh stats and chart when selection changes
 */
async function refreshAll() {
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
 * Compute rolling average over a trailing window.
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
