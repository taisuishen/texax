const API = window.location.origin;
const WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';

let token = localStorage.getItem('player_token') || '';
let userId = localStorage.getItem('player_user_id') || '';
let username = localStorage.getItem('player_username') || '';
const DEFAULT_AVATAR_ID = 'avatar-01';
const DEFAULT_AVATAR_URL = '/static/avatars/avatar-01.svg';
let avatarId = localStorage.getItem('player_avatar_id') || DEFAULT_AVATAR_ID;
let avatarOptions = [{ id: DEFAULT_AVATAR_ID, url: DEFAULT_AVATAR_URL }];
let registerAvatarId = DEFAULT_AVATAR_ID;
let profileAvatarId = avatarId;
let ws = null;
let gameState = null;
let mySeat = -1;
let turnTimerInterval = null;
let turnTimerEnd = 0;
let currentTimerSeat = -1;
let currentTurnId = 0;
let notifiedTurnId = 0;
let raisePanelTurnKey = '';
const warnedTurnIds = new Set();
let reconnectTimer = null;
const normalDocumentTitle = document.title;
let lastAnimatedHand = 0;
let renderedCommunityCount = 0;
let renderedHandNumber = 0;
let audioContext = null;
let audioEnabled = false;
let seenActions = new Map();
let dealingTimers = [];
let pendingReactionChoices = null;
let reactionExpiresAt = 0;
let decodedCheckBuffer = null;
let checkDecodePromise = null;
const mediaSounds = {
    shuffle: new Audio('/static/audio/shuffle.mp3'),
    win: new Audio('/static/audio/chips-win.mp3'),
    flip: new Audio('/static/audio/card-flip.mp3'),
};
mediaSounds.shuffle.preload = 'auto';
mediaSounds.win.preload = 'auto';
mediaSounds.flip.preload = 'auto';
mediaSounds.shuffle.volume = 0.55;
mediaSounds.win.volume = 0.7;
mediaSounds.flip.volume = 0.75;

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
            savePlayerSession(data);
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
    document.getElementById('display-avatar').src = avatarUrlById(avatarId);
    connectWS();
}

// ─── WebSocket ───

function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
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
        } else if (msg.type === 'table_message') {
            addTableMessage(msg.data);
        } else if (msg.type === 'message_history') {
            document.getElementById('chat-messages').innerHTML = '';
            msg.data.forEach(item => addTableMessage(item, false));
        } else if (msg.type === 'settlement') {
            showSettlement(msg.data);
        } else if (msg.type === 'profile_updated') {
            applyMyAvatar(msg.avatar_id);
        }
    };

    ws.onclose = (e) => {
        if (e.code === 4002) {
            showToast('认证失败，请重新登录');
            logout();
            return;
        }
        ws = null;
        reconnectTimer = setTimeout(connectWS, document.hidden ? 5000 : 1500);
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
    localStorage.removeItem('player_avatar_id');
    if (ws) ws.close();
    location.reload();
}

// ─── 游戏状态渲染 ───

function handleGameState(state, userInfo) {
    if (userInfo) {
        userId = userInfo.user_id;
        username = userInfo.username;
        if (userInfo.avatar_id) applyMyAvatar(userInfo.avatar_id);
    }
    gameState = state;
    applyDealerImage(state.dealer_image || '');
    if (state.last_hand_summary) {
        const summary = document.getElementById('last-hand-summary');
        summary.innerHTML = `<span>上一手</span><p>${escapeHtml(state.last_hand_summary)}</p>`;
    }

    mySeat = -1;
    const myPlayer = state.players.find(p => p.user_id === userId);
    if (myPlayer) mySeat = myPlayer.seat;
    if (myPlayer?.avatar_id && myPlayer.avatar_id !== avatarId) applyMyAvatar(myPlayer.avatar_id);

    renderTable(state);
    renderMyCards(state);
    renderActions(state);
    renderTopBar(state);
    updateActionSounds(state);

    if (state.event === 'hand_result' && state.last_hand_results && lastAnimatedHand !== state.hand_number) {
        lastAnimatedHand = state.hand_number;
        const results = state.last_hand_results;
        setTimeout(() => animateHandResult(results), 900);
        showReactionPicker(state.last_hand_results);
    }
    if (state.event === 'hand_start') {
        pendingReactionChoices = null;
        reactionExpiresAt = 0;
        simulateDealerDeal(state);
    }
    if (state.event === 'single_player_idle') {
        showToast('单人等待超时，座位已自动释放');
    }
    const rebuyModal = document.getElementById('rebuy-modal');
    if (myPlayer && myPlayer.pending_rebuy) {
        document.getElementById('rebuy-description').textContent =
            `离桌，或借入 ${state.rebuy_amount} 筹码继续（此前已借 ${myPlayer.rebuy_count} 次）`;
        rebuyModal.style.display = '';
    } else {
        rebuyModal.style.display = 'none';
    }
}

