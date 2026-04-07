from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.material import MaterialResponse, MaterialSelectionResponse
from app.schemas.pipeline import (
    AgentExecutionResponse,
    PipelineDeliveryResponse,
    PipelineRunResponse,
    PipelineUsageResponse,
)
from app.schemas.social_account import PublishDraftResponse, SocialAccountResponse
from app.schemas.video import VideoUploadResponse


class AutoChatMessagePayloadVideo(BaseModel):
    id: str
    name: str
    streamUrl: str


class AutoChatMessagePayloadImage(BaseModel):
    id: str
    url: str
    name: str


class AutoChatMessagePayloadFile(BaseModel):
    id: str
    name: str
    url: str
    mimeType: Optional[str] = None


class AutoChatMessagePayload(BaseModel):
    mutedLines: list[str] = []
    images: list[AutoChatMessagePayloadImage] = []
    files: list[AutoChatMessagePayloadFile] = []
    video: Optional[AutoChatMessagePayloadVideo] = None
    publishDraft: Optional[PublishDraftResponse] = None


class AutoChatMessageCreateRequest(BaseModel):
    role: str
    title: Optional[str] = None
    content: str
    payload: Optional[AutoChatMessagePayload] = None


class AutoChatMessageUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    payload: Optional[AutoChatMessagePayload] = None


class AutoChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    title: Optional[str] = None
    content: str
    payload: Optional[AutoChatMessagePayload] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AutoChatSessionState(BaseModel):
    draft_script: Optional[str] = None
    background_template_id: Optional[str] = None
    reference_video_id: Optional[str] = None
    video_platform: str = "generic"
    video_no_audio: bool = True
    duration_mode: str = "fixed"
    video_transition: str = "none"
    bgm_mood: str = "none"
    watermark_id: Optional[str] = None
    current_run_id: Optional[str] = None


class AutoChatSessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    status_preview: Optional[str] = None
    draft_script: Optional[str] = None
    background_template_id: Optional[str] = None
    reference_video_id: Optional[str] = None
    video_platform: Optional[str] = None
    video_no_audio: Optional[bool] = None
    duration_mode: Optional[str] = None
    video_transition: Optional[str] = None
    bgm_mood: Optional[str] = None
    watermark_id: Optional[str] = None
    current_run_id: Optional[str] = None
    last_activity_at: Optional[datetime] = None


class AutoChatSessionSummaryResponse(BaseModel):
    id: str
    project_id: str
    title: str
    status_preview: str
    latest_message_excerpt: Optional[str] = None
    latest_message_role: Optional[str] = None
    reference_video_name: Optional[str] = None
    current_run_id: Optional[str] = None
    current_run_status: Optional[str] = None
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime


class AutoChatSessionDetailResponse(BaseModel):
    session: AutoChatSessionSummaryResponse
    state: AutoChatSessionState
    messages: list[AutoChatMessageResponse] = []
    selected_materials: list[MaterialSelectionResponse] = []
    selected_material_items: list[MaterialResponse] = []
    reference_video: Optional[VideoUploadResponse] = None
    current_run: Optional[PipelineRunResponse] = None
    agent_executions: list[AgentExecutionResponse] = []
    delivery_info: Optional[PipelineDeliveryResponse] = None
    usage_summary: Optional[PipelineUsageResponse] = None
    connected_social_accounts: list[SocialAccountResponse] = []
    recommended_publish_account: Optional[SocialAccountResponse] = None
    latest_publish_draft: Optional[PublishDraftResponse] = None
