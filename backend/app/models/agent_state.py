from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentThread(Base):
    __tablename__ = "agent_threads"
    __table_args__ = (
        CheckConstraint(
            "thread_type IN ('chat', 'task', 'pipeline')",
            name="thread_type",
        ),
        CheckConstraint(
            "status IN ('active', 'completed', 'failed', 'archived')",
            name="status",
        ),
        Index("ix_agent_threads_user_id_last_activity_at", "user_id", "last_activity_at"),
        Index("ix_agent_threads_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    thread_type: Mapped[str] = mapped_column(String, default="chat", nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_confirmation', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
        Index("ix_agent_runs_thread_id_status", "thread_id", "status"),
        Index("ix_agent_runs_thread_id_created_at", "thread_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String, default="queued", nullable=False)
    current_step_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_node_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resume_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('system', 'user', 'assistant', 'tool')",
            name="role",
        ),
        Index("ix_agent_messages_thread_id_created_at", "thread_id", "created_at"),
        Index("ix_agent_messages_run_id_created_at", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_id_step_index"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped', 'cancelled', 'waiting_confirmation')",
            name="status",
        ),
        CheckConstraint("step_index >= 0", name="step_index_non_negative"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="duration_ms_non_negative",
        ),
        Index("ix_agent_steps_run_id_step_index", "run_id", "step_index"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String, nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    node_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    input_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        UniqueConstraint("checkpoint_key", name="uq_agent_checkpoints_checkpoint_key"),
        CheckConstraint(
            "status IN ('stable', 'resume_ready', 'expired', 'superseded')",
            name="status",
        ),
        Index("ix_agent_checkpoints_thread_id_created_at", "thread_id", "created_at"),
        Index("ix_agent_checkpoints_run_id_step_index", "run_id", "step_index"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_threads.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    step_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    node_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    checkpoint_key: Mapped[str] = mapped_column(String, nullable=False)
    resume_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="stable", nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("run_id", "call_key", name="uq_tool_calls_run_id_call_key"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        Index("ix_tool_calls_run_id_created_at", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    step_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("agent_steps.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    step_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    call_key: Mapped[str] = mapped_column(String, nullable=False)
    arguments_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_name", "version", name="uq_prompt_versions_prompt_name_version"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    prompt_name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("input_tokens >= 0", name="input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
        CheckConstraint("total_tokens >= 0", name="total_tokens_non_negative"),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="latency_ms_non_negative",
        ),
        Index("ix_model_calls_run_id_created_at", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    step_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("agent_steps.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    prompt_version_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    request_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        Index("ix_run_events_run_id_created_at", "run_id", "created_at"),
        Index("ix_run_events_thread_id_created_at", "thread_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_threads.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    step_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("agent_steps.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    tool_call_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("tool_calls.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class RetrievalDocument(Base):
    __tablename__ = "retrieval_documents"
    __table_args__ = (
        UniqueConstraint(
            "source_table",
            "source_id",
            "content_hash",
            "embedding_version",
            name="uq_retrieval_documents_source_table_source_id_content_hash_embedding_version",
        ),
        Index("ix_retrieval_documents_source_table_source_id", "source_table", "source_id"),
        Index("ix_retrieval_documents_indexed_at", "indexed_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_table: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    document_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    embedding_dimensions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    embedding_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    vector_document_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
