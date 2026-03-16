"""Debug page for state machine inspection and testing."""

DEBUG_PAGE_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Debug - REMOTE CTRL</title>
    <script src="/static/vendor/tailwind.js"></script>
    <link rel="stylesheet" href="/static/shared.css?v=3">
    <script>(function(){ var t=localStorage.getItem('rdc_theme')||'default'; document.documentElement.setAttribute('data-theme',t); })()</script>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>
        mermaid.initialize({ 
            startOnLoad: false, 
            theme: 'dark',
            themeVariables: {
                primaryColor: '#3b82f6',
                primaryTextColor: '#fff',
                primaryBorderColor: '#60a5fa',
                lineColor: '#6b7280',
                secondaryColor: '#1f2937',
                tertiaryColor: '#374151'
            }
        });
    </script>
    <style>
        .state-tree { font-family: monospace; font-size: 12px; }
        .state-key { color: #60a5fa; }
        .state-value { color: #34d399; }
        .state-null { color: #9ca3af; }
        .state-number { color: #fbbf24; }
        .state-string { color: #f472b6; }
        .tab-active { border-color: #3b82f6; color: #fff; }
        .tab-inactive { border-color: transparent; color: #9ca3af; }
        .session-item { transition: all 0.2s; }
        .session-item:hover { background-color: #374151; }
        .session-item.selected { background-color: #1e40af; }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen p-4">
    <div class="max-w-7xl mx-auto">
        <!-- Header -->
        <div class="flex items-center justify-between mb-4">
            <div>
                <h1 class="text-xl font-bold">RDC Debug Console</h1>
            </div>
            <div class="flex items-center gap-3">
                <input type="password" id="auth-token" 
                       class="bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-sm w-48" 
                       placeholder="Auth token"
                       onchange="saveToken(this.value)">
                <div id="connection-status" class="flex items-center gap-2 text-sm">
                    <span class="w-2 h-2 rounded-full bg-red-500"></span>
                    <span>Disconnected</span>
                </div>
                <button id="connect-btn" onclick="connect()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded text-sm">Connect</button>
                <button id="disconnect-btn" onclick="disconnect()" class="hidden px-3 py-1.5 bg-red-600 hover:bg-red-700 rounded text-sm">Disconnect</button>
                <a href="/" class="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm">Dashboard</a>
            </div>
        </div>
        
        <!-- Event Timeline (always visible at top) -->
        <div class="mb-4 bg-gray-800 rounded-lg p-3">
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-3">
                    <h2 class="font-semibold">Event Timeline</h2>
                    <div class="flex bg-gray-900 rounded p-0.5 text-xs">
                        <button id="mode-live" onclick="setTimelineMode('live')" class="px-2 py-1 rounded bg-green-600 text-white">● Live</button>
                        <button id="mode-history" onclick="setTimelineMode('history')" class="px-2 py-1 rounded text-gray-400 hover:text-white">Historical</button>
                    </div>
                    <div id="live-indicator" class="flex items-center gap-1 text-green-400 text-xs">
                        <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                        <span>Streaming</span>
                    </div>
                </div>
                <div class="flex items-center gap-2 text-xs text-gray-400">
                    <div id="history-controls" class="hidden items-center gap-2">
                        <select id="timeline-range" onchange="setTimelineRange(this.value)" class="bg-gray-700 rounded px-2 py-1 text-xs">
                            <option value="5">5 min</option>
                            <option value="30" selected>30 min</option>
                            <option value="60">1 hour</option>
                        </select>
                        <button onclick="loadHistoricalEvents()" class="px-2 py-1 bg-blue-700 rounded">Load</button>
                    </div>
                    <span class="text-green-400">● sent</span>
                    <span class="text-purple-400">● received</span>
                    <button onclick="clearEventLog()" class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded">Clear</button>
                </div>
            </div>
            <div class="bg-gray-900 rounded h-24 relative overflow-hidden">
                <canvas id="timeline-canvas" class="w-full h-full"></canvas>
                <div id="timeline-tooltip" class="hidden absolute bg-gray-800 border border-gray-600 rounded p-2 text-xs z-10 shadow-lg"></div>
            </div>
        </div>
        
        <!-- Main Tabs -->
        <div class="bg-gray-800 rounded-lg">
            <!-- Tab Headers -->
            <div class="flex border-b border-gray-700">
                <button onclick="showMainTab('server')" class="main-tab px-4 py-2 border-b-2 tab-active" data-tab="server">
                    Server State
                </button>
                <button onclick="showMainTab('sessions')" class="main-tab px-4 py-2 border-b-2 tab-inactive" data-tab="sessions">
                    Sessions <span id="session-count" class="ml-1 px-1.5 py-0.5 bg-gray-700 rounded text-xs">0</span>
                </button>
                <button onclick="showMainTab('events')" class="main-tab px-4 py-2 border-b-2 tab-inactive" data-tab="events">
                    Event Console
                </button>
            </div>
            
            <!-- Tab Content -->
            <div class="p-4">
                <!-- Server Tab -->
                <div id="tab-server" class="tab-content">
                    <div class="grid grid-cols-2 gap-4">
                        <!-- Server State Diagram -->
                        <div>
                            <div class="flex items-center justify-between mb-3">
                                <div class="flex items-center gap-2">
                                    <span class="font-medium">State Machine</span>
                                    <span id="server-state-badge" class="px-2 py-0.5 bg-green-600 rounded text-xs font-mono">--</span>
                                </div>
                                <button onclick="loadStateDiagram()" class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs">Refresh</button>
                            </div>
                            <div id="server-diagram" class="bg-gray-900 rounded p-3 overflow-auto" style="min-height: 300px;">
                                <div class="text-gray-500 text-sm">Loading...</div>
                            </div>
                        </div>
                        <!-- Server State JSON -->
                        <div>
                            <div class="flex items-center justify-between mb-3">
                                <span class="font-medium">State Data</span>
                                <span id="state-timestamp" class="text-xs text-gray-500">--</span>
                            </div>
                            <div class="flex gap-1 mb-2 text-xs">
                                <button onclick="showStateFilter('all')" class="state-filter px-2 py-1 bg-blue-600 rounded" data-filter="all">All</button>
                                <button onclick="showStateFilter('tasks')" class="state-filter px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded" data-filter="tasks">Tasks</button>
                                <button onclick="showStateFilter('processes')" class="state-filter px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded" data-filter="processes">Processes</button>
                                <button onclick="showStateFilter('agents')" class="state-filter px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded" data-filter="agents">Agents</button>
                            </div>
                            <div id="state-tree" class="state-tree bg-gray-900 rounded p-3 overflow-auto" style="min-height: 280px; max-height: 400px;">
                                <div class="text-gray-500 text-sm">Connect to see state...</div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Sessions Tab -->
                <div id="tab-sessions" class="tab-content hidden">
                    <div class="grid grid-cols-3 gap-4">
                        <!-- Session List -->
                        <div>
                            <div class="flex items-center justify-between mb-3">
                                <span class="font-medium">Connected Clients</span>
                            </div>
                            <div id="sessions-list" class="bg-gray-900 rounded p-2 space-y-1 overflow-auto" style="min-height: 350px; max-height: 450px;">
                                <div class="text-gray-500 text-sm p-2">No sessions connected</div>
                            </div>
                        </div>
                        <!-- Selected Session Diagram -->
                        <div class="col-span-2">
                            <div class="flex items-center justify-between mb-3">
                                <div class="flex items-center gap-2">
                                    <span class="font-medium">Session State</span>
                                    <span id="selected-session-name" class="text-cyan-400 text-sm">Select a session</span>
                                </div>
                                <span id="selected-session-badge" class="px-2 py-0.5 bg-gray-700 rounded text-xs font-mono">--</span>
                            </div>
                            <div id="session-diagram" class="bg-gray-900 rounded p-3 overflow-auto" style="min-height: 350px;">
                                <div class="text-gray-500 text-sm">Click a session to view its state machine</div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Events Tab -->
                <div id="tab-events" class="tab-content hidden">
                    <div class="grid grid-cols-2 gap-4">
                        <!-- Event Sender -->
                        <div>
                            <div class="mb-3 font-medium">Send Event</div>
                            <div class="bg-gray-900 rounded p-3">
                                <div class="grid grid-cols-2 gap-2 mb-2">
                                    <select id="event-type" class="bg-gray-800 border border-gray-600 rounded px-2 py-1.5 text-sm">
                                        <optgroup label="Session">
                                            <option value="select_project">select_project</option>
                                        </optgroup>
                                        <optgroup label="Task">
                                            <option value="task_create">task_create</option>
                                            <option value="task_start">task_start</option>
                                            <option value="task_complete">task_complete</option>
                                            <option value="task_cancel">task_cancel</option>
                                        </optgroup>
                                        <optgroup label="Process">
                                            <option value="process_start">process_start</option>
                                            <option value="process_stop">process_stop</option>
                                        </optgroup>
                                        <optgroup label="Agent">
                                            <option value="agent_spawn">agent_spawn</option>
                                            <option value="agent_stop">agent_stop</option>
                                        </optgroup>
                                    </select>
                                    <button onclick="sendEvent()" class="bg-green-600 hover:bg-green-700 rounded px-3 py-1.5 text-sm">Send</button>
                                </div>
                                <textarea id="event-data" class="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1.5 text-sm font-mono h-24" placeholder='{"project": "documaker"}'>{}</textarea>
                                <div class="mt-2 flex flex-wrap gap-1">
                                    <button onclick="quickEvent('select_project', {project: 'documaker'})" class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs">Select Project</button>
                                    <button onclick="quickEvent('task_create', {project: 'documaker', description: 'Test'})" class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs">Create Task</button>
                                    <button onclick="quickEvent('process_start', {process_id: 'test-app'})" class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs">Start Process</button>
                                </div>
                            </div>
                        </div>
                        <!-- Event Log -->
                        <div>
                            <div class="flex items-center justify-between mb-3">
                                <span class="font-medium">Event Log</span>
                                <button onclick="clearEventLog()" class="text-xs text-gray-500 hover:text-gray-300">Clear</button>
                            </div>
                            <div id="event-log" class="bg-gray-900 rounded p-2 space-y-1 overflow-auto" style="min-height: 300px; max-height: 400px;">
                                <div class="text-gray-500 text-sm p-2">No events yet...</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

<script>
let ws = null;
let sessionId = null;
let currentState = null;
let currentMainTab = 'server';
let currentStateFilter = 'all';
let selectedSessionId = null;
let eventLog = [];
let historicalEvents = [];
let timelineRangeMinutes = 30;
let timelineMode = 'live';
let mermaidCounter = 0;

// Token management
function saveToken(token) { localStorage.setItem('rdc_token', token); }
function getToken() { return localStorage.getItem('rdc_token') || ''; }

// WebSocket connection
function connect() {
    const token = getToken();
    if (!token) { alert('Please enter auth token'); return; }
    
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/state?token=${encodeURIComponent(token)}`);
    
    ws.onopen = () => {
        updateConnectionStatus(true);
        loadStateDiagram();
    };
    
    ws.onclose = () => updateConnectionStatus(false);
    ws.onerror = () => updateConnectionStatus(false);
    
    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        
        if (message.type === 'state') {
            if (message.session_id) sessionId = message.session_id;
            currentState = message.data;
            renderState();
            updateServerBadge();
            if (currentState.sessions) updateSessionsList(currentState.sessions);
        } else if (message.type === 'event') {
            logEvent(message.event_type, message.data, message.direction, message.client_id, message.client_name);
            highlightEventFlow(message.event_type);
        } else if (message.type === 'event_result') {
            logEvent(`result: ${message.event}`, message.result, 'received', message.client_id);
        }
    };
}

function disconnect() {
    if (ws) { ws.close(); ws = null; }
    updateConnectionStatus(false);
}

function updateConnectionStatus(connected) {
    const status = document.getElementById('connection-status');
    const connectBtn = document.getElementById('connect-btn');
    const disconnectBtn = document.getElementById('disconnect-btn');
    
    if (connected) {
        status.innerHTML = '<span class="w-2 h-2 rounded-full bg-green-500"></span><span>Connected</span>';
        connectBtn.classList.add('hidden');
        disconnectBtn.classList.remove('hidden');
    } else {
        status.innerHTML = '<span class="w-2 h-2 rounded-full bg-red-500"></span><span>Disconnected</span>';
        connectBtn.classList.remove('hidden');
        disconnectBtn.classList.add('hidden');
    }
}

function updateServerBadge() {
    const badge = document.getElementById('server-state-badge');
    if (badge && currentState?.server_state) {
        badge.textContent = currentState.server_state;
    }
}

// Main tab switching
function showMainTab(tab) {
    currentMainTab = tab;
    document.querySelectorAll('.main-tab').forEach(el => {
        el.classList.toggle('tab-active', el.dataset.tab === tab);
        el.classList.toggle('tab-inactive', el.dataset.tab !== tab);
    });
    document.querySelectorAll('.tab-content').forEach(el => {
        el.classList.toggle('hidden', el.id !== `tab-${tab}`);
    });
}

// State filter
function showStateFilter(filter) {
    currentStateFilter = filter;
    document.querySelectorAll('.state-filter').forEach(el => {
        el.classList.toggle('bg-blue-600', el.dataset.filter === filter);
        el.classList.toggle('bg-gray-700', el.dataset.filter !== filter);
    });
    renderState();
}

// Render state tree
function renderState() {
    const container = document.getElementById('state-tree');
    const timestamp = document.getElementById('state-timestamp');
    if (!currentState) return;
    
    timestamp.textContent = new Date(currentState.timestamp).toLocaleTimeString();
    
    let data = currentState;
    if (currentStateFilter !== 'all') {
        data = { [currentStateFilter]: currentState[currentStateFilter] };
    }
    
    container.innerHTML = renderValue(data, 0);
}

function renderValue(val, depth) {
    if (val === null || val === undefined) return '<span class="state-null">null</span>';
    if (typeof val === 'number') return `<span class="state-number">${val}</span>`;
    if (typeof val === 'boolean') return `<span class="state-number">${val}</span>`;
    if (typeof val === 'string') return `<span class="state-string">"${escapeHtml(val.slice(0, 100))}"</span>`;
    
    if (Array.isArray(val)) {
        if (val.length === 0) return '<span class="state-null">[]</span>';
        const items = val.slice(0, 20).map((v, i) => `<div style="margin-left: ${(depth + 1) * 12}px">${renderValue(v, depth + 1)}</div>`).join('');
        const more = val.length > 20 ? `<div style="margin-left: ${(depth + 1) * 12}px" class="state-null">... ${val.length - 20} more</div>` : '';
        return `[${items}${more}]`;
    }
    
    if (typeof val === 'object') {
        const entries = Object.entries(val).slice(0, 30);
        const items = entries.map(([k, v]) => `<div style="margin-left: ${(depth + 1) * 12}px"><span class="state-key">${k}:</span> ${renderValue(v, depth + 1)}</div>`).join('');
        return `{${items}}`;
    }
    
    return String(val);
}

function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Sessions
function updateSessionsList(sessions) {
    const container = document.getElementById('sessions-list');
    const countEl = document.getElementById('session-count');
    if (!container) return;
    
    countEl.textContent = sessions.length;
    
    if (sessions.length === 0) {
        container.innerHTML = '<div class="text-gray-500 text-sm p-2">No sessions connected</div>';
        return;
    }
    
    container.innerHTML = sessions.map(s => {
        const stateColors = { connected: 'bg-green-600', authenticated: 'bg-blue-600', working: 'bg-yellow-600', idle: 'bg-gray-600' };
        const color = stateColors[s.state] || 'bg-gray-600';
        const name = s.client_name || s.client_id || s.id;
        const isSelected = s.id === selectedSessionId;
        
        return `
            <div onclick="selectSession('${s.id}')" 
                 class="session-item p-2 rounded cursor-pointer ${isSelected ? 'selected' : 'bg-gray-800'}" data-session="${s.id}">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full ${color}"></span>
                        <span class="text-cyan-400 text-sm font-mono">${name}</span>
                    </div>
                    <span class="text-xs ${color} px-1.5 py-0.5 rounded">${s.state}</span>
                </div>
                ${s.project ? `<div class="text-xs text-purple-400 mt-1 ml-4">${s.project}</div>` : ''}
            </div>
        `;
    }).join('');
}

function selectSession(id) {
    selectedSessionId = id;
    document.querySelectorAll('.session-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.session === id);
        el.classList.toggle('bg-gray-800', el.dataset.session !== id);
    });
    loadSessionDiagram(id);
}

// Mermaid rendering
async function renderMermaid(container, diagram, currentState) {
    let enhanced = diagram;
    if (currentState) {
        enhanced += `\\n    classDef current fill:#22c55e,stroke:#16a34a,color:#fff`;
        enhanced += `\\n    class ${currentState} current`;
    }
    
    try {
        const id = 'mermaid-' + (++mermaidCounter);
        const { svg } = await mermaid.render(id, enhanced);
        container.innerHTML = svg;
        const svgEl = container.querySelector('svg');
        if (svgEl) { svgEl.style.maxWidth = '100%'; svgEl.style.height = 'auto'; }
    } catch (e) {
        container.innerHTML = '<pre class="text-xs text-gray-400">' + escapeHtml(diagram) + '</pre>';
    }
}

async function loadStateDiagram() {
    const container = document.getElementById('server-diagram');
    try {
        const resp = await fetch('/debug/diagram', { headers: { 'Authorization': `Bearer ${getToken()}` } });
        if (!resp.ok) { container.innerHTML = '<div class="text-red-400 text-sm">Auth required</div>'; return; }
        const data = await resp.json();
        await renderMermaid(container, data.diagram, data.current_state);
        if (data.sessions) updateSessionsList(data.sessions);
    } catch (e) {
        container.innerHTML = '<div class="text-red-400 text-sm">Error loading diagram</div>';
    }
}

async function loadSessionDiagram(sessionId) {
    const container = document.getElementById('session-diagram');
    const nameEl = document.getElementById('selected-session-name');
    const badgeEl = document.getElementById('selected-session-badge');
    
    if (!sessionId) {
        container.innerHTML = '<div class="text-gray-500 text-sm">Click a session to view its state</div>';
        return;
    }
    
    try {
        const resp = await fetch(`/debug/session/${sessionId}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
        if (!resp.ok) { container.innerHTML = '<div class="text-red-400 text-sm">Session not found</div>'; return; }
        const data = await resp.json();
        
        nameEl.textContent = data.client_name || data.id;
        badgeEl.textContent = data.state;
        
        if (data.diagram) {
            await renderMermaid(container, data.diagram, data.state);
        } else {
            container.innerHTML = `
                <div class="space-y-3 text-sm">
                    <div class="flex justify-between"><span class="text-gray-500">State:</span><span class="text-green-400">${data.state}</span></div>
                    <div class="flex justify-between"><span class="text-gray-500">Project:</span><span class="text-purple-400">${data.project || 'none'}</span></div>
                    <div class="flex justify-between"><span class="text-gray-500">Terminal:</span><span class="text-blue-400">${data.terminal_project || 'none'}</span></div>
                    <div class="flex justify-between"><span class="text-gray-500">Preview:</span><span class="text-yellow-400">${data.preview_process || 'none'}</span></div>
                </div>
            `;
        }
    } catch (e) {
        container.innerHTML = '<div class="text-red-400 text-sm">Error loading session</div>';
    }
}

// Event handling
function logEvent(type, data, direction, clientId = null, clientName = null) {
    eventLog.unshift({ type, data, direction, clientId: clientId || 'server', clientName: clientName || clientId || 'server', time: new Date().toISOString() });
    if (eventLog.length > 100) eventLog.pop();
    renderEventLog();
    renderTimeline();
}

function clearEventLog() {
    eventLog = [];
    renderEventLog();
    renderTimeline();
}

function renderEventLog() {
    const container = document.getElementById('event-log');
    if (!container) return;
    
    if (eventLog.length === 0) {
        container.innerHTML = '<div class="text-gray-500 text-sm p-2">No events yet...</div>';
        return;
    }
    
    const colors = ['text-cyan-400', 'text-amber-400', 'text-rose-400', 'text-lime-400', 'text-indigo-400'];
    function clientColor(id) {
        if (!id || id === 'server') return 'text-gray-400';
        let hash = 0;
        for (let i = 0; i < id.length; i++) hash = id.charCodeAt(i) + ((hash << 5) - hash);
        return colors[Math.abs(hash) % colors.length];
    }
    
    container.innerHTML = eventLog.slice(0, 50).map(e => {
        const dirIcon = e.direction === 'sent' ? '↑' : e.direction === 'received' ? '↓' : '•';
        const dirColor = e.direction === 'sent' ? 'text-green-400' : e.direction === 'received' ? 'text-purple-400' : 'text-gray-400';
        const time = e.time.split('T')[1].split('.')[0];
        
        return `
            <div class="p-2 bg-gray-800 rounded text-xs">
                <div class="flex justify-between items-center">
                    <div class="flex items-center gap-2">
                        <span class="${dirColor}">${dirIcon}</span>
                        <span class="font-medium">${e.type}</span>
                        <span class="${clientColor(e.clientId)} px-1 bg-gray-700 rounded">${e.clientName}</span>
                    </div>
                    <span class="text-gray-500">${time}</span>
                </div>
                ${Object.keys(e.data || {}).length > 0 ? `<pre class="text-gray-400 mt-1 overflow-x-auto">${JSON.stringify(e.data, null, 1)}</pre>` : ''}
            </div>
        `;
    }).join('');
}

function sendEvent() {
    if (!ws || ws.readyState !== WebSocket.OPEN) { alert('Not connected'); return; }
    const type = document.getElementById('event-type').value;
    let data = {};
    try { data = JSON.parse(document.getElementById('event-data').value || '{}'); } catch { alert('Invalid JSON'); return; }
    ws.send(JSON.stringify({ type, data }));
    logEvent(type, data, 'sent');
}

function quickEvent(type, data) {
    if (!ws || ws.readyState !== WebSocket.OPEN) { alert('Not connected'); return; }
    ws.send(JSON.stringify({ type, data }));
    logEvent(type, data, 'sent');
}

// Event flow highlight
let highlightTimeout = null;
function highlightEventFlow(eventType) {
    const badge = document.getElementById('server-state-badge');
    if (!badge) return;
    badge.classList.add('bg-yellow-500');
    badge.classList.remove('bg-green-600');
    clearTimeout(highlightTimeout);
    highlightTimeout = setTimeout(() => {
        badge.classList.remove('bg-yellow-500');
        badge.classList.add('bg-green-600');
    }, 300);
    
    if (['process_start', 'process_stop', 'task_create', 'task_complete', 'agent_spawn', 'agent_stop'].includes(eventType)) {
        setTimeout(() => loadStateDiagram(), 500);
    }
}

// Timeline
function setTimelineMode(mode) {
    timelineMode = mode;
    document.getElementById('mode-live').className = mode === 'live' ? 'px-2 py-1 rounded bg-green-600 text-white' : 'px-2 py-1 rounded text-gray-400';
    document.getElementById('mode-history').className = mode === 'history' ? 'px-2 py-1 rounded bg-blue-600 text-white' : 'px-2 py-1 rounded text-gray-400';
    document.getElementById('history-controls').classList.toggle('hidden', mode === 'live');
    document.getElementById('history-controls').classList.toggle('flex', mode !== 'live');
    document.getElementById('live-indicator').classList.toggle('hidden', mode !== 'live');
    if (mode === 'history') loadHistoricalEvents();
    renderTimeline();
}

function setTimelineRange(minutes) { timelineRangeMinutes = parseInt(minutes); if (timelineMode === 'history') loadHistoricalEvents(); }

async function loadHistoricalEvents() {
    try {
        const resp = await fetch(`/events?minutes=${timelineRangeMinutes}&limit=500`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
        if (!resp.ok) return;
        const data = await resp.json();
        historicalEvents = (data.events || []).map(e => ({ type: e.event_type, data: e.data || {}, direction: e.direction || 'system', time: e.timestamp, clientId: e.client_id, clientName: e.client_name }));
        renderTimeline();
    } catch (e) { console.error('Error loading events:', e); }
}

function renderTimeline() {
    const canvas = document.getElementById('timeline-canvas');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = rect.height * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    
    const width = rect.width, height = rect.height;
    ctx.fillStyle = '#111827';
    ctx.fillRect(0, 0, width, height);
    
    const events = timelineMode === 'live' ? eventLog : [...historicalEvents, ...eventLog];
    if (events.length === 0) {
        ctx.fillStyle = '#6b7280';
        ctx.font = '12px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('Waiting for events...', width / 2, height / 2);
        return;
    }
    
    const now = Date.now();
    const timeSpan = timelineMode === 'live' ? 2 * 60 * 1000 : timelineRangeMinutes * 60 * 1000;
    const startTime = now - timeSpan;
    
    // Draw axis
    ctx.strokeStyle = '#374151';
    ctx.beginPath();
    ctx.moveTo(40, height - 20);
    ctx.lineTo(width - 10, height - 20);
    ctx.stroke();
    
    // Draw events
    window.timelineEventPositions = [];
    const colors = { sent: '#22c55e', received: '#a855f7', system: '#6b7280' };
    
    events.forEach(e => {
        const t = new Date(e.time).getTime();
        if (t < startTime) return;
        const x = 40 + ((t - startTime) / timeSpan) * (width - 50);
        const y = height / 2;
        
        ctx.fillStyle = colors[e.direction] || colors.system;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        
        window.timelineEventPositions.push({ x, y, event: e });
    });
}

function initTimelineHover() {
    const canvas = document.getElementById('timeline-canvas');
    const tooltip = document.getElementById('timeline-tooltip');
    if (!canvas || !tooltip) return;
    
    canvas.addEventListener('mousemove', (e) => {
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left, y = e.clientY - rect.top;
        let closest = null, closestDist = Infinity;
        
        (window.timelineEventPositions || []).forEach(pos => {
            const dist = Math.sqrt((pos.x - x) ** 2 + (pos.y - y) ** 2);
            if (dist < 15 && dist < closestDist) { closest = pos; closestDist = dist; }
        });
        
        if (closest) {
            const ev = closest.event;
            const time = new Date(ev.time).toLocaleTimeString();
            const client = ev.clientName || ev.clientId || 'server';
            tooltip.innerHTML = `<div class="font-semibold">${ev.type}</div><div class="text-gray-400">${time}</div><div class="text-cyan-400">${client}</div>`;
            tooltip.style.left = Math.min(e.clientX - rect.left + 10, rect.width - 150) + 'px';
            tooltip.style.top = (e.clientY - rect.top - 10) + 'px';
            tooltip.classList.remove('hidden');
        } else {
            tooltip.classList.add('hidden');
        }
    });
    
    canvas.addEventListener('mouseleave', () => tooltip.classList.add('hidden'));
}

// Init
window.onload = () => {
    const token = getToken();
    if (token) { document.getElementById('auth-token').value = token; connect(); }
    initTimelineHover();
    renderTimeline();
    loadStateDiagram();
    window.addEventListener('resize', () => renderTimeline());
};
</script>
</body>
</html>
'''
