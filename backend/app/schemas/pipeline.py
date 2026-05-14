from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.social_account import PublishDraftResponse, SocialAccountResponse


class RemixConfigRequest(BaseModel):
    target_duration_seconds: Optional[float] = Field(default=None, ge=1, le=300)
    mood: Optional[str] = Field(default=None, max_length=80)
    bgm_material_id: Optional[str] = None
    bgm_mood: Literal["none", "upbeat", "calm", "cinematic", "energetic"] = "cinematic"
    bgm_volume: float = Field(default=0.15, ge=0, le=1)
    include_source_audio: bool = False
    add_voiceover: bool = True
    voiceover_script: Optional[str] = Field(default=None, max_length=8000)


class PipelineCreateRequest(BaseModel):
    script: str = Field(default="", max_length=8000)  # free-form user input: script or requirement description
    image_ids: list[str] = Field(default_factory=list, max_length=100)  # Material IDs
    session_id: Optional[str] = None
    background_template_id: Optional[str] = None
    reference_video_id: Optional[str] = None  # VideoUpload ID for replication mode
    reference_video_ids: list[str] = Field(default_factory=list, max_length=20)  # VideoUpload IDs for remix mode
    remix_config: Optional[RemixConfigRequest] = None
    platform: Literal["douyin", "xiaohongshu", "bilibili", "generic"] = "generic"
    duration_seconds: int = Field(default=30, ge=1, le=300)
    duration_mode: Literal["auto", "fixed"] = "fixed"
    no_audio: bool = True  # Legacy audio flag used when explicit audio flags are omitted.
    video_model_no_audio: Optional[bool] = None  # True = Seedance/Kling generates silent clips.
    voiceover_no_audio: Optional[bool] = None  # True = skip VidGen TTS/subtitle generation.
    generation_model: Literal["seedance1.5-pro", "seedance2.0", "kling", "mock"] = "seedance1.5-pro"
    style: Literal["commercial", "lifestyle", "cinematic"] = "commercial"
    voice_id: str = Field(default="default", min_length=1, max_length=80)
    transition: Literal["none", "fade", "dissolve", "slideright", "slideup"] = "none"
    transition_duration: float = Field(default=0.5, ge=0, le=5)
    bgm_mood: Literal["none", "upbeat", "calm", "cinematic", "energetic"] = "none"
    bgm_volume: float = Field(default=0.15, ge=0, le=1)
    watermark_image_id: Optional[str] = None  # material ID of watermark/logo image
    review_prompts: Optional[bool] = None  # override human-in-the-loop prompt review for this run


class ConfirmPlanRequest(BaseModel):
    approved: bool
    adjustments: Optional[str] = Field(default=None, max_length=4000)  # user feedback when not approved


class RemixSegmentEdit(BaseModel):
    segment_idx: int = Field(ge=0)
    source_video_id: Optional[str] = None
    start_seconds: Optional[float] = Field(default=None, ge=0)
    end_seconds: Optional[float] = Field(default=None, ge=0)
    transition_type: Optional[Literal["cut", "fade", "dissolve", "slideright", "slideup"]] = None
    removed: bool = False


class ConfirmRemixRequest(BaseModel):
    approved: bool
    adjustments: Optional[str] = Field(default=None, max_length=4000)
    edited_segments: list[RemixSegmentEdit] = Field(default_factory=list, max_length=100)


class ShotEditRequest(BaseModel):
    shot_idx: int = Field(ge=0)
    script_segment: Optional[str] = Field(default=None, max_length=2000)
    video_prompt: Optional[str] = Field(default=None, max_length=4000)
    duration_seconds: Optional[float] = Field(default=None, ge=0.5, le=60)


class ConfirmPromptReviewRequest(BaseModel):
    edited_shots: Optional[list[ShotEditRequest]] = None


class RetryShotRequest(BaseModel):
    shot_indices: list[int] = Field(min_length=1, max_length=100)


class EstimateCostRequest(BaseModel):
    shot_plan: list[dict] = Field(default_factory=list, max_length=100)      # shot_prompts list
    model: Literal["seedance1.5-pro", "seedance2.0", "kling", "mock"] = "seedance1.5-pro"
    voiceover_no_audio: bool = False
    tts_char_count: int = Field(default=0, ge=0, le=20000)         # if provided, overrides shot_plan script length estimate
    bgm_mood: Literal["none", "upbeat", "calm", "cinematic", "energetic"] = "none"


class EstimateCostResponse(BaseModel):
    estimated_total_cny: float
    breakdown: dict  # {"video_gen": float, "tts": float, "llm": float, "bgm": float}
    shot_count: int
    total_generation_seconds: float
    warning: Optional[str] = None


class ScriptGenerateRequest(BaseModel):
    image_ids: list[str] = Field(min_length=1, max_length=100)


