from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[Literal["video_jobs:create", "video_jobs:read", "video_jobs:review", "*"]] = Field(
        default_factory=lambda: ["video_jobs:create", "video_jobs:read", "video_jobs:review"],
        max_length=10,
    )


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    last_used_at: Optional[datetime] = None
    created_at: datetime


class ApiKeyCreateResponse(ApiKeyResponse):
    api_key: str


class AdminApiKeyCreateRequest(ApiKeyCreateRequest):
    user_id: str = Field(min_length=1)


class AdminApiKeyResponse(ApiKeyResponse):
    user_id: str


class AdminApiKeyCreateResponse(AdminApiKeyResponse):
    api_key: str


class PublicRemixConfig(BaseModel):
    target_duration_seconds: Optional[float] = Field(default=None, ge=1, le=300)
    bgm_mood: Literal["none", "upbeat", "calm", "cinematic", "energetic"] = "cinematic"
    bgm_volume: float = Field(default=0.15, ge=0, le=1)
    include_source_audio: bool = False
    add_voiceover: bool = True
    voiceover_script: Optional[str] = Field(default=None, max_length=8000)


class PublicVideoJobSpec(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    script: str = Field(default="", max_length=8000)
    platform: Literal["douyin", "xiaohongshu", "bilibili", "generic"] = "generic"
    duration_seconds: int = Field(default=30, ge=1, le=300)
    duration_mode: Literal["auto", "fixed"] = "fixed"
    style: Literal["commercial", "lifestyle", "cinematic"] = "commercial"
    generation_model: Literal["seedance1.5-pro", "seedance2.0", "kling", "mock"] = "seedance1.5-pro"
    video_model_no_audio: bool = True
    voiceover_no_audio: bool = True
    voice_id: str = Field(default="default", min_length=1, max_length=80)
    transition: Literal["none", "fade", "dissolve", "slideright", "slideup"] = "none"
    transition_duration: float = Field(default=0.5, ge=0, le=5)
    bgm_mood: Literal["none", "upbeat", "calm", "cinematic", "energetic"] = "none"
    bgm_volume: float = Field(default=0.15, ge=0, le=1)
    client_reference_id: Optional[str] = Field(default=None, max_length=160)
    remix_config: Optional[PublicRemixConfig] = None

    @model_validator(mode="after")
    def require_prompt_or_script(self):
        if not self.prompt.strip() and not self.script.strip():
            raise ValueError("prompt or script is required")
        return self


class PublicShotEditRequest(BaseModel):
    shot_idx: int = Field(ge=0)
    script_segment: Optional[str] = Field(default=None, max_length=2000)
    video_prompt: Optional[str] = Field(default=None, max_length=4000)
    duration_seconds: Optional[float] = Field(default=None, ge=0.5, le=60)


class PublicVideoJobReviewRequest(BaseModel):
    approved: bool = True
    adjustments: Optional[str] = Field(default=None, max_length=4000)
    edited_shots: Optional[list[PublicShotEditRequest]] = None


class PublicVideoJobReviewResponse(BaseModel):
    status: str
    message: str


class PublicVideoJobResultResponse(BaseModel):
    download_url: Optional[str] = None


class PublicVideoJobReviewState(BaseModel):
    type: Optional[Literal["shot_plan", "replication_plan", "remix_plan"]] = None
    required: bool = False
    data: dict = Field(default_factory=dict)


class PublicVideoJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "requires_review", "completed", "failed", "cancelled"]
    internal_status: str
    current_agent: Optional[str] = None
    review: PublicVideoJobReviewState
    result: PublicVideoJobResultResponse
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
