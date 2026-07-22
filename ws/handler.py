"""
WebSocket 连接管理和消息处理
"""
import json
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect
from auth import decode_token, verify_password, create_player_token
import redis_client
from game.engine import GameEngine, GamePhase, PlayerStatus
from avatar_config import DEFAULT_AVATAR_ID, is_valid_avatar

logger = logging.getLogger("poker.ws")

RECONNECT_GRACE_SECONDS = 120  # 手机切后台或网络抖动时保留座位两分钟


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.connections: dict[str, WebSocket] = {}  # user_id -> WebSocket
        self.ws_to_user: dict[int, str] = {}         # ws.id -> user_id
        self.game_engine: GameEngine | None = None
        self._disconnect_timers: dict[str, asyncio.Task] = {}  # user_id -> 延迟踢出task
        self.session: dict = {"started_at": datetime.now(timezone.utc).isoformat(),
                              "status": "active", "players": {}}

    def set_engine(self, engine: GameEngine):
        self.game_engine = engine

    async def load_session(self):
        saved = await redis_client.get_session()
        if saved and saved.get("status") == "active":
            self.session = saved

    async def _save_session_player(self, player):
        row = self.session["players"].setdefault(player.user_id, {
            "username": player.username, "initial_buyin": player.initial_buyin,
            "rebuy_count": 0, "rebuy_total": 0,
        })
        row.update({"username": player.username, "final_chips": player.chips,
                    "rebuy_count": player.rebuy_count, "rebuy_total": player.rebuy_total,
                    "net": player.chips - row["initial_buyin"] - player.rebuy_total})
        await redis_client.save_session(self.session)

    async def publish_message(self, message: dict):
        await redis_client.append_table_message(message)
        await self.broadcast({"type": "table_message", "data": message})

    async def connect(self, ws: WebSocket, user_id: str):
        # ★ 如果有待执行的断线踢出，取消它（用户刷新重连了）
        timer = self._disconnect_timers.pop(user_id, None)
        if timer and not timer.done():
            timer.cancel()
            logger.info(f"Player {user_id} reconnected, cancelled disconnect timer")

        # 替换旧连接
        old_ws = self.connections.get(user_id)
        if old_ws:
            # 清理旧ws映射
            old_ws_id = None
            for wid, uid in list(self.ws_to_user.items()):
                if uid == user_id:
                    old_ws_id = wid
                    break
            if old_ws_id:
                self.ws_to_user.pop(old_ws_id, None)

        self.connections[user_id] = ws
        self.ws_to_user[id(ws)] = user_id
        logger.info(f"Player {user_id} connected")

    async def disconnect(self, ws: WebSocket):
        user_id = self.ws_to_user.pop(id(ws), None)
        if not user_id:
            return

        # 如果当前连接已被新ws替换（重连），不做任何处理
        current_ws = self.connections.get(user_id)
        if current_ws is not ws:
            logger.info(f"Player {user_id} old ws closed (already reconnected)")
            return

        self.connections.pop(user_id, None)

        if not self.game_engine:
            return

        player = self.game_engine.get_player(user_id)
        if not player:
            logger.info(f"Player {user_id} disconnected (not seated)")
            return

        # 保存筹码
        user_data = await redis_client.get_user(user_id)
        if user_data:
            user_data["chips"] = player.chips
            await redis_client.save_user(user_id, user_data)

        # ★ 启动延迟踢出（给玩家重连的机会）
        self._disconnect_timers[user_id] = asyncio.create_task(
            self._delayed_disconnect(user_id)
        )
        logger.info(f"Player {user_id} disconnected, grace period {RECONNECT_GRACE_SECONDS}s started")

    async def _delayed_disconnect(self, user_id: str):
        """延迟踢出：宽限期内没重连则真正踢掉"""
        try:
            await asyncio.sleep(RECONNECT_GRACE_SECONDS)
        except asyncio.CancelledError:
            return  # 重连了，取消踢出

        # 宽限期到了还没重连
        self._disconnect_timers.pop(user_id, None)

        # 再次确认没有重连
        if user_id in self.connections:
            return

        engine = self.game_engine
        if not engine:
            return

        player = engine.get_player(user_id)
        if not player:
            return

        logger.info(f"Player {user_id} grace period expired, removing from table")

        if engine.phase != GamePhase.WAITING:
            if player.status == PlayerStatus.ACTIVE:
                if player.seat == engine.current_player_seat:
                    await engine.player_action(user_id, "fold")
                else:
                    player.status = PlayerStatus.FOLDED
        else:
            engine.stand_up(user_id)

        await self.broadcast_game_state("player_leave")

    async def send_personal(self, user_id: str, message: dict):
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception:
                pass

    async def broadcast(self, message: dict):
        dead = []
        for uid, ws in self.connections.items():
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self.connections.pop(uid, None)

    async def broadcast_game_state(self, event: str = "update", engine: GameEngine | None = None):
        """向每个玩家发送其个人视角的游戏状态"""
        eng = engine or self.game_engine
        if not eng:
            return
        if event == "hand_result" and eng.last_hand_results:
            winners = [r for r in eng.last_hand_results if r.get("won", 0) > 0]
            public_cards = " ".join(str(card) for card in eng.community_cards) or "无"
            winner_summaries = []
            for result in winners:
                text = (f"{result['username']} 收下 {result['won']} 筹码"
                        f"（净赢 {result.get('profit', result['won'])}）")
                if result.get("best_hand"):
                    hole_cards = " ".join(card["display"] for card in result.get("hole_cards", []))
                    best_five = " ".join(card["display"] for card in result["best_hand"].get("best_five", []))
                    text += (f"，底牌：{hole_cards}，牌型：{result['best_hand']['name']}"
                             f"，最佳五张：{best_five}")
                if result.get("reason"):
                    text += f"，{result['reason']}"
                if result.get("returned"):
                    text += f"，退回未跟注 {result['returned']}"
                winner_summaries.append(text)
            summary = f"公共牌：{public_cards}；" + "；".join(winner_summaries)
            other_refunds = [f"{r['username']} 退回未跟注 {r['returned']}"
                             for r in eng.last_hand_results
                             if r.get("returned") and r not in winners]
            if other_refunds:
                summary += "；" + "；".join(other_refunds)
            eng.last_hand_summary = f"第 {eng.hand_number} 手：{summary}"
            await self.publish_message({"kind": "system", "event": "hand_result",
                                        "hand_number": eng.hand_number,
                                        "text": eng.last_hand_summary})
        elif event == "cards_revealed" and eng.revealed_user_ids:
            uid = next(iter(eng.revealed_user_ids))
            player = eng.get_player(uid)
            if player:
                cards = " ".join(f"{c.rank}{c.suit}" for c in player.hole_cards)
                eng.last_hand_summary += f"；{player.username} 主动亮牌：{cards}"
                await self.publish_message({"kind": "system", "event": "cards_revealed",
                                            "text": f"{player.username} 选择亮牌：{cards}"})
        for uid, ws in list(self.connections.items()):
            state = eng.get_state(for_user_id=uid)
            state["event"] = event
            try:
                await ws.send_json({"type": "game_state", "data": state})
            except Exception:
                pass

    async def handle_message(self, ws: WebSocket, user_id: str, data: dict):
        """处理客户端消息"""
        msg_type = data.get("type", "")
        engine = self.game_engine

        if msg_type != "get_state":
            engine.touch_single_player_activity(user_id)

        if msg_type == "sit_down":
            seat = data.get("seat", -1)
            user_data = await redis_client.get_user(user_id)
            if not user_data:
                await self.send_personal(user_id, {"type": "error", "message": "用户数据不存在"})
                return
            chips = user_data.get("chips", 0)

            player = engine.get_player(user_id)
            if player:
                await self.send_personal(user_id, {"type": "error", "message": "你已经坐下了"})
                return

            username = user_data.get("username", user_id)
            avatar_id = user_data.get("avatar_id", DEFAULT_AVATAR_ID)
            if not is_valid_avatar(avatar_id):
                avatar_id = DEFAULT_AVATAR_ID
            ok = engine.sit_down(user_id, username, chips, seat, avatar_id)
            if ok:
                seated = engine.get_player(user_id)
                previous = self.session["players"].get(user_id)
                if previous:
                    seated.initial_buyin = previous.get("initial_buyin", chips)
                    seated.rebuy_count = previous.get("rebuy_count", 0)
                    seated.rebuy_total = previous.get("rebuy_total", 0)
                await self._save_session_player(seated)
                await self.broadcast_game_state("player_sit")
            else:
                await self.send_personal(user_id, {"type": "error", "message": "该座位已被占用或无效"})

        elif msg_type == "stand_up":
            leaving = engine.get_player(user_id)
            if leaving:
                await self._save_session_player(leaving)
                player_data = await redis_client.get_user(user_id)
                if player_data:
                    player_data["chips"] = leaving.chips
                    await redis_client.save_user(user_id, player_data)
            ok = engine.stand_up(user_id)
            if ok:
                reset = await engine.reset_if_insufficient_players()
                if not reset:
                    await self.broadcast_game_state("player_leave")
            else:
                await self.send_personal(user_id, {"type": "error", "message": "准备后座位已锁定；请先取消准备，或在游戏中选择本手后离桌"})

        elif msg_type == "ready":
            player = engine.get_player(user_id)
            if not player:
                await self.send_personal(user_id, {"type": "error", "message": "请先坐下"})
                return
            player.is_ready = not player.is_ready
            await self.broadcast_game_state("player_ready")

            if player.is_ready:
                await engine.try_start_game()

        elif msg_type == "leave_after_hand":
            if engine.request_leave_after_hand(user_id):
                await self.broadcast_game_state("leave_after_hand")
            else:
                player = engine.get_player(user_id)
                message = ("你已因操作超时被标记为本手结束后离座"
                           if player and player.timed_out else "你还没有坐下")
                await self.send_personal(user_id, {"type": "error", "message": message})

        elif msg_type == "action":
            action = data.get("action", "")
            amount = data.get("amount", 0)
            result = await engine.player_action(user_id, action, amount)
            if not result["ok"]:
                await self.send_personal(user_id, {"type": "error", "message": result["error"]})

        elif msg_type == "chat":
            text = str(data.get("text", "")).strip()[:300]
            if text:
                player = engine.get_player(user_id)
                name = player.username if player else user_id
                await self.publish_message({"kind": "chat", "user_id": user_id,
                                            "username": name, "text": text})

        elif msg_type == "show_cards_choice":
            result = await engine.choose_show_cards(user_id, bool(data.get("show")))
            if not result["ok"]:
                await self.send_personal(user_id, {"type": "error", "message": result["error"]})

        elif msg_type == "rebuy":
            amount = engine.big_blind * 50
            result = await engine.rebuy(user_id, amount)
            if result["ok"]:
                player = engine.get_player(user_id)
                await self._save_session_player(player)
                await self.publish_message({"kind": "system", "event": "rebuy",
                    "text": f"{player.username} 借入 {amount} 筹码（第 {player.rebuy_count} 次）"})
            else:
                await self.send_personal(user_id, {"type": "error", "message": result["error"]})

        elif msg_type == "settlement":
            for player in engine.players.values():
                await self._save_session_player(player)
            rows = list(self.session["players"].values())
            await self.send_personal(user_id, {"type": "settlement", "data": rows})

        elif msg_type == "update_avatar":
            avatar_id = str(data.get("avatar_id", ""))
            if not is_valid_avatar(avatar_id):
                await self.send_personal(user_id, {"type": "error", "message": "头像无效"})
                return
            user_data = await redis_client.get_user(user_id)
            if not user_data:
                await self.send_personal(user_id, {"type": "error", "message": "用户不存在"})
                return
            user_data["avatar_id"] = avatar_id
            await redis_client.save_user(user_id, user_data)
            player = engine.get_player(user_id)
            if player:
                player.avatar_id = avatar_id
                await self.broadcast_game_state("avatar_updated")
            await self.send_personal(user_id, {"type": "profile_updated",
                                               "avatar_id": avatar_id})

        elif msg_type == "get_state":
            state = engine.get_state(for_user_id=user_id)
            state["event"] = "sync"
            await self.send_personal(user_id, {"type": "game_state", "data": state})


