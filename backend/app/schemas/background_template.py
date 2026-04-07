from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class BackgroundTemplateBase(BaseModel):
    name: str
    brand_info: Optional[str] = None
    user_requirements: Optional[str] = None
    character_name: Optional[str] = None
    identity: Optional[str] = None
    scene_context: Optional[str] = None
    tone_style: Optional[str] = None
    visual_style: Optional[str] = None
    do_not_include: Optional[str] = None
    notes: Optional[str] = None


class BackgroundTemplateCreateRequest(BackgroundTemplateBase):
    pass


class BackgroundTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    brand_info: Optional[str] = None
    user_requirements: Optional[str] = None
    character_name: Optional[str] = None
    identity: Optional[str] = None
    scene_context: Optional[str] = None
    tone_style: Optional[str] = None
    visual_style: Optional[str] = None
    do_not_include: Optional[str] = None
    notes: Optional[str] = None


class BackgroundTemplateKeywordGenerateRequest(BaseModel):
    keywords: str
    template_id: Optional[str] = None


class BackgroundTemplateKeywordGenerateResponse(BackgroundTemplateBase):
    pass


class BackgroundTemplateResponse(BackgroundTemplateBase):
    id: str
    user_id: str
    learned_preferences: Optional[str] = None
    last_learned_summary: Optional[str] = None
    learning_count: int
    updated_by: str
    compiled_background_context: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BackgroundTemplateLearningLogResponse(BaseModel):
    id: str
    template_id: str
    pipeline_run_id: str
    before_snapshot: str
    applied_patch: str
    after_snapshot: str
    summary: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
