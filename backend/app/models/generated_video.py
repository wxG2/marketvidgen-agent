from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GeneratedVideo(Base):
    __tablename__ = "generated_videos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    prompt_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("prompts.id"), nullable=True)
    material_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("materials.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    kling_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    video_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generation_type: Mapped[str] = mapped_column(String, default="image_to_video")  # image_to_video | talking_head
    talking_head_task_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("talking_head_tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
