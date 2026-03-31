from datetime import datetime
from pydantic import BaseModel, EmailStr


class UserBase(BaseModel):
    name: str
    email: EmailStr
    role: str


class UserCreate(UserBase):
    password: str  # Password required for user creation


class UserUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    role: str | None = None
    password: str | None = None  # Optional password update


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