class ScriptGenerateResponse(BaseModel):
    script: str


class PrefightCheckRequest(BaseModel):
    script: str = Field(max_length=8000)
    image_count: int = Field(ge=0, le=100)
    duration_seconds: int = Field(default=30, ge=1, le=300)
    duration_mode: Literal["auto", "fixed"] = "fixed"


class PrefightCheckResponse(BaseModel):
    ok: bool
    warning: Optional[str] = None
    estimated_audio_seconds: float = 0
    max_video_seconds: int = 0
    recommended_image_count: int = 0
    estimated_tokens: int = 0  # rough total token estimate for the pipeline


class PipelineRunResponse(BaseModel):
    id: str
    project_id: str
    session_id: Optional[str] = None
    trace_id: str
    engine: str = "pipeline"
    status: str
    current_agent: Optional[str] = None
    overall_score: Optional[float] = None
    final_video_path: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AgentExecutionResponse(BaseModel):
    id: str
    agent_name: str
    status: str
    attempt_number: int = 1
    input_data: Optional[dict] = None
    output_data: Optional[dict] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    progress_text: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UsageByAgentResponse(BaseModel):
    agent_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0


class UsageByModelResponse(BaseModel):
    provider: str
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0


class PipelineUsageResponse(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    by_agent: list[UsageByAgentResponse] = Field(default_factory=list)
    by_model: list[UsageByModelResponse] = Field(default_factory=list)


class DeliveryActionRequest(BaseModel):
    social_account_id: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=20)
    visibility: Literal["public", "private", "friends"] = "public"
    cover_title: Optional[str] = Field(default=None, max_length=80)


class PlatformPreviewCardResponse(BaseModel):
    platform: str
    label: str
    aspect_ratio: str
    recommended_resolution: str
    cover_title: str
    headline: str
    caption: str
    layout_hint: str
    safe_zone_tip: str
    context_hint: str
    primary_action: str


class VideoDeliveryResponse(BaseModel):
    id: str
    user_id: str
    project_id: str
    pipeline_run_id: str
    action_type: str
    platform: str
    status: str
    social_account_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    draft_payload: Optional[dict] = None
    saved_video_path: Optional[str] = None
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    external_status: Optional[str] = None
    response_payload: Optional[dict] = None
    platform_error_code: Optional[str] = None
    error_message: Optional[str] = None
    submitted_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class PipelineDeliveryResponse(BaseModel):
    previews: list[PlatformPreviewCardResponse] = Field(default_factory=list)
    records: list[VideoDeliveryResponse] = Field(default_factory=list)
    connected_social_accounts: list[SocialAccountResponse] = Field(default_factory=list)
    recommended_publish_account: Optional[SocialAccountResponse] = None
    latest_publish_draft: Optional[PublishDraftResponse] = None


class PipelineArtifactResponse(BaseModel):
    id: str
    user_id: str
    project_id: str
    project_name: Optional[str] = None
    pipeline_run_id: str
    asset_key: str
    asset_type: str
    source_agent: str
    title: Optional[str] = None
    description: Optional[str] = None
    mime_type: Optional[str] = None
    file_path: Optional[str] = None
    file_url: Optional[str] = None
    file_size: Optional[int] = None
    text_content: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    duration_ms: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class ProjectPipelineUsageItem(BaseModel):
    id: str
    status: str
    current_agent: Optional[str] = None
    total_tokens: int = 0
    request_count: int = 0
    created_at: datetime


class ProjectUsageSummaryResponse(BaseModel):
    project_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    latest_pipeline_status: Optional[str] = None
    latest_current_agent: Optional[str] = None
    pipelines: list[ProjectPipelineUsageItem] = Field(default_factory=list)


class ArtifactFileResponse(BaseModel):
    name: str
    path: str
    url: str
    content: Optional[str] = None
    shot_idx: Optional[int] = None
    duration_ms: Optional[int] = None
    kind: Optional[str] = None


class PromptHistoryItemResponse(BaseModel):
    shot_idx: int
    script_segment: Optional[str] = None
    video_prompt: str
    duration_seconds: Optional[float] = None


class ProjectHistoryRunResponse(BaseModel):
    run_id: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    current_agent: Optional[str] = None
    total_tokens: int = 0
    request_count: int = 0
    input_script: Optional[str] = None
    voice_params: Optional[dict] = None
    prompts: list[PromptHistoryItemResponse] = []
    audio_files: list[ArtifactFileResponse] = []
    subtitle_files: list[ArtifactFileResponse] = []
    generated_videos: list[ArtifactFileResponse] = []
    final_videos: list[ArtifactFileResponse] = []


class ProjectHistoryResponse(BaseModel):
    project_id: str
    runs: list[ProjectHistoryRunResponse] = []
