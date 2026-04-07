from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from app.schemas.social_account import PublishDraftResponse, SocialAccountResponse


class PipelineCreateRequest(BaseModel):
    script: str = ""  # free-form user input: script or requirement description
    image_ids: list[str] = []  # Material IDs
    session_id: Optional[str] = None
    background_template_id: Optional[str] = None
    reference_video_id: Optional[str] = None  # VideoUpload ID for replication mode
    platform: str = "generic"  # douyin | xiaohongshu | bilibili | generic
    duration_seconds: int = 30
    duration_mode: str = "fixed"  # "auto" = trim to subtitle timing; "fixed" = honor requested duration
    no_audio: bool = True  # True = Seedance generates silent video; False = Seedance generates video with audio
    style: str = "commercial"  # commercial | lifestyle | cinematic
    voice_id: str = "default"
    transition: str = "none"  # "none" | "fade" | "dissolve" | "slideright" | "slideup"
    transition_duration: float = 0.5  # seconds
    bgm_mood: str = "none"  # "none" | "upbeat" | "calm" | "cinematic" | "energetic"
    bgm_volume: float = 0.15  # 0.0 ~ 1.0, relative to narration
    watermark_image_id: Optional[str] = None  # material ID of watermark/logo image


class ConfirmPlanRequest(BaseModel):
    approved: bool
    adjustments: Optional[str] = None  # user feedback when not approved


class ScriptGenerateRequest(BaseModel):
    image_ids: list[str]


class ScriptGenerateResponse(BaseModel):
    script: str


class PrefightCheckRequest(BaseModel):
    script: str
    image_count: int
    duration_seconds: int = 30
    duration_mode: str = "fixed"


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
    swarm_state_json: Optional[str] = None
    swarm_state: Optional[dict] = None
    latest_lead_message: Optional[str] = None
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


class SwarmMessageRequest(BaseModel):
    message: str


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
    by_agent: list[UsageByAgentResponse] = []
    by_model: list[UsageByModelResponse] = []


class DeliveryActionRequest(BaseModel):
    social_account_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    hashtags: list[str] = []
    visibility: str = "public"
    cover_title: Optional[str] = None


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
    previews: list[PlatformPreviewCardResponse] = []
    records: list[VideoDeliveryResponse] = []
    connected_social_accounts: list[SocialAccountResponse] = []
    recommended_publish_account: Optional[SocialAccountResponse] = None
    latest_publish_draft: Optional[PublishDraftResponse] = None


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
    pipelines: list[ProjectPipelineUsageItem] = []


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
