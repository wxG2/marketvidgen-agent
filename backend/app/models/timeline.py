from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TimelineAsset(Base):
    """A user-uploaded local file (video/audio/subtitle) for timeline use."""
    __tablename__ = "timeline_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False)  # video | audio | subtitle
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TimelineClip(Base):
    __tablename__ = "timeline_clips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # Source: either a generated video or a local asset
    generated_video_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("generated_videos.id"), nullable=True)
    asset_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("timeline_assets.id"), nullable=True)
    # Track info
    track_type: Mapped[str] = mapped_column(String, default="video")  # video | audio | subtitle
    track_index: Mapped[int] = mapped_column(Integer, default=0)
    position_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
