from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class GeneratedVideoResponse(BaseModel):
    id: str
    project_id: str
    prompt_id: Optional[str]
    material_id: Optional[str]
    status: str
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[float]
    is_selected: bool
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    # Bound info
    prompt_text: Optional[str] = None
    material_filename: Optional[str] = None
    material_category: Optional[str] = None
    material_thumbnail_url: Optional[str] = None

    class Config:
        from_attributes = True