manager = ConnectionManager()


async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    # 第一条消息必须是认证
    try:
        auth_data = await asyncio.wait_for(ws.receive_json(), timeout=10)
    except Exception:
        await ws.close(code=4001, reason="认证超时")
        return

    token = auth_data.get("token", "")
    payload = decode_token(token)
    if not payload or payload.get("role") != "player":
        await ws.close(code=4002, reason="认证失败")
        return

    user_id = payload["sub"]
    username = payload.get("username", user_id)
    user_data = await redis_client.get_user(user_id)
    avatar_id = (user_data or {}).get("avatar_id", DEFAULT_AVATAR_ID)
    if not is_valid_avatar(avatar_id):
        avatar_id = DEFAULT_AVATAR_ID

    await manager.connect(ws, user_id)

    # 发送初始状态
    state = manager.game_engine.get_state(for_user_id=user_id)
    state["event"] = "connected"
    await ws.send_json({
        "type": "game_state",
        "data": state,
        "user_info": {"user_id": user_id, "username": username,
                      "avatar_id": avatar_id},
    })
    await ws.send_json({"type": "message_history",
                        "data": await redis_client.get_recent_table_messages(50)})

    try:
        while True:
            data = await ws.receive_json()
            await manager.handle_message(ws, user_id, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WS error for {user_id}: {e}")
    finally:
        # 保存筹码
        player = manager.game_engine.get_player(user_id)
        if player:
            user_data = await redis_client.get_user(user_id)
            if user_data:
                user_data["chips"] = player.chips
                await redis_client.save_user(user_id, user_data)
        await manager.disconnect(ws)
