const API = window.location.origin;
const WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';

let token = localStorage.getItem('player_token') || '';
let userId = localStorage.getItem('player_user_id') || '';
let username = localStorage.getItem('player_username') || '';
let ws = null;
let gameState = null;
let mySeat = -1;
let turnTimerInterval = null;
let turnTimerEnd = 0;
let currentTimerSeat = -1;

// ─── 登录 ───

async function doLogin() {
    const u = document.getElementById('login-username').value.trim();
    const p = document.getElementById('login-password').value;
    const errEl = document.getElementById('login-error');
    errEl.textContent = '';

    if (!u || !p) { errEl.textContent = '请输入用户名和密码'; return; }

    try {
        const res = await fetch(API + '/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u, password: p })
        });
        const data = await res.json();
        if (res.ok) {
            token = data.token;
            userId = data.user_id;
            username = data.username;
            localStorage.setItem('player_token', token);
            localStorage.setItem('player_user_id', userId);
            localStorage.setItem('player_username', username);
            enterGame();
        } else {
            errEl.textContent = data.detail || '登录失败';
        }
    } catch (e) {
        errEl.textContent = '无法连接服务器';
    }
}

function enterGame() {
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('game-screen').style.display = '';
    document.getElementById('display-username').textContent = username;
    connectWS();
}

// ─── WebSocket ───

function connectWS() {
    if (ws) { ws.close(); ws = null; }
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        ws.send(JSON.stringify({ token }));
    };

    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'game_state') {
            handleGameState(msg.data, msg.user_info);
        } else if (msg.type === 'error') {
            showToast(msg.message);
        } else if (msg.type === 'chat') {
            addChatMessage(msg.data);
        }
    };

    ws.onclose = (e) => {
        if (e.code === 4002) {
            showToast('认证失败，请重新登录');
            logout();
            return;
        }
        setTimeout(connectWS, 3000);
    };

    ws.onerror = () => {};
}

function wsSend(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
    }
}

function logout() {
    localStorage.removeItem('player_token');
    localStorage.removeItem('player_user_id');
    localStorage.removeItem('player_username');
    if (ws) ws.close();
    location.reload();
}

// ─── 游戏状态渲染 ───

function handleGameState(state, userInfo) {
    if (userInfo) {
        userId = userInfo.user_id;
        username = userInfo.username;
    }
    gameState = state;

    mySeat = -1;
    const myPlayer = state.players.find(p => p.user_id === userId);
    if (myPlayer) mySeat = myPlayer.seat;

    renderTable(state);
    renderMyCards(state);
    renderActions(state);
    renderTopBar(state);

    // ★ 摊牌时弹出结果, 不自动关闭
    if (state.event === 'showdown' && state.last_hand_results) {
        showShowdown(state.last_hand_results);
    }
    // round_end 时不再自动关闭 showdown
}

function renderTopBar(state) {
    const myP = state.players.find(p => p.user_id === userId);
    document.getElementById('display-chips').textContent = myP ? `$${myP.chips}` : '';
    document.getElementById('display-blinds').textContent = `盲注 ${state.small_blind}/${state.big_blind}`;
    document.getElementById('display-hand-num').textContent = state.hand_number > 0 ? `#${state.hand_number}` : '';
}

function renderTable(state) {
    const seatsCount = state.seats_count || 6;

    // ★ 先清除旧的计时器
    removeTimer();

    for (let i = 0; i < 6; i++) {
        const el = document.getElementById(`seat-${i}`);
        if (i >= seatsCount) { el.style.display = 'none'; continue; }
        el.style.display = '';

        const p = state.players.find(x => x.seat === i);
        el.className = `seat seat-pos-${i}`;

        if (!p) {
            el.innerHTML = '<span style="font-size:20px">+</span><span style="font-size:11px">坐下</span>';
            continue;
        }

        el.classList.add('occupied');
        if (p.status === 'folded') el.classList.add('folded');
        if (state.current_player_seat === i && state.phase !== 'waiting' && state.phase !== 'showdown' && state.phase !== 'settling') {
            el.classList.add('active-turn');
        }
        if (state.dealer_seat === i) el.classList.add('dealer');
        if (state.small_blind_seat === i && state.phase !== 'waiting') el.classList.add('sb');
        if (state.big_blind_seat === i && state.phase !== 'waiting') el.classList.add('bb');

        let cardsHtml = '';
        if (state.phase !== 'waiting') {
            if (p.hole_cards) {
                cardsHtml = p.hole_cards.map(c => makeCardHtml(c, true)).join('');
            } else if (p.hole_cards_count > 0) {
                cardsHtml = '<div class="card-back card-small"></div>'.repeat(p.hole_cards_count);
            }
        }

        let actionText = p.last_action || '';
        if (p.status === 'all_in') actionText = '全押';

        el.innerHTML = `
            ${cardsHtml ? `<div class="seat-cards">${cardsHtml}</div>` : ''}
            <span class="seat-name">${p.username}</span>
            <span class="seat-chips">$${p.chips}</span>
            ${actionText ? `<span class="seat-action">${actionText}</span>` : ''}
            ${p.current_bet > 0 ? `<span class="seat-bet">${p.current_bet}</span>` : ''}
            ${((state.phase === 'waiting' || state.phase === 'settling') && p.is_ready) ? '<span class="ready-badge">已准备</span>' : ''}
        `;
    }

    // 公共牌 (服务端控制节奏, 前端直接显示)
    const ccEl = document.getElementById('community-cards');
    ccEl.innerHTML = state.community_cards.map(c => makeCardHtml(c, false)).join('');

    // 底池
    document.getElementById('pot-display').textContent = `底池: $${state.main_pot}`;

    // ★ 把计时器挂到当前行动玩家座位上
    if (state.phase !== 'waiting' && state.phase !== 'showdown' && state.phase !== 'settling' && state.current_player_seat >= 0) {
        attachTimer(state.current_player_seat, state.turn_timeout);
    } else {
        stopTurnTimer();
    }
}

