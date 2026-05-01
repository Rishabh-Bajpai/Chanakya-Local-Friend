document.addEventListener('DOMContentLoaded', () => {
    fetchModels();
    fetchProviders();
    fetchDiscoveredProviders(); // Auto-scan on load

    document.getElementById('refresh-models-btn').addEventListener('click', async (e) => {
        const btn = e.target;
        btn.disabled = true;
        btn.classList.add('rotating');
        await fetchModels(true);
        btn.classList.remove('rotating');
        btn.disabled = false;
    });

    document.getElementById('scan-btn').addEventListener('click', async (e) => {
        const btn = e.target;
        btn.disabled = true;
        btn.textContent = '⏳ Scanning...';
        await fetchDiscoveredProviders();
        btn.textContent = '🔍 Scan Now';
        btn.disabled = false;
    });
});

// --- Models Logic ---
let allModels = [];
let currentTab = 'llm';
let currentPage = 1;
let rowsPerPage = 10;

async function fetchModels(forceRefresh = false) {
    const tableBody = document.getElementById('models-table-body');
    try {
        // Show loading state if first load or explicit refresh
        if (allModels.length === 0 || forceRefresh) {
            tableBody.innerHTML = '<tr><td colspan="4" class="loading">Loading registry...</td></tr>';
        }

        const url = forceRefresh ? '/v1/models?refresh=true' : '/v1/models';
        const response = await fetch(url);
        const data = await response.json();
        allModels = data.data || [];

        renderModelTable();

        // Update stats
        document.getElementById('model-count').textContent = allModels.length;
        const uniqueProviders = new Set(allModels.map(m => m.provider_name));
        document.getElementById('provider-count').textContent = uniqueProviders.size;

    } catch (error) {
        console.error('Error fetching models:', error);
        tableBody.innerHTML = `<tr><td colspan="4" class="text-error">Error loading models: ${error.message}</td></tr>`;
    }
}

function renderModelTable() {
    const tableBody = document.getElementById('models-table-body');
    tableBody.innerHTML = '';

    // 1. Filter by Tab
    let filtered = allModels.filter(m => {
        const pType = (m.provider_type || 'llm').toLowerCase();
        return pType === currentTab;
    });

    if (filtered.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="4" class="text-center p-4">No ${currentTab.toUpperCase()} models found.</td></tr>`;
        updatePagination(0);
        return;
    }

    // 2. Paginate
    const total = filtered.length;
    let limit = rowsPerPage === 'all' ? total : parseInt(rowsPerPage);
    const start = (currentPage - 1) * limit;
    const end = start + limit;

    // Safety check if page is out of bounds (e.g. after filtering)
    if (start >= total && currentPage > 1) {
        currentPage = 1;
        renderModelTable();
        return;
    }

    const pageItems = filtered.slice(start, end);

    pageItems.forEach(model => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><span class="badge model-badge">${model.id}</span></td>
            <td>${model.provider_type ? model.provider_type.toUpperCase() : 'LLM'}</td>
            <td>${model.provider_name || 'Unknown'}</td>
            <td><span class="status-dot online"></span> Active</td>
        `;
        tableBody.appendChild(row);
    });

    updatePagination(total);
}

function updatePagination(totalItems) {
    const prevBtn = document.getElementById('prev-page');
    const nextBtn = document.getElementById('next-page');
    const pageInfo = document.getElementById('page-info');

    if (totalItems === 0) {
        prevBtn.disabled = true;
        nextBtn.disabled = true;
        pageInfo.textContent = "0 of 0";
        return;
    }

    const limit = rowsPerPage === 'all' ? totalItems : parseInt(rowsPerPage);
    const totalPages = Math.ceil(totalItems / limit);

    pageInfo.textContent = `Page ${currentPage} of ${totalPages} (${totalItems} items)`;

    prevBtn.disabled = currentPage === 1;
    nextBtn.disabled = currentPage === totalPages;
}


