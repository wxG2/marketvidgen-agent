from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TalkingHeadTask(Base):
    __tablename__ = "talking_head_tasks"
    __table_args__ = (
        CheckConstraint(
            "composite_status IN ('pending', 'processing', 'completed', 'failed')",
            name="composite_status",
        ),
        CheckConstraint(
            "lipsync_status IN ('pending', 'processing', 'completed', 'failed')",
            name="lipsync_status",
        ),
        CheckConstraint(
            "audio_start_ms IS NULL OR audio_start_ms >= 0",
            name="audio_start_ms_non_negative",
        ),
        CheckConstraint(
            "audio_end_ms IS NULL OR audio_end_ms >= 0",
            name="audio_end_ms_non_negative",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    shot_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Step A inputs
    model_image_id: Mapped[str] = mapped_column(String, ForeignKey("model_images.id"), index=True, nullable=False)
    bg_material_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("materials.id"), index=True, nullable=True)

    # Step B composite result
    composite_status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    composite_image_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    compositor_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Step C prompt & audio
    motion_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audio_segment_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    audio_start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    audio_end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Step D lipsync result
    lipsync_status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    lipsync_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    video_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