function renderTopBar(state) {
    const myP = state.players.find(p => p.user_id === userId);
    document.getElementById('display-chips').textContent = myP ? `$${myP.chips}` : '';
    document.getElementById('display-blinds').textContent = `盲注 ${state.small_blind}/${state.big_blind}`;
    document.getElementById('display-hand-num').textContent = state.hand_number > 0 ? `#${state.hand_number}` : '';
    const leaveButton = document.getElementById('btn-leave-after');
    leaveButton.style.display = myP && !myP.timed_out && !['waiting', 'settling'].includes(state.phase) ? '' : 'none';
    if (myP) {
        leaveButton.textContent = myP.leave_after_hand ? '取消离桌' : '本手后离桌';
        leaveButton.classList.toggle('leave-pending', myP.leave_after_hand);
    }
}

function savePlayerSession(data) {
    token = data.token;
    userId = data.user_id;
    username = data.username;
    avatarId = data.avatar_id || DEFAULT_AVATAR_ID;
    profileAvatarId = avatarId;
    localStorage.setItem('player_token', token);
    localStorage.setItem('player_user_id', userId);
    localStorage.setItem('player_username', username);
    localStorage.setItem('player_avatar_id', avatarId);
}

function showAuthMode(mode) {
    const registering = mode === 'register';
    document.getElementById('login-form').style.display = registering ? 'none' : '';
    document.getElementById('register-form').style.display = registering ? '' : 'none';
    document.getElementById('tab-login').classList.toggle('active', !registering);
    document.getElementById('tab-register').classList.toggle('active', registering);
}

async function doRegister() {
    const enteredUsername = document.getElementById('register-username').value.trim();
    const password = document.getElementById('register-password').value;
    const error = document.getElementById('register-error');
    error.textContent = '';
    if (enteredUsername.length < 2 || enteredUsername.length > 20) {
        error.textContent = '用户名长度需要在 2～20 个字符之间';
        return;
    }
    if (password.length < 4 || password.length > 72) {
        error.textContent = '密码长度需要在 4～72 个字符之间';
        return;
    }
    try {
        const response = await fetch(API + '/api/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: enteredUsername, password, avatar_id: registerAvatarId }),
        });
        const data = await response.json();
        if (!response.ok) {
            error.textContent = data.detail || '注册失败';
            return;
        }
        savePlayerSession(data);
        enterGame();
    } catch (e) {
        error.textContent = '无法连接服务器';
    }
}

async function loadAvatarOptions() {
    try {
        const response = await fetch(API + '/api/avatars');
        if (response.ok) avatarOptions = (await response.json()).avatars;
    } catch (e) {}
    if (!avatarOptions.some(avatar => avatar.id === registerAvatarId)) {
        registerAvatarId = avatarOptions[0]?.id || DEFAULT_AVATAR_ID;
    }
    renderAvatarPicker('register-avatar-picker', registerAvatarId, selectRegisterAvatar);
}

function avatarUrlById(id) {
    return avatarOptions.find(avatar => avatar.id === id)?.url || DEFAULT_AVATAR_URL;
}

function renderAvatarPicker(containerId, selectedId, onSelect) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    avatarOptions.forEach(avatar => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `avatar-option${avatar.id === selectedId ? ' selected' : ''}`;
        button.title = `选择头像 ${avatar.id.slice(-2)}`;
        const image = document.createElement('img');
        image.src = avatar.url;
        image.alt = '可选头像';
        button.appendChild(image);
        button.onclick = () => onSelect(avatar.id);
        container.appendChild(button);
    });
}

function selectRegisterAvatar(id) {
    registerAvatarId = id;
    renderAvatarPicker('register-avatar-picker', registerAvatarId, selectRegisterAvatar);
}