// --- Event Listeners for Models UI ---
document.addEventListener('DOMContentLoaded', () => {
    // Tabs
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentTab = btn.dataset.tab;
            currentPage = 1; // Reset page
            renderModelTable();
        });
    });

    // Pagination
    document.getElementById('rows-per-page-select').addEventListener('change', (e) => {
        rowsPerPage = e.target.value;
        currentPage = 1;
        renderModelTable();
    });

    document.getElementById('prev-page').addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            renderModelTable();
        }
    });

    document.getElementById('next-page').addEventListener('click', () => {
        // We need total pages to check upper bound, but loose check is fine or we re-calc
        // For simplicity, just increment and render handles bounds in real app,
        // but here we can just rely on the fact button is disabled if at max.
        currentPage++;
        renderModelTable();
    });
});


// --- Providers Logic ---
async function fetchProviders(force = false) {
    const tableBody = document.querySelector('#providers-table tbody');
    // If we are currently editing (inline), don't refresh and lose state unless force
    if (!force && document.querySelector('.editing-row')) return;

    try {
        const response = await fetch('/api/config/providers');
        const statusResponse = await fetch('/api/config/status');
        const providers = await statusResponse.json();

        tableBody.innerHTML = '';

        if (providers.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="5">No providers configured. Click "Add Provider" to start.</td></tr>';
            return;
        }

        providers.forEach(p => {
            const statusClass = p.status === 'online' ? 'online' : (p.status === 'error' ? 'error' : 'offline');
            // Parse index
            let index = 0;
            const match = p.name.match(/\d+$/);
            if (match) index = parseInt(match[0]);

            renderProviderRow(tableBody, p, index, statusClass);
        });

        // Re-attach listeners for new rows
        setupTableActions();

    } catch (error) {
        console.error('Error fetching providers:', error);
        tableBody.innerHTML = `<tr><td colspan="5" class="text-error">Error loading providers: ${error.message}</td></tr>`;
    }
}

// ... (renderProviderRow and setupTableActions remain unchanged) ...

// --- Helper: Robust Refresh ---
async function refreshWithRetry(retries = 5, delay = 1000) {
    // Wait a moment for server to potentially restart (Uvicorn reload)
    await new Promise(r => setTimeout(r, 1000));

    for (let i = 0; i < retries; i++) {
        try {
            // We want to know if fetch actually succeeded
            const check = await fetch('/api/config/providers');
            if (check.ok) {
                await fetchProviders(true); // Force refresh to clear editing state
                await fetchModels(true);
                return true;
            }
        } catch (e) {
            console.log(`Retrying refresh (${i + 1}/${retries})...`);
            await new Promise(r => setTimeout(r, delay));
        }
    }
    // If we failed after retries, try to reload page as last resort or just alert
    console.warn("Server reload took too long.");
    // Force a reload might be too aggressive, let's just alert
    alert("Server restart is taking longer than expected. Please refresh the page.");
    return false;
}


function renderProviderRow(tbody, p, index, statusClass) {
    const row = document.createElement('tr');
    row.dataset.index = index;
    row.dataset.type = p.type;
    row.dataset.url = p.base_url;
    row.dataset.name = p.name;

    // We store API Key in a data attribute? Ideally we shouldn't expose it if not needed,
    // but the API returns it. We need it for pre-filling edit.
    // We'll fetch it on demand or trust the config endpoint.
    // For now let's just render standard.

    row.innerHTML = `
        <td><strong>${p.name}</strong></td>
        <td><span class="badge">${p.type.toUpperCase()}</span></td>
        <td class="code-font">${p.base_url}</td>
        <td><span class="status-dot ${statusClass}"></span> ${p.status.toUpperCase()} <small>(${p.details})</small></td>
        <td>
            <button class="btn btn-icon btn-sm edit-btn" title="Edit">✏️</button>
            <button class="btn btn-icon btn-danger btn-sm delete-btn" title="Remove">🗑️</button>
        </td>
    `;
    tbody.appendChild(row);
}

