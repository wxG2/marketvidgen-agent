from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VideoDelivery(Base):
    __tablename__ = "video_deliveries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String, nullable=False)  # save | publish
    platform: Mapped[str] = mapped_column(String, nullable=False)  # repository | douyin
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)  # pending | saved | published | failed
    social_account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("social_accounts.id"), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    saved_video_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    platform_error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
