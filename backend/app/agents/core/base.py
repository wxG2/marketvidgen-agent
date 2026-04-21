from __future__ import annotations

import json
import time
import uuid
import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.pipeline import AgentExecution, PipelineRun
from app.services.usage_service import UsageRecorder

if TYPE_CHECKING:
    from app.services.agent_memory import AgentMemoryService
    from app.agents.core.tool_registry import ToolRegistry

logger = structlog.get_logger(__name__)


def describe_exception(exc: BaseException) -> str:
    """Return a human-readable exception message, even for blank transport errors."""
    message = str(exc).strip()
    if message:
        return message

    args = ", ".join(repr(arg) for arg in exc.args if arg not in ("", None))
    if args:
        return f"{exc.__class__.__name__}: {args}"
    return exc.__class__.__name__


@dataclass
class AgentContext:
    trace_id: str
    pipeline_run_id: str
    project_id: str
    db_session_factory: async_sessionmaker
    usage_recorder: UsageRecorder
    artifacts: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    active_execution_ids: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False
    # Optional extensions (populated by pipeline executors when available)
    user_id: Optional[str] = None
    memory_service: Optional["AgentMemoryService"] = None
    tool_registry: Optional["ToolRegistry"] = None
    mem0: Optional[Any] = None  # Mem0Service instance for semantic memory
    rag_service: Optional[Any] = None  # RagService instance for RAG retrieval

    async def is_cancelled(self) -> bool:
        if self.cancelled:
            return True

        async with self.db_session_factory() as session:
            run = await session.get(PipelineRun, self.pipeline_run_id)
            self.cancelled = bool(run and run.status == "cancelled")
        return self.cancelled

    async def report_progress(
        self,
        exec_id_or_message: str,
        message: str | None = None,
        *,
        agent_name: str | None = None,
        append: bool = True,
    ) -> None:
        """Update progress_text on an AgentExecution for real-time UI feedback.

        Existing callers may pass ``(exec_id, message)``. Agent implementations
        can pass just ``message`` plus ``agent_name`` while ``BaseAgent.run`` is
        active; the context will resolve the current execution id.
        """
        if message is None:
            message = exec_id_or_message
            exec_id = self.active_execution_ids.get(agent_name or "")
        else:
            exec_id = exec_id_or_message

        if not exec_id:
            return

        async with self.db_session_factory() as session:
            execution = await session.get(AgentExecution, exec_id)
            if execution:
                if append and execution.progress_text:
                    execution.progress_text = f"{execution.progress_text}\n{message}"
                else:
                    execution.progress_text = message
                await session.commit()

    async def save_checkpoint(self) -> None:
        """Persist current artifacts as a checkpoint snapshot on the PipelineRun.

        Called by pipeline executors after each agent completes so that a
        restart can resume from the last successful step.
        """
        snapshot = json.dumps(self.artifacts, ensure_ascii=False, default=str)
        async with self.db_session_factory() as session:
            run = await session.get(PipelineRun, self.pipeline_run_id)
            if run:
                run.artifacts_snapshot = snapshot
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()

    @classmethod
    async def restore_checkpoint(
        cls,
        pipeline_run_id: str,
        db_session_factory: async_sessionmaker,
    ) -> Optional[dict[str, Any]]:
        """Load the last saved artifacts snapshot for *pipeline_run_id*.

        Returns the artifacts dict, or ``None`` if no checkpoint exists.
        """
        async with db_session_factory() as session:
            run = await session.get(PipelineRun, pipeline_run_id)
            if run and run.artifacts_snapshot:
                try:
                    return json.loads(run.artifacts_snapshot)
                except json.JSONDecodeError:
                    return None
        return None


@dataclass
class AgentResult:
    success: bool
    output_data: dict
    error: Optional[str] = None
    usage_records: list[dict[str, Any]] = field(default_factory=list)


