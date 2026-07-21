import redis.asyncio as aioredis
import json
import config
from datetime import datetime, timezone

pool = None


async def get_redis() -> aioredis.Redis:
    global pool
    if pool is None:
        pool = aioredis.ConnectionPool(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            decode_responses=True,
        )
    return aioredis.Redis(connection_pool=pool)


async def save_user(user_id: str, data: dict):
    r = await get_redis()
    await r.set(f"user:{user_id}", json.dumps(data, ensure_ascii=False))


async def get_user(user_id: str) -> dict | None:
    r = await get_redis()
    raw = await r.get(f"user:{user_id}")
    if raw:
        return json.loads(raw)
    return None


async def get_all_users() -> list[dict]:
    r = await get_redis()
    keys = []
    async for key in r.scan_iter("user:*"):
        keys.append(key)
    users = []
    for key in keys:
        raw = await r.get(key)
        if raw:
            users.append(json.loads(raw))
    return users


async def save_table_config(data: dict):
    r = await get_redis()
    await r.set("table:config", json.dumps(data))


async def get_table_config() -> dict:
    r = await get_redis()
    raw = await r.get("table:config")
    if raw:
        return json.loads(raw)
    return {
        "small_blind": config.DEFAULT_SMALL_BLIND,
        "big_blind": config.DEFAULT_BIG_BLIND,
        "turn_timeout": config.DEFAULT_TURN_TIMEOUT,
        "max_players": config.MAX_PLAYERS,
        "dealer_image": "",
    }


async def append_table_message(message: dict, limit: int = 200):
    """保存牌桌聊天和系统消息，仅保留最近若干条。"""
    r = await get_redis()
    message.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    await r.rpush("table:messages", json.dumps(message, ensure_ascii=False))
    await r.ltrim("table:messages", -limit, -1)


async def get_recent_table_messages(limit: int = 50) -> list[dict]:
    r = await get_redis()
    rows = await r.lrange("table:messages", -limit, -1)
    return [json.loads(row) for row in rows]


async def save_session(session: dict):
    r = await get_redis()
    await r.set("table:session", json.dumps(session, ensure_ascii=False))


async def get_session() -> dict | None:
    r = await get_redis()
    raw = await r.get("table:session")
    return json.loads(raw) if raw else None


async def close_redis():
    global pool
    if pool:
        await pool.disconnect()
        pool = None
