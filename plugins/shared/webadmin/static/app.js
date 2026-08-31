/* ============================================================
   WebAdmin — Client-Side Logic
   Handles routing, state, SSE live feed, and API calls.
   ============================================================ */

const API_BASE = '/api';

// --- State ---
let currentUser = null;
let currentView = 'login';
let currentPage = 'dashboard';
let eventSource = null;

// --- Elements ---
const elViews = {
    login: document.getElementById('view-login'),
    app: document.getElementById('view-app')
};

const elPages = {
    dashboard: document.getElementById('page-dashboard'),
    players: document.getElementById('page-players'),
    chat: document.getElementById('page-chat'),
    commands: document.getElementById('page-commands'),
    audit: document.getElementById('page-audit'),
    settings: document.getElementById('page-settings')
};

// --- Initialization ---

async function init() {
    // Check if we have an active session
    try {
        const res = await fetch(`${API_BASE}/auth/me`);
        if (res.ok) {
            const data = await res.json();
            currentUser = data.user;
            showApp();
        } else {
            showLogin();
        }
    } catch (e) {
        showLogin();
    }
}

// --- View/Page Navigation ---

function showLogin() {
    currentView = 'login';
    elViews.app.classList.remove('active');
    elViews.login.classList.add('active');
    
    // Clear state
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    
    document.getElementById('login-error').hidden = true;
    document.getElementById('login-password').value = '';
}

function showApp() {
    currentView = 'app';
    elViews.login.classList.remove('active');
    elViews.app.classList.add('active');
    
    // Update user info in sidebar
    document.getElementById('user-name').textContent = currentUser.username;
    document.getElementById('user-avatar').textContent = currentUser.username.charAt(0).toUpperCase();
    
    const roleNames = { 1: 'Helper', 2: 'Moderator', 3: 'Administrator' };
    document.getElementById('user-role').textContent = roleNames[currentUser.smod_level] || 'Helper';
    
    // Hide settings nav if not level 3
    if (currentUser.smod_level < 3) {
        document.getElementById('nav-settings').style.display = 'none';
    } else {
        document.getElementById('nav-settings').style.display = 'flex';
    }
    
    // Apply permission gating to UI elements
    applyPermissions();
    
    // Request browser notification permissions for server health alerts
    requestNotificationPermission();
    
    // Start SSE feed
    initEventSource();
    
    // Load initial data
    loadPage(currentPage);
}

function loadPage(pageId) {
    // Update nav state
    document.querySelectorAll('.nav-item').forEach(el => {
        if (el.dataset.page === pageId) el.classList.add('active');
        else el.classList.remove('active');
    });
    
    // Show page
    Object.keys(elPages).forEach(id => {
        if (id === pageId) elPages[id].classList.add('active');
        else elPages[id].classList.remove('active');
    });
    
    currentPage = pageId;
    
    // Fetch data based on page
    if (pageId === 'dashboard') loadDashboard();
    else if (pageId === 'players') loadPlayers();
    else if (pageId === 'audit') loadAuditLog();
    else if (pageId === 'settings' && currentUser.smod_level >= 3) loadSettings();
}

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        loadPage(item.dataset.page);
    });
});

// --- API Helpers ---

async function apiFetch(endpoint, options = {}) {
    try {
        const res = await fetch(`${API_BASE}${endpoint}`, options);
        
        // Handle unauthorized (session expired)
        if (res.status === 401 && currentView === 'app') {
            showToast('Session expired. Please log in again.', 'error');
            showLogin();
            return null;
        }
        
        const data = await res.json().catch(() => ({}));
        
        if (!res.ok) {
            throw new Error(data.error || `HTTP error ${res.status}`);
        }
        
        return data;
    } catch (e) {
        showToast(e.message, 'error');
        throw e;
    }
}

// --- Auth ---

document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('login-btn');
    const errEl = document.getElementById('login-error');
    
    btn.disabled = true;
    btn.innerHTML = '<span>Signing in...</span>';
    errEl.hidden = true;
    
    const username = document.getElementById('login-username').value;
    const password = document.getElementById('login-password').value;
    
    try {
        const res = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        
        const data = await res.json();
        
        if (res.ok) {
            currentUser = data.user;
            showToast(`Welcome back, ${currentUser.username}`, 'success');
            showApp();
        } else {
            errEl.textContent = data.error || 'Login failed';
            errEl.hidden = false;
        }
    } catch (e) {
        errEl.textContent = 'Network error. Please try again.';
        errEl.hidden = false;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Sign In</span>';
    }
});

