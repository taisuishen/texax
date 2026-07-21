"""
后台管理 API
"""
import uuid
from fastapi import APIRouter, HTTPException, Depends, Header
from models import (
    AdminLoginRequest, CreateUserRequest, AddChipsRequest,
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
