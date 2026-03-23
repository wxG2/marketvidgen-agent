from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# --- Model Image schemas ---

class ModelImageResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    file_url: str
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Talking Head Task schemas ---

class TalkingHeadCreate(BaseModel):
    model_image_id: str
    bg_material_id: Optional[str] = None
    shot_index: Optional[int] = None
    motion_prompt: Optional[str] = None
    audio_segment_url: Optional[str] = None
    audio_start_ms: Optional[int] = None
    audio_end_ms: Optional[int] = None


class TalkingHeadPromptUpdate(BaseModel):
    motion_prompt: Optional[str] = None
    audio_segment_url: Optional[str] = None
    audio_start_ms: Optional[int] = None
    audio_end_ms: Optional[int] = None


class TalkingHeadResponse(BaseModel):
    id: str
    project_id: str
    shot_index: Optional[int] = None

    model_image_id: str
    model_image_url: Optional[str] = None
    bg_material_id: Optional[str] = None
    bg_thumbnail_url: Optional[str] = None

    composite_status: str
    composite_preview_url: Optional[str] = None

    motion_prompt: Optional[str] = None
    audio_segment_url: Optional[str] = None
    audio_start_ms: Optional[int] = None
    audio_end_ms: Optional[int] = None

    lipsync_status: str
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None

    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
