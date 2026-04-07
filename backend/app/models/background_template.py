from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Text, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BackgroundTemplate(Base):
    __tablename__ = "background_templates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    brand_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    character_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    identity: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    scene_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tone_style: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visual_style: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    do_not_include: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    learned_preferences: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_learned_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    learning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_by: Mapped[str] = mapped_column(String, default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class BackgroundTemplateLearningLog(Base):
    __tablename__ = "background_template_learning_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    template_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("background_templates.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    before_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    applied_patch: Mapped[str] = mapped_column(Text, nullable=False)
    after_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
