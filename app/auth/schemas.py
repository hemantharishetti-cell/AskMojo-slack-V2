from pydantic import BaseModel, EmailStr
from datetime import datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: int | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "user"


class UserAuthResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

