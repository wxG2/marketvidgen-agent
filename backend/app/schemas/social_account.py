from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SocialAccountResponse(BaseModel):
    id: str
    user_id: str
    platform: str
    open_id: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    expires_at: Optional[datetime] = None
    scopes: list[str] = []
    status: str
    is_default: bool = False
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class SocialAccountConnectResponse(BaseModel):
    authorization_url: str


class PublishDraftResponse(BaseModel):
    platform: str
    pipeline_run_id: str
    delivery_record_id: Optional[str] = None
    social_account_id: Optional[str] = None
    account_name: Optional[str] = None
    title: str
    description: str
    hashtags: list[str] = []
    visibility: str = "public"
    cover_title: Optional[str] = None
    topic: Optional[str] = None
    risk_tip: Optional[str] = None
    video_source: Optional[str] = None
    status: str = "draft"


class PublishDraftCreateRequest(BaseModel):
    platform: str = "douyin"
    social_account_id: Optional[str] = None


class PublishDraftConfirmRequest(BaseModel):
    social_account_id: str
    title: str
    description: str
    hashtags: list[str] = []
    visibility: str = "public"
    cover_title: Optional[str] = None
