const API = window.location.origin;
let adminToken = localStorage.getItem('admin_token') || '';

// 页面加载
window.onload = function() {
    if (adminToken) {
        checkToken();
    }
};

async function checkToken() {
    try {
        const res = await apiFetch('/api/admin/users');
        if (res.ok) {
            showPanel();
        } else {
            adminToken = '';
            localStorage.removeItem('admin_token');
        }
    } catch {
        adminToken = '';
        localStorage.removeItem('admin_token');
    }
}

async function apiFetch(path, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (adminToken) headers['Authorization'] = 'Bearer ' + adminToken;
    return fetch(API + path, { ...options, headers });
}

async function adminLogin() {
    const username = document.getElementById('admin-username').value;
    const password = document.getElementById('admin-password').value;
    const errEl = document.getElementById('admin-login-error');
    errEl.textContent = '';

    try {
        const res = await fetch(API + '/api/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (res.ok) {
            adminToken = data.token;
            localStorage.setItem('admin_token', adminToken);
            showPanel();
        } else {
            errEl.textContent = data.detail || '登录失败';
        }
    } catch (e) {
        errEl.textContent = '连接服务器失败';
    }
}

function adminLogout() {
    adminToken = '';
    localStorage.removeItem('admin_token');
    document.getElementById('admin-login').style.display = '';
    document.getElementById('admin-panel').style.display = 'none';
}

function showPanel() {
    document.getElementById('admin-login').style.display = 'none';
    document.getElementById('admin-panel').style.display = '';
    loadUsers();
    loadTableConfig();
    checkHealth();
}

// 用户管理
async function loadUsers() {
    const res = await apiFetch('/api/admin/users');
    const data = await res.json();
    const tbody = document.getElementById('users-tbody');
    const select = document.getElementById('recharge-user');
    tbody.innerHTML = '';
    select.innerHTML = '';

    (data.users || []).forEach(u => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${u.user_id}</td>
            <td>${u.username}</td>
            <td style="color:#f0a500;font-weight:bold">${u.chips}</td>
            <td><button class="btn-del" onclick="deleteUser('${u.user_id}')">删除</button></td>
        `;
        tbody.appendChild(tr);

        const opt = document.createElement('option');
        opt.value = u.user_id;
        opt.textContent = `${u.username} (余额: ${u.chips})`;
        select.appendChild(opt);
    });
}

async function createUser() {
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value;
    const chips = parseInt(document.getElementById('new-chips').value) || 1000;
    const msgEl = document.getElementById('create-user-msg');
    msgEl.textContent = '';
    msgEl.className = 'msg-text';

    if (!username || !password) {
        msgEl.textContent = '请填写用户名和密码';
        msgEl.className = 'msg-text error';
        return;
    }

    const res = await apiFetch('/api/admin/users', {
        method: 'POST',
        body: JSON.stringify({ username, password, chips })
    });
    const data = await res.json();
    if (res.ok) {
        msgEl.textContent = `创建成功! ID: ${data.user_id}`;
        msgEl.className = 'msg-text success';
        document.getElementById('new-username').value = '';
        document.getElementById('new-password').value = '';
        loadUsers();
    } else {
        msgEl.textContent = data.detail || '创建失败';
        msgEl.className = 'msg-text error';
    }
}

async function addChips() {
    const userId = document.getElementById('recharge-user').value;
    const amount = parseInt(document.getElementById('recharge-amount').value) || 0;
    const msgEl = document.getElementById('recharge-msg');
    msgEl.textContent = '';
    msgEl.className = 'msg-text';

    if (!userId) {
        msgEl.textContent = '请选择用户';
        msgEl.className = 'msg-text error';
        return;
    }

    const res = await apiFetch('/api/admin/users/add_chips', {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, amount })
    });
    const data = await res.json();
    if (res.ok) {
        msgEl.textContent = `充值成功! 当前余额: ${data.chips}`;
        msgEl.className = 'msg-text success';
        loadUsers();
    } else {
        msgEl.textContent = data.detail || '充值失败';
        msgEl.className = 'msg-text error';
    }
}

async function deleteUser(userId) {
    if (!confirm('确定删除该用户?')) return;
    const res = await apiFetch('/api/admin/users/' + userId, { method: 'DELETE' });
    if (res.ok) loadUsers();
}

// 桌台配置
async function loadTableConfig() {
    const res = await apiFetch('/api/admin/table_config');
    const cfg = await res.json();
    document.getElementById('cfg-small-blind').value = cfg.small_blind;
    document.getElementById('cfg-big-blind').value = cfg.big_blind;
    document.getElementById('cfg-turn-timeout').value = cfg.turn_timeout;
    document.getElementById('cfg-max-players').value = cfg.max_players;
}

async function saveTableConfig() {
    const msgEl = document.getElementById('config-msg');
    const cfg = {
        small_blind: parseInt(document.getElementById('cfg-small-blind').value),
        big_blind: parseInt(document.getElementById('cfg-big-blind').value),
        turn_timeout: parseInt(document.getElementById('cfg-turn-timeout').value),
        max_players: parseInt(document.getElementById('cfg-max-players').value),
    };
    const res = await apiFetch('/api/admin/table_config', {
        method: 'POST',
        body: JSON.stringify(cfg)
    });
    if (res.ok) {
        msgEl.textContent = '设置已保存 (下一局生效)';
        msgEl.className = 'msg-text success';
    } else {
        msgEl.textContent = '保存失败';
        msgEl.className = 'msg-text error';
    }
}

async function checkHealth() {
    try {
        const res = await fetch(API + '/api/health');
        const data = await res.json();
        document.getElementById('server-status').innerHTML = `
            状态: <span style="color:#4caf50">运行中</span><br>
            在线玩家: <strong>${data.players_online}</strong>
        `;
    } catch {
        document.getElementById('server-status').innerHTML = `状态: <span style="color:#e74c3c">无法连接</span>`;
    }
}
