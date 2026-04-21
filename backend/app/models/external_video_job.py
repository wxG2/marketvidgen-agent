from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExternalVideoJob(Base):
    __tablename__ = "external_video_jobs"
    __table_args__ = (
        UniqueConstraint("api_key_id", "idempotency_key_hash", name="uq_external_video_jobs_api_key_id_idempotency_key_hash"),
        Index("ix_external_video_jobs_user_id_created_at", "user_id", "created_at"),
        Index("ix_external_video_jobs_pipeline_run_id", "pipeline_run_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    api_key_id: Mapped[str] = mapped_column(String, ForeignKey("api_keys.id", ondelete="CASCADE"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    client_reference_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