document.getElementById('logout-btn').addEventListener('click', async () => {
    try {
        await fetch(`${API_BASE}/auth/logout`, { method: 'POST' });
    } catch (e) {}
    showLogin();
});

// --- Server Events (SSE) ---

function initEventSource() {
    if (eventSource) return;
    
    const indicator = document.getElementById('chat-indicator');
    
    eventSource = new EventSource(`${API_BASE}/events`);
    
    eventSource.onopen = () => {
        indicator.hidden = true;
    };
    
    eventSource.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            handleServerEvent(event);
        } catch (err) {}
    };
    
    eventSource.onerror = () => {
        indicator.hidden = false;
        indicator.style.background = 'var(--danger)';
        indicator.style.animation = 'none';
        
        // EventSource auto-reconnects, but let's visually indicate it's broken
        setTimeout(() => {
            if (eventSource && eventSource.readyState !== EventSource.CLOSED) {
                indicator.style.background = 'var(--warning)';
                indicator.style.animation = 'pulse-glow 2s ease infinite';
            }
        }, 1000);
    };
}

function handleServerEvent(event) {
    const chatFeed = document.getElementById('chat-feed');
    const dashFeed = document.getElementById('dashboard-feed');
    
    // Remove empty state if present
    document.querySelectorAll('.feed-empty').forEach(el => el.remove());
    
    const el = document.createElement('div');
    el.className = 'feed-item';
    
    const timeHtml = `<div class="feed-time">${event.time.split(' ')[1]}</div>`;
    let iconHtml = '';
    let contentHtml = '';
    
    // Handle server health events separately (they get special treatment)
    if (event.type === 'server_health') {
        handleServerHealthEvent(event);
        return;
    }

    switch (event.type) {
        case 'chat':
            iconHtml = `<div class="feed-icon" style="color:var(--text-secondary)">💬</div>`;
            contentHtml = `<div class="feed-content"><span class="feed-player">${escapeHtml(event.player)}</span>: ${escapeHtml(event.message)}</div>`;
            break
        case 'kill':
            iconHtml = `<div class="feed-icon" style="color:var(--danger)">☠️</div>`;
            contentHtml = `<div class="feed-content">${escapeHtml(event.text)}</div>`;
            break
        case 'connect':
            iconHtml = `<div class="feed-icon" style="color:var(--success)">📥</div>`;
            contentHtml = `<div class="feed-content"><span class="feed-player">${escapeHtml(event.player)}</span> connected (ID:${event.player_id})</div>`;
            break
        case 'disconnect':
            iconHtml = `<div class="feed-icon" style="color:var(--warning)">📤</div>`;
            contentHtml = `<div class="feed-content"><span class="feed-player">${escapeHtml(event.player)}</span> disconnected (ID:${event.player_id})</div>`;
            break
        case 'admin':
            iconHtml = `<div class="feed-icon" style="color:var(--accent)">🛡️</div>`;
            contentHtml = `<div class="feed-content"><span class="feed-player">${escapeHtml(event.admin)}</span> used command: ${escapeHtml(event.command)} ${escapeHtml(event.target || '')}</div>`;
            break
        case 'server_say':
            iconHtml = `<div class="feed-icon" style="color:var(--info)">📢</div>`;
            contentHtml = `<div class="feed-content" style="color:var(--info); font-weight:500;">Server: ${escapeHtml(event.message)}</div>`;
            break
        case 'smsay':
            iconHtml = `<div class="feed-icon" style="color:var(--warning)">🛡️</div>`;
            contentHtml = `<div class="feed-content"><span class="feed-player" style="color:var(--warning)">${escapeHtml(event.admin)} (smsay)</span>: ${escapeHtml(event.message)}</div>`;
            break
        default:
            return; // Ignore other events
    }
    
    el.innerHTML = timeHtml + iconHtml + contentHtml;
    
    // Add to chat page
    chatFeed.appendChild(el.cloneNode(true));
    scrollToBottom(chatFeed);
    
    // Add to dashboard (keep only last 50)
    dashFeed.appendChild(el);
    while (dashFeed.children.length > 50) {
        dashFeed.removeChild(dashFeed.firstChild);
    }
    scrollToBottom(dashFeed);
    
    // Show indicator if not on chat page
    if (currentPage !== 'chat' && (event.type === 'chat' || event.type === 'smsay')) {
        document.getElementById('chat-indicator').hidden = false;
    }
}

function scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
}

// --- Dashboard ---

