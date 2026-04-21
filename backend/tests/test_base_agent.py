"""Unit tests for BaseAgent template method (run + cancellation + error handling)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.core.base import AgentContext, AgentResult, BaseAgent, describe_exception
from app.services.usage_service import UsageRecorder


# ── Helpers ───────────────────────────────────────────────────────────────────

class _SuccessAgent(BaseAgent):
    name = "success_agent"

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        return AgentResult(success=True, output_data={"result": "ok"})


class _FailingAgent(BaseAgent):
    name = "failing_agent"

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        raise ValueError("deliberate failure")


class _SoftFailAgent(BaseAgent):
    name = "soft_fail_agent"

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        return AgentResult(success=False, output_data={}, error="soft failure")


def _make_context(cancelled: bool = False) -> AgentContext:
    """Build a minimal AgentContext with mocked DB dependencies."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    usage_recorder = AsyncMock(spec=UsageRecorder)

    ctx = AgentContext(
        trace_id=str(uuid.uuid4()),
        pipeline_run_id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        db_session_factory=mock_factory,
        usage_recorder=usage_recorder,
    )
    ctx.cancelled = cancelled
    return ctx


# ── describe_exception ────────────────────────────────────────────────────────

def test_describe_exception_with_message():
    exc = ValueError("something went wrong")
    assert "something went wrong" in describe_exception(exc)


def test_describe_exception_no_message():
    class BlankError(Exception):
        pass
    exc = BlankError()
    assert "BlankError" in describe_exception(exc)


def test_describe_exception_with_args():
    exc = OSError(5, "Input/output error")
    result = describe_exception(exc)
    assert result  # Should not be empty


# ── AgentContext ──────────────────────────────────────────────────────────────

def test_agent_context_cancelled_flag():
    ctx = _make_context(cancelled=True)
    assert ctx.cancelled is True


def test_agent_context_rag_service_default_none():
    ctx = _make_context()
    assert ctx.rag_service is None


# ── BaseAgent.run — success path ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_returns_success_result():
    agent = _SuccessAgent()
    ctx = _make_context()

    # Mock all DB interactions
    with (
        patch.object(agent, "_update_pipeline_status", return_value=True),
        patch.object(agent, "_next_attempt_number", return_value=1),
        patch.object(agent, "_record_start", return_value="exec-id"),
        patch.object(agent, "_record_complete"),
        patch("app.services.pipeline_artifact_repository.save_agent_artifacts", new_callable=AsyncMock),
    ):
        result = await agent.run(ctx, {})

    assert result.success is True
    assert result.output_data == {"result": "ok"}


@pytest.mark.asyncio
async def test_run_returns_failure_when_pipeline_cancelled_before_start():
    agent = _SuccessAgent()
    ctx = _make_context(cancelled=True)

    # is_cancelled() will be True; should short-circuit
    result = await agent.run(ctx, {})
    assert result.success is False
    assert "cancelled" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_run_handles_execute_exception_gracefully():
    agent = _FailingAgent()
    ctx = _make_context()

    with (
        patch.object(agent, "_update_pipeline_status", return_value=True),
        patch.object(agent, "_next_attempt_number", return_value=1),
        patch.object(agent, "_record_start", return_value="exec-id"),
        patch.object(agent, "_record_complete"),
    ):
        result = await agent.run(ctx, {})

    assert result.success is False
    assert "deliberate failure" in (result.error or "")


@pytest.mark.asyncio
async def test_run_clears_active_execution_id_on_success():
    agent = _SuccessAgent()
    ctx = _make_context()

    with (
        patch.object(agent, "_update_pipeline_status", return_value=True),
        patch.object(agent, "_next_attempt_number", return_value=1),
        patch.object(agent, "_record_start", return_value="exec-999"),
        patch.object(agent, "_record_complete"),
        patch("app.services.pipeline_artifact_repository.save_agent_artifacts", new_callable=AsyncMock),
    ):
        await agent.run(ctx, {})

    assert "success_agent" not in ctx.active_execution_ids


@pytest.mark.asyncio
async def test_run_clears_active_execution_id_on_failure():
    agent = _FailingAgent()
    ctx = _make_context()

    with (
        patch.object(agent, "_update_pipeline_status", return_value=True),
        patch.object(agent, "_next_attempt_number", return_value=1),
        patch.object(agent, "_record_start", return_value="exec-999"),
        patch.object(agent, "_record_complete"),
    ):
        await agent.run(ctx, {})

    assert "failing_agent" not in ctx.active_execution_ids
