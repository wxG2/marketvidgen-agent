from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class TimelineAssetResponse(BaseModel):
    id: str
    project_id: str
    asset_type: str
    filename: str
    file_url: str
    file_size: int
    duration_ms: Optional[int] = None

    class Config:
        from_attributes = True


class TimelineClipData(BaseModel):
    generated_video_id: Optional[str] = None
    asset_id: Optional[str] = None
    track_type: str = "video"
    track_index: int = 0
    position_ms: int
    duration_ms: int
    sort_order: int
    label: Optional[str] = None


class TimelineClipResponse(BaseModel):
    id: str
    generated_video_id: Optional[str] = None
    asset_id: Optional[str] = None
    track_type: str
    track_index: int
    position_ms: int
    duration_ms: int
    sort_order: int
    label: Optional[str] = None
    # Resolved URLs
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    filename: Optional[str] = None

    class Config:
        from_attributes = True


class TimelineSaveRequest(BaseModel):
    clips: List[TimelineClipData]


class TimelineResponse(BaseModel):
    project_id: str
    clips: List[TimelineClipResponse]
    assets: List[TimelineAssetResponse] = []
