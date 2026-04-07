from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class VideoUploadResponse(BaseModel):
    id: str
    project_id: str
    session_id: Optional[str]
    filename: str
    file_size: int
    duration_seconds: Optional[float]
    mime_type: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class VideoAnalysisResponse(BaseModel):
    id: str
    project_id: str
    status: str
    summary: Optional[str]
    scene_tags: Optional[List[str]] = None
    recommended_categories: Optional[List[str]] = None
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True
