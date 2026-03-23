from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ChatMessageRequest(BaseModel):
    content: str


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class PromptResponse(BaseModel):
    id: str
    project_id: str
    material_selection_id: Optional[str]
    prompt_text: str
    created_at: datetime

    class Config:
        from_attributes = True


class PromptUpdateRequest(BaseModel):
    prompt_text: str


class PromptTemplate(BaseModel):
    name: str
    description: str
    template: str


class PromptBindingResponse(BaseModel):
    """A prompt bound to its material — the core unit for video generation."""
    prompt_id: str
    prompt_text: str
    material_id: Optional[str] = None
    material_filename: Optional[str] = None
    material_category: Optional[str] = None
    material_thumbnail_url: Optional[str] = None
