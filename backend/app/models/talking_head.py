from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TalkingHeadTask(Base):
    __tablename__ = "talking_head_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    shot_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Step A inputs
    model_image_id: Mapped[str] = mapped_column(String, ForeignKey("model_images.id"), nullable=False)
    bg_material_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("materials.id"), nullable=True)

    # Step B composite result
    composite_status: Mapped[str] = mapped_column(String, default="pending")
    composite_image_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    compositor_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Step C prompt & audio
    motion_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audio_segment_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    audio_start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    audio_end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Step D lipsync result
    lipsync_status: Mapped[str] = mapped_column(String, default="pending")
    lipsync_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    video_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