function renderTable(state) {
    const seatsCount = state.seats_count || 6;

    // ★ 先清除旧的计时器
    removeTimer();

    for (let i = 0; i < 9; i++) {
        const el = document.getElementById(`seat-${i}`);
        if (i >= seatsCount) { el.style.display = 'none'; continue; }
        el.style.display = '';

        const p = state.players.find(x => x.seat === i);
        const displaySeat = getDisplaySeat(i, seatsCount);
        el.className = `seat seat-pos-${displaySeat}`;
        el.dataset.tablePosition = displaySeat;

        if (!p) {
            el.innerHTML = '<span style="font-size:20px">+</span><span style="font-size:11px">坐下</span>';
            continue;
        }

        el.classList.add('occupied');
        if (p.user_id === userId) el.classList.add('my-seat');
        if (p.status === 'folded') el.classList.add('folded');
        if (state.current_player_seat === i && state.phase !== 'waiting' && state.phase !== 'showdown' && state.phase !== 'settling') {
            el.classList.add('active-turn');
        }
        if (state.dealer_seat === i) el.classList.add('dealer');
        if (state.small_blind_seat === i && state.phase !== 'waiting') el.classList.add('sb');
        if (state.big_blind_seat === i && state.phase !== 'waiting') el.classList.add('bb');

        let cardsHtml = '';
        if (state.phase !== 'waiting' && !state.dealing) {
            if (p.hole_cards) {
                cardsHtml = p.hole_cards.map(c => makeCardHtml(c, true)).join('');
            } else if (p.hole_cards_count > 0) {
                cardsHtml = '<div class="card-back card-small"></div>'.repeat(p.hole_cards_count);
            }
        }

        let actionText = p.last_action || '';
        if (p.status === 'all_in') actionText = '全押';
        const mobileActionText = p.current_bet > 0
            ? `${actionText || '下注'} ${p.current_bet}`
            : actionText;

        el.innerHTML = `
            ${cardsHtml ? `<div class="seat-cards">${cardsHtml}</div>` : ''}
            <button class="seat-avatar${p.user_id === userId ? ' editable' : ''}"
                title="${p.user_id === userId ? '修改头像' : '玩家头像'}"
                ${p.user_id === userId ? 'onclick="event.stopPropagation(); openAvatarModal()"' : ''}>
                <img src="${avatarUrlById(p.avatar_id)}" alt="玩家头像">
            </button>
            <span class="seat-name">${escapeHtml(p.username)}</span>
            <span class="seat-chips">$${p.chips}</span>
            ${actionText ? `<span class="seat-action">${actionText}</span>` : ''}
            ${mobileActionText ? `<span class="seat-mobile-action">${mobileActionText}</span>` : ''}
            ${p.current_bet > 0 ? `<span class="seat-bet"><i class="chip-icon"></i><b>${p.current_bet}</b></span>` : ''}
            ${((state.phase === 'waiting' || state.phase === 'settling') && p.is_ready) ? '<span class="ready-badge">已准备</span>' : ''}
            ${(p.user_id === userId && state.pending_show_choice) ? `
                <button class="show-cards-eye" title="亮出手牌" aria-label="亮出手牌"
                    onclick="event.stopPropagation(); chooseShow()">👁</button>` : ''}
        `;
    }

    restoreReactionPicker();

    renderCommunityBoard(state);

    // 底池
    document.getElementById('pot-display').textContent = `底池: $${state.main_pot}`;

    // ★ 把计时器挂到当前行动玩家座位上
    if (state.phase !== 'waiting' && state.phase !== 'showdown' && state.phase !== 'settling'
            && state.current_player_seat >= 0 && state.turn_remaining > 0) {
        attachTimer(
            state.current_player_seat,
            state.turn_remaining,
            state.turn_timeout,
            state.turn_id || 0
        );
    } else {
        stopTurnTimer();
        currentTurnId = 0;
        document.title = normalDocumentTitle;
    }
}

function getDisplaySeat(serverSeat, seatsCount) {
    const isPhonePortrait = window.matchMedia('(max-width: 480px) and (orientation: portrait)').matches;
    if (!isPhonePortrait || mySeat < 0) return serverSeat;

    const layouts = {
        2: [0, 4],
        3: [0, 3, 6],
        4: [0, 2, 5, 7],
        5: [0, 2, 4, 6, 8],
        6: [0, 1, 3, 5, 7, 8],
        7: [0, 1, 3, 4, 6, 7, 8],
        8: [0, 1, 2, 3, 5, 6, 7, 8],
        9: [0, 1, 2, 3, 4, 5, 6, 7, 8],
    };
    const layout = layouts[seatsCount] || layouts[9];
    const relativeSeat = (serverSeat - mySeat + seatsCount) % seatsCount;
    return layout[relativeSeat] ?? relativeSeat;
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

    if (state.dealing || !myP || !myP.hole_cards || myP.hole_cards.length === 0) {
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
    const readyBtn = document.getElementById('btn-ready');
    const standBtn = document.getElementById('btn-standup');

    if (!myP) {
        closeRaisePanel();
        actionBar.style.display = 'none';
        seatActions.style.display = 'none';
        return;
    }

    readyBtn.style.display = '';
    standBtn.style.display = '';

    if (state.phase === 'settling') {
        closeRaisePanel();
        actionBar.style.display = 'none';
        if (state.players.length === 1) {
            seatActions.style.display = '';
            readyBtn.style.display = 'none';
            return;
        }
        if (myP && !myP.is_ready && !myP.pending_rebuy && myP.chips > 0) {
            seatActions.style.display = '';
            readyBtn.textContent = '准备加入下一手';
            readyBtn.style.background = '#2ecc71';
        } else {
            seatActions.style.display = 'none';
        }
        return;
    }

    if (state.phase === 'waiting') {
        closeRaisePanel();
        actionBar.style.display = 'none';
        seatActions.style.display = '';
        readyBtn.textContent = myP.is_ready ? '取消准备' : '准备';
        readyBtn.style.background = myP.is_ready ? '#e67e22' : '#2ecc71';
        return;
    }

    seatActions.style.display = 'none';

    if (state.actions && state.actions.length > 0) {
        actionBar.style.display = '';
        const btnsEl = document.getElementById('action-buttons');
        const raiseEl = document.getElementById('raise-slider');
        const turnKey = `${state.hand_number || 0}:${state.turn_id || 0}:${state.current_player_seat}`;
        const raiseAction = state.actions.find(action => action.action === 'raise');
        const keepRaiseOpen = raiseEl.style.display !== 'none'
            && raisePanelTurnKey === turnKey && Boolean(raiseAction);
        btnsEl.innerHTML = '';
        if (!keepRaiseOpen) closeRaisePanel();

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
                    const opening = raiseEl.style.display === 'none';
                    if (opening) {
                        raisePanelTurnKey = turnKey;
                        raiseEl.style.display = 'flex';
                        document.body.classList.add('raise-panel-open');
                        configureRaiseControls(a, state, myP);
                    } else {
                        closeRaisePanel();
                    }
                };
            } else if (a.action === 'allin') {
                btn.className += ' btn-allin';
                btn.onclick = () => wsSend({ type: 'action', action: 'allin' });
            }

            btnsEl.appendChild(btn);
        });
    } else {
        closeRaisePanel();
        actionBar.style.display = 'none';
    }
}

