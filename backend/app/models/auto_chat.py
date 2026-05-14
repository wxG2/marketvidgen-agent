from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AutoChatSession(Base):
    __tablename__ = "auto_chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "video_platform IN ('generic', 'douyin', 'xiaohongshu', 'bilibili')",
            name="video_platform",
        ),
        CheckConstraint(
            "duration_mode IN ('fixed', 'auto')",
            name="duration_mode",
        ),
        CheckConstraint(
            "video_transition IN ('none', 'fade', 'dissolve', 'slideright', 'slideup')",
            name="video_transition",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, default="新会话", nullable=False)
    status_preview: Mapped[str] = mapped_column(String, default="等待发送", nullable=False)
    draft_script: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    background_template_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("background_templates.id"), index=True, nullable=True)
    reference_video_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("video_uploads.id"), index=True, nullable=True)
    video_platform: Mapped[str] = mapped_column(String, default="generic", nullable=False)
    video_no_audio: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    voiceover_no_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duration_mode: Mapped[str] = mapped_column(String, default="fixed", nullable=False)
    video_transition: Mapped[str] = mapped_column(String, default="none", nullable=False)
    bgm_mood: Mapped[str] = mapped_column(String, default="none", nullable=False)
    watermark_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("materials.id"), index=True, nullable=True)
    current_run_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AutoChatMessage(Base):
    __tablename__ = "auto_chat_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("auto_chat_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AutoSessionMaterialSelection(Base):
    __tablename__ = "auto_session_material_selections"
    __table_args__ = (
        UniqueConstraint("session_id", "material_id", name="uq_auto_session_material_selections_session_id_material_id"),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("auto_chat_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    material_id: Mapped[str] = mapped_column(String, ForeignKey("materials.id", ondelete="CASCADE"), index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AutoSessionReferenceVideoSelection(Base):
    __tablename__ = "auto_session_reference_video_selections"
    __table_args__ = (
        UniqueConstraint("session_id", "video_upload_id", name="uq_auto_session_reference_videos_session_video"),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("auto_chat_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    video_upload_id: Mapped[str] = mapped_column(String, ForeignKey("video_uploads.id", ondelete="CASCADE"), index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
