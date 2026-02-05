from pydantic import BaseModel, HttpUrl
from typing import Optional
from datetime import datetime


class SlackConfigCreate(BaseModel):
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    bot_token: Optional[str] = None
    app_token: Optional[str] = None  # App-Level Token for Socket Mode
    socket_mode_enabled: bool = False  # Enable Socket Mode
    webhook_url: Optional[str] = None
    signing_secret: Optional[str] = None
    channel_id: Optional[str] = None
    is_active: bool = True


class SlackConfigUpdate(BaseModel):
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    bot_token: Optional[str] = None
    app_token: Optional[str] = None  # App-Level Token for Socket Mode
    socket_mode_enabled: Optional[bool] = None  # Enable Socket Mode
    webhook_url: Optional[str] = None
    signing_secret: Optional[str] = None
    channel_id: Optional[str] = None
    is_active: Optional[bool] = None


class SlackConfigResponse(BaseModel):
    id: int
    workspace_name: Optional[str]
    workspace_id: Optional[str]
    bot_token: Optional[str] = None  # Don't return token in response
    app_token: Optional[str] = None  # Don't return token in response
    socket_mode_enabled: bool = False
    webhook_url: Optional[str] = None
    signing_secret: Optional[str] = None  # Don't return secret in response
    channel_id: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SlackTestRequest(BaseModel):
    message: str = "Test message from ASKMOJO admin panel"


class SlackTestResponse(BaseModel):
    success: bool
    message: str
    error: Optional[str] = None


class SlackUserFetchRequest(BaseModel):
    email: Optional[str] = None
    slack_user_id: Optional[str] = None
    
    def model_post_init(self, __context):
        """Validate that either email or slack_user_id is provided."""
        if not self.email and not self.slack_user_id:
            raise ValueError("Either 'email' or 'slack_user_id' must be provided")


class SlackUserInfo(BaseModel):
    """Slack user info from Slack API"""
    id: str
    email: Optional[str] = None
    name: str
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    image_24: Optional[str] = None
    image_32: Optional[str] = None
    image_48: Optional[str] = None
    image_72: Optional[str] = None
    image_192: Optional[str] = None
    is_admin: bool = False
    is_owner: bool = False
    is_bot: bool = False
    is_active: bool = True
    tz: Optional[str] = None
    tz_label: Optional[str] = None
    tz_offset: Optional[int] = None


class SlackUserCreate(BaseModel):
    slack_user_id: str
    email: Optional[str] = None  # Email might be None for some Slack users
    name: str
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    image_24: Optional[str] = None
    image_32: Optional[str] = None
    image_48: Optional[str] = None
    image_72: Optional[str] = None
    image_192: Optional[str] = None
    is_admin: bool = False
    is_owner: bool = False
    is_bot: bool = False
    is_active: bool = True
    timezone: Optional[str] = None
    tz_label: Optional[str] = None
    tz_offset: Optional[int] = None
    is_registered: bool = True


class SlackUserUpdate(BaseModel):
    is_registered: Optional[bool] = None
    is_active: Optional[bool] = None


class SlackUserResponse(BaseModel):
    id: int
    slack_user_id: str
    email: str
    name: str
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    image_24: Optional[str] = None
    image_32: Optional[str] = None
    image_48: Optional[str] = None
    image_72: Optional[str] = None
    image_192: Optional[str] = None
    is_admin: bool
    is_owner: bool
    is_bot: bool
    is_active: bool
    timezone: Optional[str] = None
    tz_label: Optional[str] = None
    tz_offset: Optional[int] = None
    is_registered: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