function normalizeRaiseAmount(value, min, max, step) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return min;
    const stepped = min + Math.round((numeric - min) / step) * step;
    return Math.max(min, Math.min(max, stepped));
}

function updateRaisePresetSelection(value) {
    document.querySelectorAll('.raise-preset').forEach(button => {
        button.classList.toggle('selected', Number(button.dataset.amount) === Number(value));
    });
}

function setRaiseAmount(value) {
    const rangeEl = document.getElementById('raise-range');
    const inputEl = document.getElementById('raise-input');
    const min = Number(inputEl.min);
    const max = Number(inputEl.max);
    const step = Number(inputEl.step) || 1;
    const amount = normalizeRaiseAmount(value, min, max, step);
    rangeEl.value = amount;
    inputEl.value = amount;
    document.getElementById('raise-amount-display').textContent = amount.toLocaleString();
    updateRaisePresetSelection(amount);
}

function adjustRaiseBy(direction) {
    const inputEl = document.getElementById('raise-input');
    const step = Number(inputEl.step) || 1;
    setRaiseAmount(Number(inputEl.value) + Number(direction) * step);
}

function closeRaisePanel() {
    document.getElementById('raise-slider').style.display = 'none';
    document.body.classList.remove('raise-panel-open');
    raisePanelTurnKey = '';
}

function configureRaiseControls(action, state, myPlayer) {
    const rangeEl = document.getElementById('raise-range');
    const inputEl = document.getElementById('raise-input');
    const presetsEl = document.getElementById('raise-presets');
    const min = Math.min(Number(action.min), Number(action.max));
    const max = Number(action.max);
    const step = Math.max(1, Number(state.big_blind) || 1);

    rangeEl.min = min;
    rangeEl.max = max;
    rangeEl.step = step;
    inputEl.min = min;
    inputEl.max = max;
    inputEl.step = step;

    const callAmount = Math.max(0, Number(state.current_bet) - Number(myPlayer.current_bet));
    const potAfterCall = Number(state.main_pot) + callAmount;
    const candidates = [
        ['全押', max],
        ['满池', Number(state.current_bet) + potAfterCall],
        ['2/3池', Number(state.current_bet) + potAfterCall * (2 / 3)],
        ['1/2池', Number(state.current_bet) + potAfterCall * 0.5],
        ['最小', min],
    ];

    presetsEl.innerHTML = '';
    const seenAmounts = new Set();
    for (const [label, rawAmount] of candidates) {
        const amount = normalizeRaiseAmount(rawAmount, min, max, step);
        if (seenAmounts.has(amount)) continue;
        seenAmounts.add(amount);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'raise-preset';
        button.dataset.amount = amount;
        button.innerHTML = `<span>${label}</span><strong>${amount.toLocaleString()}</strong>`;
        button.onclick = () => setRaiseAmount(amount);
        presetsEl.appendChild(button);
    }

    rangeEl.oninput = () => {
        inputEl.value = rangeEl.value;
        document.getElementById('raise-amount-display').textContent = Number(rangeEl.value).toLocaleString();
        updateRaisePresetSelection(rangeEl.value);
    };
    inputEl.oninput = () => {
        document.getElementById('raise-amount-display').textContent = (Number(inputEl.value) || 0).toLocaleString();
        updateRaisePresetSelection(inputEl.value);
    };
    inputEl.onchange = () => setRaiseAmount(inputEl.value);
    inputEl.onfocus = () => inputEl.select();
    setRaiseAmount(min);
}

