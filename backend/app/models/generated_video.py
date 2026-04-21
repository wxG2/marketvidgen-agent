from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GeneratedVideo(Base):
    __tablename__ = "generated_videos"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "generation_type IN ('image_to_video', 'talking_head')",
            name="generation_type",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    prompt_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("prompts.id"), index=True, nullable=True)
    material_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("materials.id"), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    kling_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    video_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generation_type: Mapped[str] = mapped_column(String, default="image_to_video")  # image_to_video | talking_head
    talking_head_task_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("talking_head_tasks.id"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