async function loadDashboard() {
    const data = await apiFetch('/server/info');
    if (!data) return;
    
    document.getElementById('dashboard-server-name').textContent = data.name;
    document.getElementById('stat-players').textContent = `${data.player_count} / ${data.max_players}`;
    const mapEl = document.getElementById('stat-map');
    mapEl.textContent = data.map;
    mapEl.title = data.map;
    const modeNames = { 0: 'Open', 1: 'Semi-Auth', 2: 'Full-Auth', 3: 'Duel', 4: 'Legends' };
    document.getElementById('stat-mode').textContent = modeNames[data.mode] || `Mode ${data.mode}`;
    
    document.getElementById('player-count-badge').textContent = data.player_count;
    
    // Update server health indicator
    updateHealthDisplay(data.process_status, data.process_status_since);
    
    const shuffleBtn = document.getElementById('cmd-shuffle-btn');
    if (shuffleBtn) {
        if (data.mode === 0) {
            shuffleBtn.disabled = false;
            shuffleBtn.title = '';
        } else {
            shuffleBtn.disabled = true;
            shuffleBtn.title = 'Shuffle Teams is only available in Open Mode';
        }
    }
}

// --- Server Health ---

let _lastHealthStatus = 'unknown';

function handleServerHealthEvent(event) {
    const status = event.status;
    const message = event.message;
    
    updateHealthDisplay(status, event.time);
    
    // Only fire alerts on transitions to offline (crash)
    if (status === 'offline' && _lastHealthStatus !== 'offline') {
        // Show persistent crash banner
        showCrashBanner(message);
        
        // Show toast
        showToast(`⚠️ ${message}`, 'error');
        
        // Fire browser notification
        sendBrowserNotification('Server Crash Detected', message);
        
        // Add to live feed as a critical event
        addHealthEventToFeed(event.time, message, 'offline');
    } else if (status === 'online' && _lastHealthStatus === 'offline') {
        // Server recovered — dismiss crash banner
        hideCrashBanner();
        showToast(`✅ ${message}`, 'success');
        sendBrowserNotification('Server Recovered', message);
        addHealthEventToFeed(event.time, message, 'online');
    }
    
    _lastHealthStatus = status;
}

function updateHealthDisplay(status, since) {
    const healthEl = document.getElementById('stat-health');
    const iconEl = document.getElementById('health-stat-icon');
    const cardEl = document.getElementById('health-stat-card');
    if (!healthEl) return;
    
    const statusConfig = {
        online:  { text: 'Online',  color: '#10b981', cardClass: '' },
        offline: { text: 'Offline', color: '#ef4444', cardClass: 'health-critical' },
        unknown: { text: '—',       color: '#71717a', cardClass: '' },
    };
    
    const cfg = statusConfig[status] || statusConfig.unknown;
    healthEl.textContent = cfg.text;
    healthEl.style.color = cfg.color;
    if (iconEl) iconEl.style.setProperty('--accent', cfg.color);
    
    if (cardEl) {
        cardEl.classList.remove('health-critical');
        if (cfg.cardClass) cardEl.classList.add(cfg.cardClass);
    }
    
    if (since) {
        healthEl.title = `Since: ${since}`;
    }
}

function showCrashBanner(message) {
    let banner = document.getElementById('crash-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'crash-banner';
        banner.className = 'crash-banner';
        // Insert at the top of main-content
        const main = document.querySelector('.main-content');
        if (main) main.prepend(banner);
    }
    banner.innerHTML = `
        <div class="crash-banner-content">
            <span class="crash-banner-icon">🚨</span>
            <span class="crash-banner-text"><strong>SERVER DOWN</strong> — ${escapeHtml(message)}</span>
            <button class="crash-banner-dismiss" onclick="hideCrashBanner()" title="Dismiss">✕</button>
        </div>
    `;
    banner.hidden = false;
}

function hideCrashBanner() {
    const banner = document.getElementById('crash-banner');
    if (banner) banner.hidden = true;
}

