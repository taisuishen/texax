"""
德州扑克在线平台 - 主入口
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

import config
import redis_client
from auth import verify_password, create_player_token
from models import PlayerLoginRequest, TokenResponse
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
    )
    game_engine._broadcast = manager.broadcast_game_state
    game_engine._is_online = lambda uid: uid in manager.connections
    manager.set_engine(game_engine)
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
                return TokenResponse(
                    token=token,
                    user_id=u["user_id"],
                    username=u["username"],
                    role="player",
                )
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail="用户名或密码错误")


@app.websocket("/ws")
async def ws_route(ws: WebSocket):
    await websocket_endpoint(ws)


@app.get("/api/health")
async def health():
    return {"status": "ok", "players_online": len(manager.connections)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.SERVER_HOST, port=config.SERVER_PORT, reload=True)