function makeCardHtml(card, isSmall) {
    const sizeClass = isSmall ? 'card-small' : '';
    const isRed = card.suit === '♥' || card.suit === '♦';
    const colorClass = isRed ? 'red' : 'black';
    return `<div class="card ${sizeClass} ${colorClass}">
        <span class="card-rank">${card.rank}</span>
        <span class="card-suit">${card.suit}</span>
    </div>`;
}

function renderMyCards(state) {
    const myP = state.players.find(p => p.user_id === userId);
    const cardsEl = document.getElementById('my-hole-cards');
    const infoEl = document.getElementById('my-hand-info');

    if (!myP || !myP.hole_cards || myP.hole_cards.length === 0) {
        cardsEl.innerHTML = '';
        infoEl.textContent = '';
        return;
    }

    // ★ 手牌不做动画, 只在内容变化时才重新渲染
    const newHtml = myP.hole_cards.map(c => makeCardHtml(c, false)).join('');
    if (cardsEl.dataset.cards !== JSON.stringify(myP.hole_cards)) {
        cardsEl.innerHTML = newHtml;
        cardsEl.dataset.cards = JSON.stringify(myP.hole_cards);
    }
    infoEl.textContent = '';
}

function renderActions(state) {
    const actionBar = document.getElementById('action-bar');
    const seatActions = document.getElementById('seat-actions');
    const myP = state.players.find(p => p.user_id === userId);

    if (!myP) {
        actionBar.style.display = 'none';
        seatActions.style.display = 'none';
        return;
    }

    if (state.phase === 'waiting' || state.phase === 'settling') {
        actionBar.style.display = 'none';
        seatActions.style.display = '';
        const readyBtn = document.getElementById('btn-ready');
        readyBtn.textContent = myP.is_ready ? '取消准备' : '准备';
        readyBtn.style.background = myP.is_ready ? '#e67e22' : '#2ecc71';
        return;
    }

    seatActions.style.display = 'none';

    if (state.actions && state.actions.length > 0) {
        actionBar.style.display = '';
        const btnsEl = document.getElementById('action-buttons');
        const raiseEl = document.getElementById('raise-slider');
        btnsEl.innerHTML = '';
        raiseEl.style.display = 'none';

        state.actions.forEach(a => {
            const btn = document.createElement('button');
            btn.className = 'btn-action';
            btn.textContent = a.label;

            if (a.action === 'fold') {
                btn.className += ' btn-fold';
                btn.onclick = () => wsSend({ type: 'action', action: 'fold' });
            } else if (a.action === 'check') {
                btn.className += ' btn-check';
                btn.onclick = () => wsSend({ type: 'action', action: 'check' });
            } else if (a.action === 'call') {
                btn.className += ' btn-call';
                btn.onclick = () => wsSend({ type: 'action', action: 'call' });
            } else if (a.action === 'raise') {
                btn.className += ' btn-raise';
                btn.onclick = () => {
                    raiseEl.style.display = raiseEl.style.display === 'none' ? 'flex' : 'none';
                    const rangeEl = document.getElementById('raise-range');
                    const inputEl = document.getElementById('raise-input');
                    rangeEl.min = a.min;
                    rangeEl.max = a.max;
                    rangeEl.value = a.min;
                    inputEl.min = a.min;
                    inputEl.max = a.max;
                    inputEl.value = a.min;
                    rangeEl.oninput = () => { inputEl.value = rangeEl.value; };
                    inputEl.oninput = () => { rangeEl.value = inputEl.value; };
                };
            } else if (a.action === 'allin') {
                btn.className += ' btn-allin';
                btn.onclick = () => wsSend({ type: 'action', action: 'allin' });
            }

            btnsEl.appendChild(btn);
        });
    } else {
        actionBar.style.display = 'none';
    }
}

function doRaise() {
    const val = parseInt(document.getElementById('raise-input').value);
    if (val) {
        wsSend({ type: 'action', action: 'raise', amount: val });
        document.getElementById('raise-slider').style.display = 'none';
    }
}

// ─── 座位交互 ───