function addHealthEventToFeed(time, message, status) {
    const dashFeed = document.getElementById('dashboard-feed');
    const chatFeed = document.getElementById('chat-feed');
    if (!dashFeed) return;
    
    document.querySelectorAll('.feed-empty').forEach(el => el.remove());
    
    const el = document.createElement('div');
    el.className = 'feed-item';
    const timeStr = time ? time.split(' ')[1] || time : '';
    const icon = status === 'offline' ? '🚨' : '✅';
    const color = status === 'offline' ? 'var(--danger)' : 'var(--success)';
    el.innerHTML = `
        <div class="feed-time">${timeStr}</div>
        <div class="feed-icon" style="color:${color}">${icon}</div>
        <div class="feed-content" style="color:${color}; font-weight:600;">${escapeHtml(message)}</div>
    `;
    
    if (chatFeed) { chatFeed.appendChild(el.cloneNode(true)); scrollToBottom(chatFeed); }
    dashFeed.appendChild(el);
    while (dashFeed.children.length > 50) dashFeed.removeChild(dashFeed.firstChild);
    scrollToBottom(dashFeed);
}

// --- Browser Notifications ---

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

function sendBrowserNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
        try {
            const n = new Notification(title, {
                body: body,
                icon: '/static/favicon.ico',
                tag: 'server-health',
                requireInteraction: true
            });
            // Focus window when notification is clicked
            n.onclick = () => { window.focus(); n.close(); };
        } catch (e) {
            // Notifications not supported in this context
        }
    }
}

// --- Players ---

async function loadPlayers() {
    const data = await apiFetch('/server/players');
    if (!data) return;
    
    const tbody = document.getElementById('players-tbody');
    tbody.innerHTML = '';
    
    if (data.players.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="table-empty">No players connected</td></tr>`;
        return;
    }
    
    data.players.forEach(p => {
        const tr = document.createElement('tr');
        
        const teamClass = {
            'Spec': 'team-spec',
            'Red': 'team-red',
            'Blue': 'team-blue',
            'Free': 'team-free'
        }[p.team] || 'team-spec';
        
        // Action dropdown based on permissions
        let actionHtml = `<select class="input-xs" onchange="handlePlayerAction(this.value, ${p.id}, '${p.ip}', '${escapeHtml(p.name)}'); this.value='';">
            <option value="">Actions...</option>`;
            
        if (currentUser.smod_level >= 2) {
            actionHtml += `
                <option value="kick">Kick</option>
                <option value="tempban">Tempban</option>
                <option value="mute">Mute</option>
                <option value="unmute">Unmute</option>
                <option value="settk">Set TK Points</option>
                <option value="marktk">Mark TK</option>
                <option value="unmarktk">Unmark TK</option>
                <option value="forceteam">Force Team</option>
                <option value="spectator">Spectator</option>
            `;
        }
        if (currentUser.smod_level >= 3) {
            actionHtml += `<option value="ban">Ban IP</option>`;
        }
        actionHtml += `</select>`;
        
        tr.innerHTML = `
            <td>${p.id}</td>
            <td style="font-weight:500;">${escapeHtml(p.name)}</td>
            <td><span class="team-badge ${teamClass}">${p.team}</span></td>
            <td><span class="ip-text">${p.ip}</span></td>
            <td><div class="action-btns">${actionHtml}</div></td>
        `;
        tbody.appendChild(tr);
    });
}

document.getElementById('refresh-players-btn').addEventListener('click', loadPlayers);

// --- Quick Chat ---

function handleQuickSay(endpoint, successMsg) {
    return async () => {
        const input = document.getElementById('chat-quick-msg');
        const message = input.value.trim();
        if (!message) return;
        
        await apiFetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });
        
        input.value = '';
        showToast(successMsg, 'success');
    };
}

document.getElementById('chat-quick-say-btn')?.addEventListener('click', handleQuickSay('/server/say', 'Server message sent'));
document.getElementById('chat-quick-smsay-btn')?.addEventListener('click', handleQuickSay('/server/smsay', 'Admin chat sent'));

document.getElementById('chat-quick-msg')?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        document.getElementById('chat-quick-say-btn').click();
    }
});

// --- Commands ---

document.getElementById('cmd-say-btn').addEventListener('click', async () => {
    const input = document.getElementById('cmd-say-msg');
    const message = input.value.trim();
    if (!message) return;
    
    await apiFetch('/server/say', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
    });
    
    input.value = '';
    showToast('Server message sent', 'success');
});

document.getElementById('cmd-smsay-btn').addEventListener('click', async () => {
    const input = document.getElementById('cmd-say-msg');
    const message = input.value.trim();
    if (!message) return;
    
    await apiFetch('/server/smsay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
    });
    
    input.value = '';
    showToast('Admin chat sent', 'success');
});

document.getElementById('cmd-map-btn')?.addEventListener('click', async () => {
    const input = document.getElementById('cmd-map-name');
    const map = input.value.trim();
    if (!map) return;
    
    if (confirm(`Are you sure you want to change the map to ${map}?`)) {
        await apiFetch('/server/map', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ map })
        });
        input.value = '';
        showToast('Map change initiated', 'success');
    }
});

document.getElementById('cmd-cvar-get-btn')?.addEventListener('click', async () => {
    const name = document.getElementById('cmd-cvar-name').value.trim();
    if (!name) return;
    
    const data = await apiFetch(`/server/cvar?name=${encodeURIComponent(name)}`);
    if (data) {
        const resEl = document.getElementById('cvar-result');
        resEl.textContent = `${data.name} = "${data.value}"`;
        resEl.hidden = false;
    }
});

document.getElementById('cmd-cvar-set-btn')?.addEventListener('click', async () => {
    const name = document.getElementById('cmd-cvar-name').value.trim();
    const value = document.getElementById('cmd-cvar-value').value;
    if (!name) return;
    
    const data = await apiFetch('/server/cvar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, value })
    });
    
    if (data) {
        const resEl = document.getElementById('cvar-result');
        resEl.textContent = `Set ${data.name} to "${data.value}"`;
        resEl.hidden = false;
        showToast('CVar updated', 'success');
    }
});

document.getElementById('cmd-unban-btn')?.addEventListener('click', async () => {
    const ip = document.getElementById('cmd-unban-ip').value.trim();
    if (!ip) return;
    
    if (confirm(`Are you sure you want to unban IP: ${ip}?`)) {
        await apiFetch(`/player/${encodeURIComponent(ip)}/unban`, { method: 'POST' });
        document.getElementById('cmd-unban-ip').value = '';
        showToast('Unban executed', 'success');
    }
});

// --- Audit Log ---

async function loadAuditLog() {
    const data = await apiFetch('/admin/log');
    if (!data) return;
    
    const tbody = document.getElementById('audit-tbody');
    tbody.innerHTML = '';
    
    if (data.entries.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="table-empty">No audit records found</td></tr>`;
        return;
    }
    
    data.entries.forEach(e => {
        const tr = document.createElement('tr');
        
        // Format time
        const date = new Date(e.created_at + 'Z'); // SQLite timestamp is UTC
        const timeStr = date.toLocaleString();
        
        let detailsStr = '';
        if (e.details) {
            try {
                // If it's a dict, format it nicely
                if (typeof e.details === 'object') {
                    detailsStr = Object.entries(e.details)
                        .map(([k, v]) => `<span style="color:var(--text-secondary)">${k}:</span> ${v}`)
                        .join(', ');
                } else {
                    detailsStr = String(e.details);
                }
            } catch (err) {}
        }
        
        let actionClass = '';
        if (['kick', 'ban', 'mute'].includes(e.action)) actionClass = 'color:var(--danger)';
        else if (['unban', 'unmute'].includes(e.action)) actionClass = 'color:var(--success)';
        
        tr.innerHTML = `
            <td style="font-family:var(--font-mono); color:var(--text-secondary); font-size:0.75rem;">${timeStr}</td>
            <td style="font-weight:500;">${escapeHtml(e.username || 'System')}</td>
            <td style="${actionClass}; font-weight:600;">${escapeHtml(e.action)}</td>
            <td>${escapeHtml(e.target || '-')}</td>
            <td style="font-size:0.75rem;">${detailsStr || '-'}</td>
        `;
        tbody.appendChild(tr);
    });
}

