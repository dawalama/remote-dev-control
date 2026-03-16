"""Collections management page for RDC Command Center."""

COLLECTIONS_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Collections - REMOTE CTRL</title>
    <script src="/static/vendor/tailwind.js"></script>
    <link rel="stylesheet" href="/static/shared.css?v=3">
    <script>(function(){ var t=localStorage.getItem('rdc_theme')||'default'; document.documentElement.setAttribute('data-theme',t); })()</script>
    <style>
        .fade-in { animation: fadeIn 0.3s ease-in; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        .collection-card { transition: all 0.2s; }
        .collection-card:hover { background: rgba(255,255,255,0.05); }
        .project-pill { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 9999px; font-size: 13px; background: rgba(255,255,255,0.08); }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">
    <div id="app" class="container mx-auto px-4 py-6 max-w-4xl">
        <div class="flex justify-between items-center mb-6">
            <div>
                <h1 class="text-2xl font-bold">Collections</h1>
                <p class="text-gray-400 text-sm">Group projects by client, theme, or workflow</p>
            </div>
            <a href="/" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Back to Dashboard</a>
        </div>

        <!-- Create collection -->
        <div class="bg-gray-800 rounded-lg p-4 mb-6">
            <h2 class="text-sm font-semibold text-gray-400 mb-3">New Collection</h2>
            <div class="flex gap-3">
                <input id="new-name" type="text" placeholder="Collection name" class="flex-1 bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm focus:border-blue-500 outline-none">
                <input id="new-desc" type="text" placeholder="Description (optional)" class="flex-1 bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm focus:border-blue-500 outline-none">
                <button onclick="createCollection()" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium">Create</button>
            </div>
        </div>

        <!-- Collections list -->
        <div id="collections-list" class="space-y-4">
            <div class="text-gray-400">Loading...</div>
        </div>
    </div>

<script>
const authToken = localStorage.getItem('rdc_token') || '';

async function api(path, options = {}) {
    const resp = await fetch(path, {
        ...options,
        headers: {
            'Authorization': `Bearer ${authToken}`,
            'Content-Type': 'application/json',
            ...(options.headers || {}),
        },
    });
    if (resp.status === 401) { window.location.href = '/'; return null; }
    return resp.json();
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

let collections = [];
let projects = [];
let expandedCollection = null;

async function loadData() {
    const [colls, projs] = await Promise.all([api('/collections'), api('/projects')]);
    if (Array.isArray(colls)) collections = colls;
    if (Array.isArray(projs)) projects = projs;
    renderAll();
}

function renderAll() {
    const container = document.getElementById('collections-list');
    if (collections.length === 0) {
        container.innerHTML = '<div class="text-gray-400">No collections yet.</div>';
        return;
    }

    container.innerHTML = collections.map(c => {
        const isExpanded = expandedCollection === c.id;
        const collProjects = projects.filter(p => p.collection_id === c.id);
        const isGeneral = c.id === 'general';

        return `
            <div class="bg-gray-800 rounded-lg overflow-hidden collection-card">
                <div class="p-4 flex items-center justify-between cursor-pointer" onclick="toggleCollection('${esc(c.id)}')">
                    <div class="flex items-center gap-3">
                        <span class="text-lg">${isExpanded ? '&#9660;' : '&#9654;'}</span>
                        <div>
                            <span class="font-semibold">${esc(c.name)}</span>
                            <span class="text-gray-400 text-sm ml-2">${c.project_count || 0} project${(c.project_count || 0) !== 1 ? 's' : ''}</span>
                            ${c.description ? `<div class="text-gray-500 text-xs mt-0.5">${esc(c.description)}</div>` : ''}
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        ${!isGeneral ? `
                            <button onclick="event.stopPropagation(); editCollection('${esc(c.id)}')" class="px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs">Edit</button>
                            <button onclick="event.stopPropagation(); deleteCollection('${esc(c.id)}', '${esc(c.name)}')" class="px-3 py-1 bg-red-600 hover:bg-red-700 rounded text-xs text-white">Delete</button>
                        ` : ''}
                    </div>
                </div>
                ${isExpanded ? `
                    <div class="border-t border-gray-700 p-4">
                        ${collProjects.length === 0 ? '<div class="text-gray-500 text-sm">No projects in this collection</div>' : `
                            <div class="flex flex-wrap gap-2">
                                ${collProjects.map(p => `
                                    <div class="project-pill">
                                        <span>${esc(p.name)}</span>
                                        <select onchange="moveProject('${esc(p.name)}', this.value)" class="bg-transparent text-xs text-gray-400 outline-none cursor-pointer ml-1" style="max-width:120px;">
                                            <option value="">Move to...</option>
                                            ${collections.filter(cc => cc.id !== c.id).map(cc => `<option value="${esc(cc.id)}">${esc(cc.name)}</option>`).join('')}
                                        </select>
                                    </div>
                                `).join('')}
                            </div>
                        `}
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

function toggleCollection(id) {
    expandedCollection = expandedCollection === id ? null : id;
    renderAll();
}

async function createCollection() {
    const name = document.getElementById('new-name').value.trim();
    const description = document.getElementById('new-desc').value.trim();
    if (!name) return;
    const result = await api('/collections', { method: 'POST', body: JSON.stringify({ name, description: description || null }) });
    if (result && !result.detail) {
        document.getElementById('new-name').value = '';
        document.getElementById('new-desc').value = '';
        await loadData();
    } else if (result?.detail) {
        alert(result.detail);
    }
}

async function editCollection(id) {
    const c = collections.find(cc => cc.id === id);
    if (!c) return;
    const name = prompt('Collection name:', c.name);
    if (name === null) return;
    const description = prompt('Description:', c.description || '');
    if (description === null) return;
    await api(`/collections/${id}`, { method: 'PATCH', body: JSON.stringify({ name: name.trim() || c.name, description: description.trim() || null }) });
    await loadData();
}

async function deleteCollection(id, name) {
    if (!confirm(`Delete collection "${name}"? Projects will be moved to General.`)) return;
    await api(`/collections/${id}`, { method: 'DELETE' });
    if (expandedCollection === id) expandedCollection = null;
    await loadData();
}

async function moveProject(projectName, collectionId) {
    if (!collectionId) return;
    await api(`/projects/${encodeURIComponent(projectName)}/move`, { method: 'POST', body: JSON.stringify({ collection_id: collectionId }) });
    await loadData();
}

loadData();
</script>
</body>
</html>
'''
