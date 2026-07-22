"""
Pydantic 数据模型
"""
from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    chips: int = 1000
    avatar_id: str = "avatar-01"


class AddChipsRequest(BaseModel):
    user_id: str
    amount: int


class SetChipsRequest(BaseModel):
    user_id: str
    chips: int = Field(ge=0)


class UpdateTableConfigRequest(BaseModel):
    small_blind: int | None = None
    big_blind: int | None = None
    turn_timeout: int | None = None
    max_players: int | None = None
    dealer_image: str | None = None


class PlayerLoginRequest(BaseModel):
    username: str
    password: str


class PlayerRegisterRequest(BaseModel):
    username: str
    password: str
    avatar_id: str


class TokenResponse(BaseModel):
    token: str
    user_id: str | None = None
    username: str | None = None
    avatar_id: str | None = None
    role: str