function doRaise() {
    const inputEl = document.getElementById('raise-input');
    const val = Number(inputEl.value);
    if (Number.isFinite(val) && val >= Number(inputEl.min) && val <= Number(inputEl.max)) {
        wsSend({ type: 'action', action: 'raise', amount: Math.round(val) });
        closeRaisePanel();
    } else {
        showToast(`请输入 ${inputEl.min} 到 ${inputEl.max} 之间的加注额`);
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

function toggleLeaveAfterHand() {
    wsSend({ type: 'leave_after_hand' });
}

function applyMyAvatar(id) {
    if (!avatarOptions.some(avatar => avatar.id === id)) id = DEFAULT_AVATAR_ID;
    avatarId = id;
    profileAvatarId = id;
    localStorage.setItem('player_avatar_id', id);
    const image = document.getElementById('display-avatar');
    if (image) image.src = avatarUrlById(id);
}

function openAvatarModal() {
    profileAvatarId = avatarId;
    renderAvatarPicker('profile-avatar-picker', profileAvatarId, selectProfileAvatar);
    document.getElementById('avatar-modal').style.display = '';
}

function selectProfileAvatar(id) {
    profileAvatarId = id;
    renderAvatarPicker('profile-avatar-picker', profileAvatarId, selectProfileAvatar);
}

function saveAvatar() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        showToast('正在重新连接，请稍后再试');
        return;
    }
    wsSend({ type: 'update_avatar', avatar_id: profileAvatarId });
    closeAvatarModal();
}

function closeAvatarModal() {
    document.getElementById('avatar-modal').style.display = 'none';
}

// ─── 计时器 (挂到座位上) ───

function removeTimer() {
    const old = document.getElementById('seat-timer');
    if (old) old.remove();
    currentTimerSeat = -1;
}