class BaseAgent(ABC):
    """Base class for all pipeline agents.

    Subclasses implement `execute()`. The `run()` template method wraps it
    with DB tracking, timing, and error handling.
    """

    name: str = "base"

    @abstractmethod
    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        ...

    async def run(self, context: AgentContext, input_data: dict, attempt: int | None = None) -> AgentResult:
        """Template method: update pipeline status, record start, execute, record end."""
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        # Update PipelineRun.current_agent
        if not await self._update_pipeline_status(context, "running"):
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        # Create AgentExecution record
        if attempt is None:
            attempt = await self._next_attempt_number(context)
        exec_id = await self._record_start(context, input_data, attempt)
        context.active_execution_ids[self.name] = exec_id

        start_time = time.monotonic()
        try:
            result = await self.execute(context, input_data)
            if await context.is_cancelled():
                result = AgentResult(success=False, output_data={}, error="Pipeline cancelled")
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await self._record_complete(context, exec_id, result, duration_ms)
            if result.success:
                try:
                    from app.services.pipeline_artifact_repository import save_agent_artifacts

                    await save_agent_artifacts(
                        context.db_session_factory,
                        pipeline_run_id=context.pipeline_run_id,
                        agent_name=self.name,
                        output_data=result.output_data,
                    )
                except Exception as artifact_exc:
                    logger.warning(
                        "artifact_persist_failed",
                        trace_id=context.trace_id,
                        agent=self.name,
                        error=describe_exception(artifact_exc),
                    )
            for record in result.usage_records:
                await context.usage_recorder.record(
                    pipeline_run_id=context.pipeline_run_id,
                    trace_id=context.trace_id,
                    agent_name=self.name,
                    provider=record.get("provider", "unknown"),
                    model_name=record.get("model_name", "unknown"),
                    operation=record.get("operation", "unknown"),
                    prompt_tokens=int(record.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(record.get("completion_tokens", 0) or 0),
                    total_tokens=int(record.get("total_tokens", 0) or 0),
                    metadata=record.get("metadata"),
                )
            logger.info(
                "agent_completed",
                trace_id=context.trace_id,
                agent=self.name,
                attempt=attempt,
                duration_ms=duration_ms,
                success=result.success,
            )
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            error_message = describe_exception(e)
            error_result = AgentResult(success=False, output_data={}, error=error_message)
            await self._record_complete(context, exec_id, error_result, duration_ms)
            logger.error(
                "agent_failed",
                trace_id=context.trace_id,
                agent=self.name,
                attempt=attempt,
                duration_ms=duration_ms,
                error=error_message,
            )
            return error_result
        finally:
            if context.active_execution_ids.get(self.name) == exec_id:
                context.active_execution_ids.pop(self.name, None)

    async def _update_pipeline_status(self, context: AgentContext, status: str) -> bool:
        async with context.db_session_factory() as session:
            run = await session.get(PipelineRun, context.pipeline_run_id)
            if run:
                if run.status == "cancelled":
                    context.cancelled = True
                    return False
                run.current_agent = self.name
                run.status = status
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()
        return True

    async def _next_attempt_number(self, context: AgentContext) -> int:
        async with context.db_session_factory() as session:
            result = await session.execute(
                select(func.max(AgentExecution.attempt_number)).where(
                    AgentExecution.pipeline_run_id == context.pipeline_run_id,
                    AgentExecution.agent_name == self.name,
                )
            )
            max_attempt = result.scalar_one_or_none() or 0
        return int(max_attempt) + 1

    async def _record_start(self, context: AgentContext, input_data: dict, attempt: int) -> str:
        exec_id = str(uuid.uuid4())
        execution = AgentExecution(
            id=exec_id,
            pipeline_run_id=context.pipeline_run_id,
            trace_id=context.trace_id,
            agent_name=self.name,
            status="running",
            input_data=json.dumps(input_data, ensure_ascii=False, default=str),
            attempt_number=attempt,
        )
        async with context.db_session_factory() as session:
            session.add(execution)
            await session.commit()
        return exec_id

    async def _record_complete(self, context: AgentContext, exec_id: str, result: AgentResult, duration_ms: int):
        async with context.db_session_factory() as session:
            execution = await session.get(AgentExecution, exec_id)
            if execution:
                if execution.status != "cancelled":
                    execution.status = "completed" if result.success else "failed"
                execution.output_data = json.dumps(result.output_data, ensure_ascii=False, default=str)
                execution.error_message = execution.error_message or result.error
                execution.duration_ms = duration_ms
                execution.completed_at = datetime.now(timezone.utc)
                await session.commit()
