from __future__ import annotations

import json
import time
import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.pipeline import AgentExecution, PipelineRun
from app.services.usage_service import UsageRecorder

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    trace_id: str
    pipeline_run_id: str
    project_id: str
    db_session_factory: async_sessionmaker
    usage_recorder: UsageRecorder
    artifacts: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False


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

    async def run(self, context: AgentContext, input_data: dict, attempt: int = 1) -> AgentResult:
        """Template method: update pipeline status, record start, execute, record end."""
        if context.cancelled:
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        # Update PipelineRun.current_agent
        await self._update_pipeline_status(context, "running")

        # Create AgentExecution record
        exec_id = await self._record_start(context, input_data, attempt)

        start_time = time.monotonic()
        try:
            result = await self.execute(context, input_data)
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await self._record_complete(context, exec_id, result, duration_ms)
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
            logger.info(f"[{context.trace_id}] Agent '{self.name}' completed (attempt {attempt}, {duration_ms}ms)")
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            error_result = AgentResult(success=False, output_data={}, error=str(e))
            await self._record_complete(context, exec_id, error_result, duration_ms)
            logger.error(f"[{context.trace_id}] Agent '{self.name}' failed: {e}")
            return error_result

    async def _update_pipeline_status(self, context: AgentContext, status: str):
        async with context.db_session_factory() as session:
            run = await session.get(PipelineRun, context.pipeline_run_id)
            if run:
                run.current_agent = self.name
                run.status = status
                run.updated_at = datetime.utcnow()
                await session.commit()

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
                execution.status = "completed" if result.success else "failed"
                execution.output_data = json.dumps(result.output_data, ensure_ascii=False, default=str)
                execution.error_message = result.error
                execution.duration_ms = duration_ms
                execution.completed_at = datetime.utcnow()
                await session.commit()
