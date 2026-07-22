"""
后台管理 API
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Header
from models import (
    AdminLoginRequest, CreateUserRequest, AddChipsRequest, SetChipsRequest,
    ResetAllFinancesRequest,
    UpdateTableConfigRequest, TokenResponse,
)
from auth import (
    create_admin_token, decode_token, hash_password, verify_password,
)
import config
import redis_client
from avatar_config import DEFAULT_AVATAR_ID, is_valid_avatar

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证信息")
    token = authorization.replace("Bearer ", "")
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="无管理员权限")
    return payload


@router.post("/login", response_model=TokenResponse)
async def admin_login(req: AdminLoginRequest):
    if req.username != config.ADMIN_USERNAME or req.password != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_admin_token()
    return TokenResponse(token=token, role="admin")


@router.get("/users")
async def list_users(_=Depends(require_admin)):
    users = await redis_client.get_all_users()
    for u in users:
        u.pop("password_hash", None)
    return {"users": users}


@router.post("/users")
async def create_user(req: CreateUserRequest, _=Depends(require_admin)):
    existing = await redis_client.get_all_users()
    for u in existing:
        if u["username"] == req.username:
            raise HTTPException(status_code=400, detail="用户名已存在")

    avatar_id = req.avatar_id if is_valid_avatar(req.avatar_id) else DEFAULT_AVATAR_ID
    user_id = str(uuid.uuid4())[:8]
    user_data = {
        "user_id": user_id,
        "username": req.username,
        "password_hash": hash_password(req.password),
        "chips": req.chips,
        "avatar_id": avatar_id,
        "created_at": str(__import__("datetime").datetime.now()),
    }
    await redis_client.save_user(user_id, user_data)
    return {"ok": True, "user_id": user_id, "username": req.username, "chips": req.chips}


@router.post("/users/add_chips")
async def add_chips(req: AddChipsRequest, _=Depends(require_admin)):
    user = await redis_client.get_user(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user["chips"] = user.get("chips", 0) + req.amount
    await redis_client.save_user(req.user_id, user)
    return {"ok": True, "user_id": req.user_id, "chips": user["chips"]}


@router.post("/users/set_chips")
async def set_chips(req: SetChipsRequest, _=Depends(require_admin)):
    user = await redis_client.get_user(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 牌局进行中直接改筹码会破坏下注与边池守恒，只允许在等待阶段修改。
    from ws.handler import manager
    engine = manager.game_engine
    seated = engine.get_player(req.user_id) if engine else None
    if seated and engine.phase.value != "waiting":
        raise HTTPException(status_code=409, detail="该玩家正在牌局中，请本手结束后再修改")

    user["chips"] = req.chips
    await redis_client.save_user(req.user_id, user)

    if seated:
        seated.chips = req.chips
        seated.pending_rebuy = req.chips <= 0
        if seated.pending_rebuy:
            seated.is_ready = False
        await manager._save_session_player(seated)
        await manager.broadcast_game_state("admin_chips_updated")

    return {"ok": True, "user_id": req.user_id, "chips": req.chips}


@router.post("/users/reset_all_finances")
async def reset_all_finances(req: ResetAllFinancesRequest, _=Depends(require_admin)):
    from ws.handler import manager
    engine = manager.game_engine
    if engine and engine.phase.value != "waiting":
        raise HTTPException(status_code=409, detail="牌局进行中，请等待本手结束并回到等待阶段")

    users = await redis_client.get_all_users()
    for user in users:
        user["chips"] = req.chips
        await redis_client.save_user(user["user_id"], user)

    # 重新建立朋友局账本基线，清空所有历史借入次数和借入总额。
    manager.session = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "players": {},
    }
    if engine:
        for player in engine.players.values():
            player.chips = req.chips
            player.initial_buyin = req.chips
            player.rebuy_count = 0
            player.rebuy_total = 0
            player.pending_rebuy = req.chips <= 0
            player.is_ready = False
            await manager._save_session_player(player)
    await redis_client.save_session(manager.session)

    if engine:
        await manager.broadcast_game_state("admin_all_finances_reset")

    return {"ok": True, "users_reset": len(users), "chips": req.chips}


@router.get("/table_config")
async def get_table_config(_=Depends(require_admin)):
    cfg = await redis_client.get_table_config()
    return cfg


@router.post("/table_config")
async def update_table_config(req: UpdateTableConfigRequest, _=Depends(require_admin)):
    cfg = await redis_client.get_table_config()
    if req.small_blind is not None:
        cfg["small_blind"] = req.small_blind
    if req.big_blind is not None:
        cfg["big_blind"] = req.big_blind
    if req.turn_timeout is not None:
        cfg["turn_timeout"] = req.turn_timeout
    if req.max_players is not None:
        cfg["max_players"] = req.max_players
    if req.dealer_image is not None:
        if len(req.dealer_image) > 2_100_000:
            raise HTTPException(status_code=400, detail="荷官图片过大，请控制在 1.5MB 以内")
        if req.dealer_image and not req.dealer_image.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="荷官文件必须是图片")
        cfg["dealer_image"] = req.dealer_image
    await redis_client.save_table_config(cfg)
    # 后台保存后立即同步当前牌桌，无需重启服务。
    from ws.handler import manager
    if manager.game_engine:
        manager.game_engine.update_config(
            cfg["small_blind"], cfg["big_blind"], cfg["turn_timeout"],
            cfg["max_players"], cfg.get("dealer_image", ""),
        )
    return {"ok": True, **cfg}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, _=Depends(require_admin)):
    user = await redis_client.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    r = await redis_client.get_redis()
    await r.delete(f"user:{user_id}")
    return {"ok": True}
