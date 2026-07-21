"""
德州扑克在线平台 - 主入口
"""
import logging
import base64
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

import config
import redis_client
from auth import verify_password, create_player_token, hash_password
from models import PlayerLoginRequest, PlayerRegisterRequest, TokenResponse
from avatar_config import AVATAR_IDS, DEFAULT_AVATAR_ID, avatar_url, is_valid_avatar
from admin.routes import router as admin_router
from ws.handler import websocket_endpoint, manager
from game.engine import GameEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("poker")

game_engine = GameEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时加载配置
    table_cfg = await redis_client.get_table_config()
    game_engine.update_config(
        small_blind=table_cfg["small_blind"],
        big_blind=table_cfg["big_blind"],
        turn_timeout=table_cfg["turn_timeout"],
        max_players=table_cfg["max_players"],
        dealer_image=table_cfg.get("dealer_image", ""),
    )
    async def save_player_chips(user_id, chips):
        user_data = await redis_client.get_user(user_id)
        if user_data:
            user_data["chips"] = chips
            await redis_client.save_user(user_id, user_data)
        player = game_engine.get_player(user_id)
        if player:
            await manager._save_session_player(player)

    game_engine._broadcast = manager.broadcast_game_state
    game_engine._is_online = lambda uid: uid in manager.connections
    game_engine._save_chips = save_player_chips
    manager.set_engine(game_engine)
    await manager.load_session()
    logger.info(f"Texas Hold'em server starting on {config.SERVER_HOST}:{config.SERVER_PORT}")
    logger.info(f"Table config: SB={table_cfg['small_blind']} BB={table_cfg['big_blind']} "
                f"Timeout={table_cfg['turn_timeout']}s Max={table_cfg['max_players']}players")
    yield
    await redis_client.close_redis()
    logger.info("Server shutdown")


app = FastAPI(title="德州扑克在线平台", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(admin_router)

# 静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/admin")
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.post("/api/login", response_model=TokenResponse)
async def player_login(req: PlayerLoginRequest):
    users = await redis_client.get_all_users()
    for u in users:
        if u["username"] == req.username:
            if verify_password(req.password, u["password_hash"]):
                token = create_player_token(u["user_id"], u["username"])
                avatar_id = u.get("avatar_id", DEFAULT_AVATAR_ID)
                if not is_valid_avatar(avatar_id):
                    avatar_id = DEFAULT_AVATAR_ID
                return TokenResponse(
                    token=token,
                    user_id=u["user_id"],
                    username=u["username"],
                    avatar_id=avatar_id,
                    role="player",
                )
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail="用户名或密码错误")


@app.get("/api/avatars")
async def available_avatars():
    return {"avatars": [{"id": avatar_id, "url": avatar_url(avatar_id)}
                        for avatar_id in AVATAR_IDS]}


@app.post("/api/register", response_model=TokenResponse)
async def player_register(req: PlayerRegisterRequest):
    username = req.username.strip()
    if not 2 <= len(username) <= 20:
        raise HTTPException(status_code=400, detail="用户名长度需要在 2～20 个字符之间")
    if not 4 <= len(req.password) <= 72:
        raise HTTPException(status_code=400, detail="密码长度需要在 4～72 个字符之间")
    if not is_valid_avatar(req.avatar_id):
        raise HTTPException(status_code=400, detail="请选择有效头像")
    users = await redis_client.get_all_users()
    if any(user.get("username", "").casefold() == username.casefold() for user in users):
        raise HTTPException(status_code=400, detail="用户名已存在")

    user_id = str(uuid.uuid4())[:8]
    await redis_client.save_user(user_id, {
        "user_id": user_id,
        "username": username,
        "password_hash": hash_password(req.password),
        "chips": 1000,
        "avatar_id": req.avatar_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return TokenResponse(
        token=create_player_token(user_id, username),
        user_id=user_id,
        username=username,
        avatar_id=req.avatar_id,
        role="player",
    )


@app.websocket("/ws")
async def ws_route(ws: WebSocket):
    await websocket_endpoint(ws)


@app.get("/api/health")
async def health():
    return {"status": "ok", "players_online": len(manager.connections)}


@app.get("/api/dealer-image")
async def dealer_image():
    cfg = await redis_client.get_table_config()
    source = cfg.get("dealer_image", "")
    if not source or "," not in source:
        raise HTTPException(status_code=404, detail="未设置荷官图片")
    header, payload = source.split(",", 1)
    media_type = header[5:].split(";", 1)[0]
    try:
        content = base64.b64decode(payload, validate=True)
    except ValueError:
        raise HTTPException(status_code=422, detail="荷官图片数据无效")
    return Response(content=content, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.SERVER_HOST, port=config.SERVER_PORT, reload=True)