function attachTimer(seatIndex, remainingSeconds, totalSeconds, turnId) {
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
        <span id="seat-timer-text" class="seat-timer-text">${remainingSeconds}</span>
    `;
    seatEl.appendChild(timerDiv);
    currentTimerSeat = seatIndex;
    currentTurnId = turnId;

    // 使用服务端剩余时间恢复倒计时，切回后台或重连后不会重新从 30 秒开始。
    turnTimerEnd = Date.now() + remainingSeconds * 1000;
    const barEl = document.getElementById('seat-timer-bar');
    const textEl = document.getElementById('seat-timer-text');

    if (seatIndex === mySeat && turnId && notifiedTurnId !== turnId) {
        notifiedTurnId = turnId;
        playYourTurnSound();
        if (document.hidden) document.title = '【轮到你】' + normalDocumentTitle;
    } else if (seatIndex !== mySeat) {
        document.title = normalDocumentTitle;
    }

    turnTimerInterval = setInterval(() => {
        const remaining = Math.max(0, turnTimerEnd - Date.now());
        const pct = Math.min(100, (remaining / (totalSeconds * 1000)) * 100);
        if (barEl) {
            barEl.style.width = pct + '%';
            if (pct < 30) barEl.style.background = '#e74c3c';
            else if (pct < 60) barEl.style.background = '#f39c12';
            else barEl.style.background = '#4caf50';
        }
        if (textEl) textEl.textContent = Math.ceil(remaining / 1000);
        if (seatIndex === mySeat && turnId && remaining > 0 && remaining <= 5000 && !warnedTurnIds.has(turnId)) {
            warnedTurnIds.add(turnId);
            playFiveSecondWarning();
        }
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

function animateHandResult(results) {
    playMediaSound('win');
    results.filter(r => r.won > 0).forEach((winner, index) => {
        const player = gameState.players.find(p => p.user_id === winner.user_id);
        const target = player ? document.getElementById(`seat-${player.seat}`) : null;
        if (!target) return;
        const table = document.querySelector('.poker-table').getBoundingClientRect();
        const rect = target.getBoundingClientRect();
        const chip = document.createElement('div');
        chip.className = 'flying-chip';
        chip.textContent = `+${winner.won}`;
        chip.style.left = `${table.left + table.width / 2}px`;
        chip.style.top = `${table.top + table.height / 2}px`;
        chip.style.setProperty('--chip-x', `${rect.left + rect.width / 2 - table.left - table.width / 2}px`);
        chip.style.setProperty('--chip-y', `${rect.top + rect.height / 2 - table.top - table.height / 2}px`);
        document.getElementById('chip-animation-layer').appendChild(chip);
        setTimeout(() => chip.remove(), 1500);
        if (winner.user_id === userId) showWinBanner(
            Math.max(0, winner.profit ?? winner.won), index * 100
        );
    });
}

function renderCommunityBoard(state) {
    const board = document.getElementById('community-cards');
    if (renderedHandNumber !== state.hand_number || board.children.length !== 5) {
        renderedHandNumber = state.hand_number;
        renderedCommunityCount = 0;
        board.innerHTML = Array.from({ length: 5 }, (_, index) => `
            <div class="community-card-slot" data-index="${index}" data-revealed="false">
                <div class="community-card-inner">
                    <div class="community-card-back"><div class="card-back"></div></div>
                    <div class="community-card-front"></div>
                </div>
            </div>`).join('');
    }
    state.community_cards.forEach((card, index) => {
        const slot = board.children[index];
        if (!slot || slot.dataset.revealed === 'true') return;
        slot.dataset.revealed = 'true';
        slot.querySelector('.community-card-front').innerHTML = makeCardHtml(card, false);
        playMediaSound('flip');
        const slowRiver = Boolean(state.all_in_runout && index === 4);
        slot.classList.toggle('slow-runout', slowRiver);
        const duration = slowRiver ? 1150 : 750;
        const face = slot.querySelector('.community-card-front');
        if (face.animate) {
            const animation = face.animate([
                { clipPath: 'polygon(0 0,0 0,0 0,0 0,0 0)', transform: 'translate(-4px,-4px) rotate(-2deg)', filter: 'brightness(1.15)' },
                { clipPath: 'polygon(0 0,72% 0,0 72%,0 72%,0 72%)', transform: 'translate(-2px,-2px) rotate(-1deg)', offset: .42 },
                { clipPath: 'polygon(0 0,100% 0,100% 38%,38% 100%,0 100%)', transform: 'translate(0,0) rotate(0)', offset: .74 },
                { clipPath: 'polygon(0 0,100% 0,100% 100%,0 100%,0 100%)', transform: 'translate(0,0) rotate(0)', filter: 'brightness(1)' },
            ], { duration: duration + 180, easing: 'cubic-bezier(.22,.65,.25,1)', fill: 'forwards' });
            animation.finished.then(() => {
                slot.classList.add('corner-revealed');
                animation.cancel();
            }).catch(() => {});
        } else {
            slot.classList.add('corner-flipping');
            setTimeout(() => {
                slot.classList.remove('corner-flipping');
                slot.classList.add('corner-revealed');
            }, duration + 180);
        }
    });
    renderedCommunityCount = state.community_cards.length;
}

function showWinBanner(amount, delay = 0) {
    const banner = document.getElementById('win-banner');
    setTimeout(() => {
        banner.innerHTML = `YOU WIN<small>+${amount}</small>`;
        banner.style.display = '';
        setTimeout(() => { banner.style.display = 'none'; }, 1800);
    }, delay);
}

function chooseShow() {
    const button = document.querySelector('.show-cards-eye');
    if (button) {
        button.disabled = true;
        button.classList.add('selected');
    }
    wsSend({ type: 'show_cards_choice', show: true });
}

function doRebuy() { wsSend({ type: 'rebuy' }); }
function requestSettlement() { wsSend({ type: 'settlement' }); }
function closeSettlement() { document.getElementById('settlement-modal').style.display = 'none'; }

function showSettlement(rows) {
    const body = document.getElementById('settlement-results');
    body.innerHTML = '<table><thead><tr><th>玩家</th><th>初始</th><th>借入</th><th>最终</th><th>净输赢</th></tr></thead><tbody>' +
        rows.map(r => `<tr><td>${escapeHtml(r.username)}</td><td>${r.initial_buyin}</td>` +
            `<td>${r.rebuy_total} (${r.rebuy_count}次)</td><td>${r.final_chips}</td>` +
            `<td class="${r.net >= 0 ? 'profit' : 'loss'}">${r.net >= 0 ? '+' : ''}${r.net}</td></tr>`).join('') +
        '</tbody></table>';
    document.getElementById('settlement-modal').style.display = '';
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

function sendQuickChat(text) {
    wsSend({ type: 'chat', text });
}

function addTableMessage(data, showBubble = true) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `chat-msg ${data.kind === 'system' ? 'system-msg' : ''}`;
    div.innerHTML = data.kind === 'system'
        ? `<span class="system-name">系统：</span>${escapeHtml(data.text)}`
        : `<span class="chat-name">${escapeHtml(data.username)}:</span> ${escapeHtml(data.text)}`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    if (data.kind === 'system' && data.event === 'hand_result') {
        const summary = document.getElementById('last-hand-summary');
        summary.innerHTML = `<span>上一手</span><p>${escapeHtml(data.text)}</p>`;
    } else if (showBubble && data.kind === 'chat' && data.user_id) {
        showPlayerBubble(data.user_id, data.text, 'player-chat');
    }
}

function showPlayerBubble(targetUserId, text, extraClass = '') {
    if (!gameState) return;
    const player = gameState.players.find(p => p.user_id === targetUserId);
    if (!player) return;
    const seat = document.getElementById(`seat-${player.seat}`);
    if (!seat) return;
    const old = seat.querySelector('.speech-bubble');
    if (old) old.remove();
    const bubble = document.createElement('div');
    bubble.className = `speech-bubble ${extraClass}`;
    bubble.textContent = text;
    seat.appendChild(bubble);
    requestAnimationFrame(() => bubble.classList.add('visible'));
    setTimeout(() => {
        bubble.classList.remove('visible');
        setTimeout(() => bubble.remove(), 220);
    }, 6000);
}

function showReactionPicker(results) {
    const me = gameState?.players.find(p => p.user_id === userId);
    if (!me || me.total_bet <= 0) return;
    document.querySelectorAll('.reaction-picker').forEach(el => el.remove());
    const won = results.some(r => r.user_id === userId && r.won > 0);
    const choices = won
        ? ['Good luck! 🍀', 'Good game! 😎', '承让啦 😄', '运气不错 ✨']
        : ['Nice hand! 👍', '下一把赢回来 🔥', '差一点 😅', '这把算你的 😏'];
    pendingReactionChoices = choices;
    reactionExpiresAt = Date.now() + 6500;
    renderReactionPicker(choices);
}

function restoreReactionPicker() {
    if (!pendingReactionChoices || Date.now() >= reactionExpiresAt) return;
    renderReactionPicker(pendingReactionChoices);
}

function renderReactionPicker(choices) {
    const me = gameState?.players.find(p => p.user_id === userId);
    if (!me) return;
    const seat = document.getElementById(`seat-${me.seat}`);
    if (!seat) return;
    if (seat.querySelector('.reaction-picker')) return;
    const picker = document.createElement('div');
    picker.className = 'reaction-picker';
    picker.innerHTML = choices.map(text =>
        `<button onclick="sendReaction(this, '${text.replaceAll("'", "&#39;")}')">${text}</button>`
    ).join('');
    seat.appendChild(picker);
    setTimeout(() => picker.remove(), Math.max(0, reactionExpiresAt - Date.now()));
}