function setupTableActions() {
    // Delete
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.onclick = async () => {
            if (!confirm('Remove this provider from .env?')) return;
            // Logic same as before
            const row = btn.closest('tr');
            const index = row.dataset.index;
            const type = row.dataset.type;

            try {
                const res = await fetch(`/api/config/providers/${index}?type=${type}`, { method: 'DELETE' });
                if (res.ok) await refreshWithRetry();
                else alert('Failed to delete');
            } catch (e) { console.error(e); }
        };
    });

    // Edit
    document.querySelectorAll('.edit-btn').forEach(btn => {
        btn.onclick = () => {
            const row = btn.closest('tr');
            enableInlineEdit(row);
        };
    });
}

function enableInlineEdit(row) {
    if (document.querySelector('.editing-row')) {
        alert("Please finish editing the current row first.");
        return;
    }
    row.classList.add('editing-row');

    // Get current values
    const currentType = row.dataset.type;
    const currentUrl = row.dataset.url;
    const index = row.dataset.index;

    // Fetch key for this provider to prefill
    // (We could fetch all config again, or just assume 'na' if safely hidden)
    // Let's fetch config quickly?
    // For smoothness, we can start with '...' or fetch explicitly.
    // Optimization: Just load it.

    row.innerHTML = `
        <td>Editing...</td>
        <td>
            <select class="edit-type form-control-sm">
                <option value="llm" ${currentType === 'llm' ? 'selected' : ''}>LLM</option>
                <option value="stt" ${currentType === 'stt' ? 'selected' : ''}>STT</option>
                <option value="tts" ${currentType === 'tts' ? 'selected' : ''}>TTS</option>
            </select>
        </td>
        <td>
            <input type="text" class="edit-url form-control-sm" value="${currentUrl}" placeholder="Base URL">
            <input type="text" class="edit-key form-control-sm mt-1" value="loading..." placeholder="API Key">
        </td>
        <td>-</td>
        <td class="actions-cell">
            <button class="btn btn-sm btn-success save-btn">Save</button>
            <button class="btn btn-sm btn-secondary cancel-btn">Cancel</button>
        </td>
    `;

    // Fetch key async
    fetch('/api/config/providers').then(r => r.json()).then(list => {
        const p = list.find(item => item.base_url === currentUrl && item.type === currentType);
        // Identifying by URL/Type is sloppy but works if unique. Identifying by Name is better.
        // We stored name in dataset.
        const name = row.dataset.name;
        const exact = list.find(item => item.name === name);
        if (exact) {
            row.querySelector('.edit-key').value = exact.api_key;
        } else {
            row.querySelector('.edit-key').value = 'na';
        }
    });

    // Attach listeners
    row.querySelector('.cancel-btn').onclick = () => {
        // Just refresh to restore
        fetchProviders();
    };

    row.querySelector('.save-btn').onclick = async () => {
        const newType = row.querySelector('.edit-type').value;
        const newUrl = row.querySelector('.edit-url').value;
        const newKey = row.querySelector('.edit-key').value;

        // Save
        const btn = row.querySelector('.save-btn');
        btn.textContent = '...';
        btn.disabled = true;

        try {
            const res = await fetch(`/api/config/providers/${index}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: newType, base_url: newUrl, api_key: newKey })
            });

            if (res.ok) {
                await refreshWithRetry();
            } else {
                const e = await res.json();
                alert(`Error: ${e.detail}`);
                btn.disabled = false;
                btn.textContent = 'Save';
            }
        } catch (e) {
            console.error(e);
            await refreshWithRetry();
        }
    };
}

// Add New Provider Logic (Inline)
// --- Discovery Logic ---

async function fetchDiscoveredProviders() {
    const section = document.getElementById('discovered-section');
    const list = document.getElementById('discovered-list');

    list.innerHTML = '<div class="scanning-state"><span class="spinner">🔄</span> Scanning for providers...</div>';
    section.style.display = 'block';

    try {
        const response = await fetch('/api/config/discovered');

        if (!response.ok) {
            let errorMsg = `Server error (${response.status})`;
            try {
                const errorData = await response.json();
                if (errorData.detail) errorMsg = errorData.detail;
            } catch (e) { } // Fallback to status code if not JSON
            throw new Error(errorMsg);
        }

        const providers = await response.json();

        if (!providers || providers.length === 0) {
            list.innerHTML = '<div class="text-center p-4">No new providers discovered.</div>';
            // We can keep it visible or hide if truly empty, but usually "no results" is better than a disappearing block
            return;
        }

        list.innerHTML = '';
        providers.forEach(p => {
            const card = document.createElement('div');
            card.className = 'discovered-card';

            const typeBadges = p.detected_types
                .map(t => `<span class="type-badge ${t}">${t.toUpperCase()}</span>`)
                .join('');

            card.innerHTML = `
                <div class="discovered-info">
                    <div class="discovered-name">${p.name}</div>
                    <div class="discovered-url">${p.base_url}</div>
                    <div class="discovered-types">${typeBadges}</div>
                    <div class="discovered-models">${p.model_count} model(s) available</div>
                </div>
                <div class="discovered-actions">
                    <button class="btn-add" title="Add this provider">+ Add</button>
                </div>
            `;

            card.querySelector('.btn-add').addEventListener('click', async (e) => {
                const btn = e.target;
                btn.disabled = true;
                btn.textContent = 'Adding...';

                try {
                    const res = await fetch('/api/config/discovered/accept', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify([{
                            name: p.name,
                            base_url: p.base_url,
                            detected_types: p.detected_types,
                            api_key: 'na'
                        }])
                    });

                    if (res.ok) {
                        btn.textContent = '✓ Added';
                        btn.style.color = '#10b981';
                        // Refresh everything
                        await refreshWithRetry();
                        await fetchDiscoveredProviders();
                    } else {
                        btn.textContent = 'Error';
                        btn.disabled = false;
                    }
                } catch (err) {
                    console.error('Accept error:', err);
                    btn.textContent = '+ Add';
                    btn.disabled = false;
                }
            });

            list.appendChild(card);
        });

    } catch (error) {
        console.error('Discovery error:', error);
        list.innerHTML = `
            <div class="error-state">
                <p>⚠️ Failed to scan for providers: ${error.message}</p>
                <button class="btn btn-secondary btn-sm" onclick="fetchDiscoveredProviders()">Retry Scan</button>
            </div>
        `;
    }
}


document.getElementById('add-provider-btn').onclick = () => {
    if (document.querySelector('.editing-row')) {
        alert("Please save the current edit first.");
        return;
    }

    const tbody = document.querySelector('#providers-table tbody');
    const row = document.createElement('tr');
    row.classList.add('editing-row');
    row.innerHTML = `
        <td>New Provider</td>
        <td>
            <select class="edit-type form-control-sm">
                <option value="llm">LLM</option>
                <option value="stt">STT</option>
                <option value="tts">TTS</option>
            </select>
        </td>
        <td>
            <input type="text" class="edit-url form-control-sm" placeholder="http://localhost:11434/v1">
            <input type="text" class="edit-key form-control-sm mt-1" value="na" placeholder="API Key">
        </td>
        <td>-</td>
        <td class="actions-cell">
            <button class="btn btn-sm btn-success save-new-btn">Save</button>
            <button class="btn btn-sm btn-secondary cancel-new-btn">Cancel</button>
        </td>
    `;

    // Insert at top or bottom? User usually expects bottom, but top is more visible.
    // Let's append for now.
    tbody.appendChild(row);
    // Scroll to it
    row.scrollIntoView({ behavior: 'smooth' });

    row.querySelector('.cancel-new-btn').onclick = () => {
        row.remove();
        // Check if empty
        if (tbody.children.length === 0) fetchProviders();
    };

    row.querySelector('.save-new-btn').onclick = async () => {
        const newType = row.querySelector('.edit-type').value;
        const newUrl = row.querySelector('.edit-url').value;
        const newKey = row.querySelector('.edit-key').value;

        const btn = row.querySelector('.save-new-btn');
        btn.textContent = 'Saving...';
        btn.disabled = true;

        try {
            const res = await fetch('/api/config/providers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ type: newType, base_url: newUrl, api_key: newKey })
            });

            if (res.ok) {
                await refreshWithRetry();
            } else {
                const e = await res.json();
                alert(`Error: ${e.detail}`);
                btn.disabled = false;
                btn.textContent = 'Save';
            }
        } catch (e) {
            console.error(e);
            await refreshWithRetry();
        }
    };
};
