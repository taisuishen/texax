"""
Pydantic 数据模型
"""
from pydantic import BaseModel


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    chips: int = 1000


class AddChipsRequest(BaseModel):
    user_id: str
    amount: int


class UpdateTableConfigRequest(BaseModel):
    small_blind: int | None = None
    big_blind: int | None = None
    turn_timeout: int | None = None
    max_players: int | None = None


class PlayerLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user_id: str | None = None
    username: str | None = None
    role: str