function sendReaction(button, text) {
    const picker = button.closest('.reaction-picker');
    if (picker) picker.remove();
    pendingReactionChoices = null;
    reactionExpiresAt = 0;
    sendQuickChat(text);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ─── 荷官、模拟发牌与音效 ───

function enableAudio() {
    if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === 'suspended') audioContext.resume();
    audioEnabled = true;
    Object.values(mediaSounds).forEach(audio => audio.load());
    loadCheckSound();
}

function loadCheckSound() {
    if (checkDecodePromise || !audioContext) return checkDecodePromise;
    checkDecodePromise = fetch('/static/audio/check-tap.mp3?v=5')
        .then(response => {
            if (!response.ok) throw new Error(`check sound HTTP ${response.status}`);
            return response.arrayBuffer();
        })
        .then(buffer => audioContext.decodeAudioData(buffer))
        .then(decoded => { decodedCheckBuffer = decoded; })
        .catch(error => { console.warn('敲桌音效解码失败，将使用回退音效', error); });
    return checkDecodePromise;
}

function playMediaSound(name, onError = null) {
    if (!audioEnabled) return;
    const audio = mediaSounds[name];
    if (!audio) return;
    audio.pause();
    audio.currentTime = 0;
    const playback = audio.play();
    if (playback && typeof playback.catch === 'function') {
        playback.catch(onError || (() => {}));
    }
    return playback;
}

function playSyntheticKnock() {
    playTone(125, .09, 'triangle', .13);
    playTone(92, .12, 'triangle', .1, .1);
}

function playCheckSound() {
    if (!audioEnabled || !audioContext) return;
    if (!decodedCheckBuffer) {
        playSyntheticKnock();
        loadCheckSound();
        return;
    }
    const source = audioContext.createBufferSource();
    const gain = audioContext.createGain();
    source.buffer = decodedCheckBuffer;
    gain.gain.value = 1.25;
    source.connect(gain).connect(audioContext.destination);
    source.start();
}

function playTone(frequency, duration, type = 'sine', volume = 0.05, delay = 0) {
    if (!audioEnabled || !audioContext) return;
    const start = audioContext.currentTime + delay;
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(volume, start);
    gain.gain.exponentialRampToValueAtTime(0.001, start + duration);
    oscillator.connect(gain).connect(audioContext.destination);
    oscillator.start(start);
    oscillator.stop(start + duration);
}

function playYourTurnSound() {
    if (!audioEnabled || !audioContext) return;
    if (audioContext.state === 'suspended') audioContext.resume().catch(() => {});
    playTone(660, .12, 'sine', .055);
    playTone(880, .16, 'sine', .05, .14);
}