document.getElementById('refresh-audit-btn').addEventListener('click', loadAuditLog);

// --- Settings ---

async function loadSettings() {
    if (currentUser.smod_level < 3) return;
    
    const data = await apiFetch('/admin/users');
    if (!data) return;
    
    const tbody = document.getElementById('users-tbody');
    tbody.innerHTML = '';
    
    data.users.forEach(u => {
        const tr = document.createElement('tr');
        const roleName = { 1: 'Helper', 2: 'Moderator', 3: 'Administrator' }[u.smod_level] || `Level ${u.smod_level}`;
        const statusHtml = u.is_active 
            ? `<span style="color:var(--success)">Active</span>` 
            : `<span style="color:var(--danger)">Disabled</span>`;
            
        let lastLogin = '-';
        if (u.last_login) {
            const date = new Date(u.last_login + 'Z');
            lastLogin = date.toLocaleString();
        }
        
        tr.innerHTML = `
            <td style="font-weight:500;">${escapeHtml(u.username)}</td>
            <td>${roleName}</td>
            <td>${statusHtml}</td>
            <td style="font-size:0.75rem; color:var(--text-secondary);">${lastLogin}</td>
            <td>
                <button class="btn btn-ghost btn-xs" onclick="editUser('${escapeHtml(u.username)}', ${u.smod_level})">Edit Level</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

document.getElementById('add-user-btn')?.addEventListener('click', () => {
    const html = `
        <div class="form-group">
            <label>Username</label>
            <input type="text" id="new-user-name" placeholder="Username" required>
        </div>
        <div class="form-group">
            <label>Password</label>
            <input type="password" id="new-user-pass" placeholder="Initial password" required>
        </div>
        <div class="form-group">
            <label>SMOD Level</label>
            <select id="new-user-level">
                <option value="1">Helper (Level 1)</option>
                <option value="2">Moderator (Level 2)</option>
                <option value="3">Administrator (Level 3)</option>
            </select>
        </div>
    `;
    
    showModal('Add Admin User', html, [
        { text: 'Cancel', class: 'btn-ghost', onclick: hideModal },
        { text: 'Create User', class: 'btn-primary', onclick: async () => {
            const username = document.getElementById('new-user-name').value;
            const password = document.getElementById('new-user-pass').value;
            const level = parseInt(document.getElementById('new-user-level').value);
            
            if (!username || !password) {
                showToast('Username and password required', 'error');
                return;
            }
            
            const res = await apiFetch('/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password, smod_level: level })
            });
            
            if (res) {
                hideModal();
                showToast('User created', 'success');
                loadSettings();
            }
        }}
    ]);
});

window.editUser = function(username, currentLevel) {
    const html = `
        <p style="margin-bottom:16px;">Updating level for <strong>${username}</strong></p>
        <div class="form-group">
            <label>SMOD Level</label>
            <select id="edit-user-level">
                <option value="1" ${currentLevel===1?'selected':''}>Helper (Level 1)</option>
                <option value="2" ${currentLevel===2?'selected':''}>Moderator (Level 2)</option>
                <option value="3" ${currentLevel===3?'selected':''}>Administrator (Level 3)</option>
            </select>
        </div>
        <p style="font-size:0.8rem; color:var(--text-muted);">Leave password blank to keep current.</p>
        <div class="form-group" style="margin-top:8px;">
            <label>New Password (Optional)</label>
            <input type="password" id="edit-user-pass" placeholder="Change password...">
        </div>
    `;
    
    showModal('Edit Admin User', html, [
        { text: 'Cancel', class: 'btn-ghost', onclick: hideModal },
        { text: 'Save Changes', class: 'btn-primary', onclick: async () => {
            const level = parseInt(document.getElementById('edit-user-level').value);
            const password = document.getElementById('edit-user-pass').value;
            
            const payload = { username, smod_level: level };
            if (password) payload.password = password;
            
            const res = await apiFetch('/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            if (res) {
                hideModal();
                showToast('User updated', 'success');
                loadSettings();
            }
        }}
    ]);
};

document.getElementById('change-own-password-btn')?.addEventListener('click', async () => {
    const input = document.getElementById('settings-new-password');
    const password = input.value;
    
    if (password.length < 6) {
        showToast('Password must be at least 6 characters', 'error');
        return;
    }
    
    const res = await apiFetch('/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
    });
    
    if (res) {
        input.value = '';
        showToast('Password updated. You will be logged out.', 'info');
        setTimeout(() => showLogin(), 2000);
    }
});


// --- Modals & Prompts ---

function showModal(title, bodyHtml, buttons) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = bodyHtml;
    
    const footer = document.getElementById('modal-footer');
    footer.innerHTML = '';
    
    buttons.forEach(b => {
        const btn = document.createElement('button');
        btn.className = `btn ${b.class}`;
        btn.textContent = b.text;
        btn.onclick = b.onclick;
        footer.appendChild(btn);
    });
    
    document.getElementById('modal-overlay').hidden = false;
}

function hideModal() {
    document.getElementById('modal-overlay').hidden = true;
}

document.getElementById('modal-close').addEventListener('click', hideModal);


window.handlePlayerAction = function(action, id, ip, name) {
    if (!action) return;
    
    if (['kick', 'unmute', 'unmarktk', 'spectator'].includes(action)) {
        let endpointAction = action;
        if (action === 'spectator') endpointAction = 'forceteam';
        
        const html = `<p>Are you sure you want to <strong>${action}</strong> player <span style="color:var(--accent)">${name}</span>?</p>`;
        showModal(`Confirm ${action}`, html, [
            { text: 'Cancel', class: 'btn-ghost', onclick: hideModal },
            { text: `Yes`, class: 'btn-danger', onclick: async () => {
                const body = action === 'spectator' ? { team: 's' } : {};
                const res = await apiFetch(`/player/${id}/${endpointAction}`, { 
                    method: 'POST',
                    headers: Object.keys(body).length > 0 ? { 'Content-Type': 'application/json' } : undefined,
                    body: Object.keys(body).length > 0 ? JSON.stringify(body) : undefined
                });
                if (res) {
                    hideModal();
                    showToast(`Executed ${action} on ${name}`, 'success');
                    if (currentPage === 'players') loadPlayers();
                }
            }}
        ]);
    } else if (action === 'ban') {
        const html = `<p>Are you sure you want to <strong>ban IP</strong> for <span style="color:var(--accent)">${name}</span>?</p>
            <div class="form-group"><label>Reason</label><input type="text" id="prompt-ban-reason" placeholder="Optional"></div>`;
        showModal(`Confirm Ban`, html, [
            { text: 'Cancel', class: 'btn-ghost', onclick: hideModal },
            { text: `Ban`, class: 'btn-danger', onclick: async () => {
                const reason = document.getElementById('prompt-ban-reason').value;
                const res = await apiFetch(`/player/${encodeURIComponent(ip)}/ban`, { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason })
                });
                if (res) {
                    hideModal();
                    showToast(`Banned IP for ${name}`, 'success');
                    if (currentPage === 'players') loadPlayers();
                }
            }}
        ]);
    } else if (['mute', 'marktk'].includes(action)) {
        promptAction(action, id, name, 'Minutes (default 10)', 10, 'minutes');
    } else if (action === 'settk') {
        promptAction(action, id, name, 'TK Points', 0, 'points');
    } else if (action === 'tempban') {
        promptAction(action, id, name, 'Rounds (default 1)', 1, 'rounds');
    } else if (action === 'forceteam') {
        const html = `
            <p style="margin-bottom:12px;">Action: <strong>Force Team</strong> on <span style="color:var(--accent)">${name}</span></p>
            <div class="form-group">
                <label>Team</label>
                <select id="prompt-team-select" class="input-xs" style="width:100%; padding:8px;">
                    <option value="red">Red</option>
                    <option value="blue">Blue</option>
                    <option value="s">Spectator</option>
                </select>
            </div>
        `;
        showModal(`Force Team`, html, [
            { text: 'Cancel', class: 'btn-ghost', onclick: hideModal },
            { text: `Execute`, class: 'btn-warning', onclick: async () => {
                const team = document.getElementById('prompt-team-select').value;
                const res = await apiFetch(`/player/${id}/forceteam`, { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ team })
                });
                if (res) {
                    hideModal();
                    showToast(`Forced ${name} to team ${team}`, 'success');
                    if (currentPage === 'players') loadPlayers();
                }
            }}
        ]);
    }
};

window.confirmAction = function(action, id_or_ip, name) {
    const html = `<p>Are you sure you want to <strong>${action}</strong> player <span style="color:var(--accent)">${name}</span>?</p>`;
    
    showModal(`Confirm ${action}`, html, [
        { text: 'Cancel', class: 'btn-ghost', onclick: hideModal },
        { text: `Yes, ${action}`, class: 'btn-danger', onclick: async () => {
            const endpoint = `/player/${encodeURIComponent(id_or_ip)}/${action}`;
            const res = await apiFetch(endpoint, { method: 'POST' });
            if (res) {
                hideModal();
                showToast(`Player ${name} was ${action}ed`, 'success');
                if (currentPage === 'players') loadPlayers();
            }
        }}
    ]);
};

window.promptAction = function(action, id_or_ip, name, inputLabel, defaultValue, payloadKey = 'minutes') {
    const html = `
        <p style="margin-bottom:12px;">Action: <strong>${action}</strong> on <span style="color:var(--accent)">${name}</span></p>
        <div class="form-group">
            <label>${inputLabel}</label>
            <input type="number" id="prompt-input" value="${defaultValue}">
        </div>
    `;
    
    showModal(`Action: ${action}`, html, [
        { text: 'Cancel', class: 'btn-ghost', onclick: hideModal },
        { text: `Execute`, class: 'btn-warning', onclick: async () => {
            const val = document.getElementById('prompt-input').value;
            const endpoint = `/player/${encodeURIComponent(id_or_ip)}/${action}`;
            const res = await apiFetch(endpoint, { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [payloadKey]: parseInt(val) })
            });
            if (res) {
                hideModal();
                showToast(`Executed ${action} on ${name}`, 'success');
            }
        }}
    ]);
};

// --- Toasts ---

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add('toast-exit');
        setTimeout(() => toast.remove(), 250);
    }, 4000);
}

// --- Utils ---

function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe
         .toString()
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

function applyPermissions() {
    const level = currentUser.smod_level;
    document.querySelectorAll('[data-min-level]').forEach(el => {
        const required = parseInt(el.dataset.minLevel);
        if (level < required) {
            el.style.display = 'none';
        } else {
            // Restore default display (remove inline none)
            el.style.display = '';
        }
    });
}

// --- Boot ---
init();


document.getElementById('cmd-nextmap-btn').addEventListener('click', async () => {
    if(confirm("Are you sure you want to execute nextmap?")) {
        const res = await apiFetch('/server/commands/nextmap', { method: 'POST' });
        if(res) showToast('Next map executing', 'success');
    }
});

document.getElementById('cmd-maprestart-btn').addEventListener('click', async () => {
    if(confirm("Are you sure you want to restart the map?")) {
        const res = await apiFetch('/server/commands/maprestart', { method: 'POST' });
        if(res) showToast('Map restart executing', 'success');
    }
});

document.getElementById('cmd-newround-btn').addEventListener('click', async () => {
    if(confirm("Are you sure you want to start a new round?")) {
        const res = await apiFetch('/server/commands/newround', { method: 'POST' });
        if(res) showToast('New round executing', 'success');
    }
});

document.getElementById('cmd-shuffle-btn').addEventListener('click', async () => {
    if(confirm("Are you sure you want to shuffle teams?")) {
        const res = await apiFetch('/server/commands/shuffle', { method: 'POST' });
        if(res) showToast('Teams shuffled', 'success');
    }
});

document.getElementById('cmd-vstr-btn').addEventListener('click', async () => {
    const vstr = document.getElementById('cmd-vstr-val').value.trim();
    if (!vstr) return;
    const res = await apiFetch('/server/commands/vstr', { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vstr })
    });
    if(res) showToast(`Executed vstr: ${vstr}`, 'success');
});

document.getElementById('cmd-mbmode-btn').addEventListener('click', async () => {
    const mode = document.getElementById('cmd-mbmode-val').value;
    const map = document.getElementById('cmd-mbmode-map').value.trim();
    if (!mode) return;
    if(confirm(`Set MBMode to ${mode} (Map: ${map || 'current'})?`)) {
        const res = await apiFetch('/server/commands/mbmode', { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: parseInt(mode), map })
        });
        if(res) showToast(`MBMode changed`, 'success');
    }
});

document.getElementById('cmd-gametype-btn').addEventListener('click', async () => {
    const team1 = document.getElementById('cmd-gametype-t1').value.trim();
    const team2 = document.getElementById('cmd-gametype-t2').value.trim();
    const map = document.getElementById('cmd-gametype-map').value.trim();
    if(confirm(`Set Gametype (T1: ${team1}, T2: ${team2}, Map: ${map || 'current'})?`)) {
        const res = await apiFetch('/server/commands/gametype', { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ team1, team2, map })
        });
        if(res) showToast(`Gametype changed`, 'success');
    }
});

document.getElementById('cmd-tempbanlist-btn').addEventListener('click', async () => {
    const res = await apiFetch('/server/tempbanlist');
    if (res) {
        const el = document.getElementById('tempbanlist-result');
        el.hidden = false;
        
        if (!res.bans || res.bans.length === 0) {
            el.innerHTML = '<div style="color:var(--text-muted)">No active tempbans.</div>';
            return;
        }
        
        let html = `
            <table class="data-table" style="margin-top: 10px;">
                <thead>
                    <tr><th>Slot ID</th><th>Rounds Left</th><th>IP Address</th></tr>
                </thead>
                <tbody>
        `;
        res.bans.forEach(b => {
            html += `<tr><td>${b.id}</td><td>${b.rounds}</td><td>${b.ip}</td></tr>`;
        });
        html += `</tbody></table>`;
        el.innerHTML = html;
    }
});

document.getElementById('cmd-removetempban-btn').addEventListener('click', async () => {
    const target = document.getElementById('cmd-removetempban-val').value.trim();
    if (!target) return;
    const res = await apiFetch('/server/commands/removetempban', { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target })
    });
    if(res) showToast(`Removed tempban for ${target}`, 'success');
});
