from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("auto_chat_sessions.id", ondelete="SET NULL"), index=True, nullable=True)
    trace_id: Mapped[str] = mapped_column(String, default=lambda: str(uuid.uuid4()))
    engine: Mapped[str] = mapped_column(String, default="pipeline")
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | running | completed | failed | cancelled
    input_config: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    current_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    swarm_state_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latest_lead_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # JSON snapshot of AgentContext.artifacts after the last completed agent.
    # Used for checkpoint-based resume after server restarts or partial failures.
    artifacts_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_video_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AgentExecution(Base):
    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_run_id: Mapped[str] = mapped_column(String, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    trace_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)  # orchestrator | prompt_engineer | audio_subtitle | video_generator | video_editor | qa_reviewer
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | running | completed | failed | skipped
    input_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    output_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
