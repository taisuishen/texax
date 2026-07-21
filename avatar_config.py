"""玩家可选头像白名单，仅开放 static/avatars 中指定的 PNG。"""

AVATAR_IDS = tuple(f"avatars{index:02d}" for index in range(1, 17))
DEFAULT_AVATAR_ID = "avatar-01"


def is_valid_avatar(avatar_id: str) -> bool:
    return avatar_id in AVATAR_IDS


def avatar_url(avatar_id: str) -> str:
    if avatar_id == DEFAULT_AVATAR_ID or not is_valid_avatar(avatar_id):
        return "/static/avatars/avatar-01.svg"
    return f"/static/avatars/{avatar_id}.png"