function clickSeat(seat) {
    if (!gameState) return;
    const occupied = gameState.players.find(p => p.seat === seat);
    if (occupied) return;
    if (mySeat >= 0) {
        showToast('你已经坐在座位上了');
        return;
    }
    wsSend({ type: 'sit_down', seat });
}

function toggleReady() {
    wsSend({ type: 'ready' });
}

function doStandUp() {
    wsSend({ type: 'stand_up' });
}

// ─── 计时器 (挂到座位上) ───

function removeTimer() {
    const old = document.getElementById('seat-timer');
    if (old) old.remove();
    currentTimerSeat = -1;
}

function attachTimer(seatIndex, seconds) {
    // 如果已经在同一个座位上且计时器还在跑, 不重建
    if (currentTimerSeat === seatIndex && turnTimerInterval) return;

    removeTimer();
    stopTurnTimer();

    const seatEl = document.getElementById(`seat-${seatIndex}`);
    if (!seatEl) return;

    // 创建计时器DOM, 挂到座位上
    const timerDiv = document.createElement('div');
    timerDiv.id = 'seat-timer';
    timerDiv.className = 'seat-timer';
    timerDiv.innerHTML = `
        <div class="seat-timer-bar-bg">
            <div id="seat-timer-bar" class="seat-timer-bar"></div>
        </div>
        <span id="seat-timer-text" class="seat-timer-text">${seconds}</span>
    `;
    seatEl.appendChild(timerDiv);
    currentTimerSeat = seatIndex;

    // 启动倒计时
    turnTimerEnd = Date.now() + seconds * 1000;
    const barEl = document.getElementById('seat-timer-bar');
    const textEl = document.getElementById('seat-timer-text');

    turnTimerInterval = setInterval(() => {
        const remaining = Math.max(0, turnTimerEnd - Date.now());
        const pct = (remaining / (seconds * 1000)) * 100;
        if (barEl) {
            barEl.style.width = pct + '%';
            if (pct < 30) barEl.style.background = '#e74c3c';
            else if (pct < 60) barEl.style.background = '#f39c12';
            else barEl.style.background = '#4caf50';
        }
        if (textEl) textEl.textContent = Math.ceil(remaining / 1000);
        if (remaining <= 0) {
            stopTurnTimer();
        }
    }, 200);
}

function startTurnTimer(seconds) {
    // 兼容旧调用, 现在由 attachTimer 处理
}

function stopTurnTimer() {
    if (turnTimerInterval) {
        clearInterval(turnTimerInterval);
        turnTimerInterval = null;
    }
}

// ─── 摊牌结果 (不自动关闭) ───

function showShowdown(results) {
    const overlay = document.getElementById('showdown-overlay');
    const container = document.getElementById('showdown-results');
    container.innerHTML = '';

    const maxWon = Math.max(...results.map(r => r.won || 0));

    results.forEach(r => {
        const div = document.createElement('div');
        div.className = 'result-item' + (r.won > 0 ? ' winner' : '');

        let cardsHtml = '';
        if (r.hole_cards) {
            cardsHtml = r.hole_cards.map(c => makeCardHtml(c, true)).join('');
        }

        let bestHtml = '';
        if (r.best_hand && r.best_hand.best_five) {
            bestHtml = '<div class="result-cards">' +
                r.best_hand.best_five.map(c => makeCardHtml(c, true)).join('') +
                '</div>';
        }

        div.innerHTML = `
            <div><strong>${r.username}</strong></div>
            <div class="result-cards">${cardsHtml}</div>
            ${bestHtml}
            ${r.best_hand ? `<div class="result-hand-name">${r.best_hand.name} (${r.best_hand.name_en})</div>` : ''}
            ${r.won > 0 ? `<div class="result-won">+$${r.won}</div>` : ''}
            ${r.reason ? `<div style="color:#aaa;font-size:13px">${r.reason}</div>` : ''}
        `;
        container.appendChild(div);
    });

    overlay.style.display = '';
}

function closeShowdown() {
    document.getElementById('showdown-overlay').style.display = 'none';
}

// ─── 聊天 ───

function toggleChat() {
    const panel = document.getElementById('chat-panel');
    panel.style.display = panel.style.display === 'none' ? '' : 'none';
}

function sendChat() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (text) {
        wsSend({ type: 'chat', text });
        input.value = '';
    }
}

function addChatMessage(data) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'chat-msg';
    div.innerHTML = `<span class="chat-name">${data.username}:</span> ${escapeHtml(data.text)}`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ─── Toast 提示 ───

function showToast(msg) {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.style.cssText = `
            position:fixed; top:60px; left:50%; transform:translateX(-50%); z-index:999;
            background:#e94560; color:#fff; padding:10px 24px; border-radius:8px;
            font-size:14px; transition:opacity 0.3s; pointer-events:none;
        `;
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.opacity = '1';
    setTimeout(() => { toast.style.opacity = '0'; }, 2500);
}

// ─── 初始化 ───

window.onload = function() {
    if (token) {
        enterGame();
    }
    document.getElementById('login-password').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doLogin();
    });
};