function playFiveSecondWarning() {
    if (!audioEnabled || !audioContext) return;
    if (audioContext.state === 'suspended') audioContext.resume().catch(() => {});
    playTone(520, .09, 'triangle', .045);
    playTone(520, .09, 'triangle', .04, .15);
}

function playDealSound() {
    if (!audioEnabled || !audioContext) return;
    const length = Math.floor(audioContext.sampleRate * .075);
    const buffer = audioContext.createBuffer(1, length, audioContext.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / length) * .18;
    const source = audioContext.createBufferSource();
    const filter = audioContext.createBiquadFilter();
    filter.type = 'bandpass'; filter.frequency.value = 1700;
    source.buffer = buffer; source.connect(filter).connect(audioContext.destination); source.start();
}

function updateActionSounds(state) {
    if (['connected', 'sync'].includes(state.event)) {
        state.players.forEach(player => seenActions.set(player.user_id, player.action_serial || 0));
        return;
    }
    state.players.forEach(player => {
        const serial = player.action_serial || 0;
        if (!player.last_action || serial <= (seenActions.get(player.user_id) || 0)) return;
        seenActions.set(player.user_id, serial);
        if (player.last_action === '过牌') {
            playCheckSound();
        }
        if (['跟注', '加注', '全押'].includes(player.last_action)) playMediaSound('win');
    });
}

function simulateDealerDeal(state) {
    dealingTimers.forEach(clearTimeout);
    dealingTimers = [];
    document.querySelectorAll('.dealt-card').forEach(card => card.remove());
    document.querySelectorAll('.shuffle-animation').forEach(animation => animation.remove());
    const players = [...state.players].filter(p => p.hole_cards_count > 0 || p.hole_cards?.length > 0)
        .sort((a, b) => a.seat - b.seat);
    if (!players.length) return;
    playMediaSound('shuffle');
    const dealer = document.getElementById('dealer-avatar');
    if (dealer) {
        const shuffleAnimation = document.createElement('div');
        shuffleAnimation.className = 'shuffle-animation';
        shuffleAnimation.innerHTML = '<i></i><i></i><i></i>';
        dealer.appendChild(shuffleAnimation);
        const removeShuffle = setTimeout(() => shuffleAnimation.remove(), 1950);
        dealingTimers.push(removeShuffle);
    }
    const split = players.findIndex(p => p.seat > state.dealer_seat);
    const order = split > -1 ? players.slice(split).concat(players.slice(0, split)) : players;
    order.forEach(player => document.getElementById(`seat-${player.seat}`)?.classList.add('dealing-cards'));
    const sequence = [...order, ...order];
    sequence.forEach((player, index) => {
        const timer = setTimeout(() => {
            const dealer = document.getElementById('dealer-avatar');
            const seat = document.getElementById(`seat-${player.seat}`);
            if (!dealer || !seat) return;
            const from = dealer.getBoundingClientRect();
            const to = seat.getBoundingClientRect();
            const card = document.createElement('div');
            card.className = 'dealt-card';
            card.style.left = `${from.left + from.width / 2}px`;
            card.style.top = `${from.top + from.height / 2}px`;
            card.style.setProperty('--deal-x', `${to.left + to.width / 2 - from.left - from.width / 2}px`);
            card.style.setProperty('--deal-y', `${to.top + to.height / 2 - from.top - from.height / 2}px`);
            document.body.appendChild(card);
            playDealSound();
            setTimeout(() => card.remove(), 480);
            if (index >= order.length) setTimeout(() => seat.classList.remove('dealing-cards'), 330);
        }, 2000 + index * 230);
        dealingTimers.push(timer);
    });
}

function applyDealerImage(source) {
    const image = document.getElementById('dealer-image');
    const fallback = document.getElementById('dealer-fallback');
    if (source && image.getAttribute('src') !== source) image.src = source;
    if (!source) image.removeAttribute('src');
    image.style.display = source ? '' : 'none';
    fallback.style.display = source ? 'none' : '';
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
    document.addEventListener('pointerdown', enableAudio, { once: true });
    loadAvatarOptions().then(() => {
        const image = document.getElementById('display-avatar');
        if (image) image.src = avatarUrlById(avatarId);
    });
    if (token) {
        enterGame();
    }
    document.getElementById('login-password').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doLogin();
    });
    document.getElementById('register-password').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doRegister();
    });
};

function syncAfterReturningToTable() {
    document.title = normalDocumentTitle;
    if (!token) return;
    if (!ws || ws.readyState === WebSocket.CLOSED) {
        connectWS();
    } else if (ws.readyState === WebSocket.OPEN) {
        wsSend({ type: 'get_state' });
    }
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) syncAfterReturningToTable();
});
window.addEventListener('focus', syncAfterReturningToTable);
window.addEventListener('orientationchange', () => {
    setTimeout(() => {
        if (gameState) renderTable(gameState);
    }, 180);
});
