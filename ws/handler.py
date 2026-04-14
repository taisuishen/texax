"""
WebSocket 连接管理和消息处理
"""
import json
import logging
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from auth import decode_token, verify_password, create_player_token
import redis_client
from game.engine import GameEngine, GamePhase, PlayerStatus

logger = logging.getLogger("poker.ws")


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.connections: dict[str, WebSocket] = {}  # user_id -> WebSocket
        self.ws_to_user: dict[int, str] = {}         # ws.id -> user_id
        self.game_engine: GameEngine | None = None

    def set_engine(self, engine: GameEngine):
        self.game_engine = engine

    async def connect(self, ws: WebSocket, user_id: str):
        self.connections[user_id] = ws
        self.ws_to_user[id(ws)] = user_id
        logger.info(f"Player {user_id} connected")

    async def disconnect(self, ws: WebSocket):
        user_id = self.ws_to_user.pop(id(ws), None)
        if user_id:
            self.connections.pop(user_id, None)
            if self.game_engine:
                player = self.game_engine.get_player(user_id)
                if player:
                    # 保存筹码
                    user_data = await redis_client.get_user(user_id)
                    if user_data:
                        user_data["chips"] = player.chips
                        await redis_client.save_user(user_id, user_data)

                    if self.game_engine.phase != GamePhase.WAITING:
                        # 游戏中断线: 标记弃牌, 如果轮到他则自动弃牌
                        if player.status == PlayerStatus.ACTIVE:
                            if player.seat == self.game_engine.current_player_seat:
                                await self.game_engine.player_action(user_id, "fold")
                            else:
                                player.status = PlayerStatus.FOLDED
                        # 游戏结束后会被清理
                    else:
                        # 等待阶段直接离座
                        self.game_engine.stand_up(user_id)

                    await self.broadcast_game_state("player_leave")
            logger.info(f"Player {user_id} disconnected")

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

        if msg_type == "sit_down":
            seat = data.get("seat", -1)
            user_data = await redis_client.get_user(user_id)
            if not user_data:
                await self.send_personal(user_id, {"type": "error", "message": "用户数据不存在"})
                return
            chips = user_data.get("chips", 0)
            if chips <= 0:
                await self.send_personal(user_id, {"type": "error", "message": "余额不足，请联系管理员充值"})
                return

            player = engine.get_player(user_id)
            if player:
                await self.send_personal(user_id, {"type": "error", "message": "你已经坐下了"})
                return

            username = user_data.get("username", user_id)
            ok = engine.sit_down(user_id, username, chips, seat)
            if ok:
                await self.broadcast_game_state("player_sit")
            else:
                await self.send_personal(user_id, {"type": "error", "message": "该座位已被占用或无效"})

        elif msg_type == "stand_up":
            ok = engine.stand_up(user_id)
            if ok:
                # 保存筹码回Redis
                player_data = await redis_client.get_user(user_id)
                if player_data:
                    await redis_client.save_user(user_id, player_data)
                await self.broadcast_game_state("player_leave")
            else:
                await self.send_personal(user_id, {"type": "error", "message": "无法离开 (游戏进行中)"})

        elif msg_type == "ready":
            player = engine.get_player(user_id)
            if not player:
                await self.send_personal(user_id, {"type": "error", "message": "请先坐下"})
                return
            player.is_ready = not player.is_ready
            await self.broadcast_game_state("player_ready")

            # 尝试开始游戏
            if player.is_ready:
                await engine.try_start_game()

        elif msg_type == "action":
            action = data.get("action", "")
            amount = data.get("amount", 0)
            result = await engine.player_action(user_id, action, amount)
            if not result["ok"]:
                await self.send_personal(user_id, {"type": "error", "message": result["error"]})

        elif msg_type == "chat":
            text = data.get("text", "").strip()
            if text:
                player = engine.get_player(user_id)
                name = player.username if player else user_id
                await self.broadcast({
                    "type": "chat",
                    "data": {"user_id": user_id, "username": name, "text": text}
                })

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

    await manager.connect(ws, user_id)

    # 发送初始状态
    state = manager.game_engine.get_state(for_user_id=user_id)
    state["event"] = "connected"
    await ws.send_json({
        "type": "game_state",
        "data": state,
        "user_info": {"user_id": user_id, "username": username},
    })

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
