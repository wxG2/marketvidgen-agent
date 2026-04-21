from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TimelineAsset(Base):
    """A user-uploaded local file (video/audio/subtitle) for timeline use."""
    __tablename__ = "timeline_assets"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('video', 'audio', 'subtitle')",
            name="asset_type",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="duration_ms_non_negative",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False)  # video | audio | subtitle
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class TimelineClip(Base):
    __tablename__ = "timeline_clips"
    __table_args__ = (
        CheckConstraint(
            "track_type IN ('video', 'audio', 'subtitle')",
            name="track_type",
        ),
        CheckConstraint("track_index >= 0", name="track_index_non_negative"),
        CheckConstraint("position_ms >= 0", name="position_ms_non_negative"),
        CheckConstraint("duration_ms >= 0", name="duration_ms_non_negative"),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    # Source: either a generated video or a local asset
    generated_video_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("generated_videos.id"), index=True, nullable=True)
    asset_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("timeline_assets.id"), index=True, nullable=True)
    # Track info
    track_type: Mapped[str] = mapped_column(String, default="video")  # video | audio | subtitle
    track_index: Mapped[int] = mapped_column(Integer, default=0)
    position_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
