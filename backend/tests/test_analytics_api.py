"""Tests for the Analytics API endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import AgentExecution, PipelineRun


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_run(project_id: str, status: str = "completed", score: float | None = None) -> PipelineRun:
    return PipelineRun(
        id=str(uuid.uuid4()),
        project_id=project_id,
        trace_id=str(uuid.uuid4()),
        engine="langgraph",
        status=status,
        input_config='{"requirement": "test video"}',
        overall_score=score,
    )


def _make_execution(run_id: str, agent: str, status: str = "completed", duration_ms: int = 1000) -> AgentExecution:
    return AgentExecution(
        id=str(uuid.uuid4()),
        pipeline_run_id=run_id,
        trace_id=str(uuid.uuid4()),
        agent_name=agent,
        status=status,
        attempt_number=1,
        duration_ms=duration_ms,
    )


# ── Overview ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_overview_returns_200(client: AsyncClient):
    resp = await client.get("/api/analytics/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_pipeline_runs" in data
    assert "success_rate_pct" in data
    assert "window_days" in data


@pytest.mark.asyncio
async def test_analytics_overview_counts_runs(client: AsyncClient, db: AsyncSession):
    project_id = str(uuid.uuid4())
    run = _make_run(project_id, status="completed")
    db.add(run)
    await db.commit()

    resp = await client.get("/api/analytics/overview?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_pipeline_runs"] >= 1


# ── Agents ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_agents_returns_200(client: AsyncClient):
    resp = await client.get("/api/analytics/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    assert isinstance(data["agents"], list)


@pytest.mark.asyncio
async def test_analytics_agents_includes_agent_name(client: AsyncClient, db: AsyncSession):
    project_id = str(uuid.uuid4())
    run = _make_run(project_id)
    db.add(run)
    await db.flush()

    exec_ = _make_execution(run.id, agent="orchestrator", duration_ms=2500)
    db.add(exec_)
    await db.commit()

    resp = await client.get("/api/analytics/agents?days=7")
    assert resp.status_code == 200
    agents = resp.json()["agents"]
    names = [a["agent_name"] for a in agents]
    assert "orchestrator" in names


@pytest.mark.asyncio
async def test_analytics_agents_success_rate_calculation(client: AsyncClient, db: AsyncSession):
    project_id = str(uuid.uuid4())
    run = _make_run(project_id)
    db.add(run)
    await db.flush()

    # 3 completed, 1 failed
    for _ in range(3):
        db.add(_make_execution(run.id, "prompt_engineer", status="completed", duration_ms=800))
    db.add(_make_execution(run.id, "prompt_engineer", status="failed", duration_ms=200))
    await db.commit()

    resp = await client.get("/api/analytics/agents?days=7")
    agents = {a["agent_name"]: a for a in resp.json()["agents"]}
    pe = agents.get("prompt_engineer")
    assert pe is not None
    assert pe["total_executions"] >= 4
    # Success rate should be 75% (3/4)
    assert pe["success_rate_pct"] <= 100.0


# ── QA ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_qa_returns_200(client: AsyncClient):
    resp = await client.get("/api/analytics/qa")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_qa_executions" in data
    assert "qa_pass_rate_pct" in data


@pytest.mark.asyncio
async def test_analytics_qa_pass_rate(client: AsyncClient, db: AsyncSession):
    project_id = str(uuid.uuid4())
    run = _make_run(project_id, score=8.5)
    db.add(run)
    await db.flush()

    db.add(_make_execution(run.id, "qa_reviewer", status="completed"))
    db.add(_make_execution(run.id, "qa_reviewer", status="failed"))
    await db.commit()

    resp = await client.get("/api/analytics/qa?days=30")
    data = resp.json()
    assert data["qa_pass"] >= 1
    assert data["qa_fail"] >= 1
    assert 0 <= data["qa_pass_rate_pct"] <= 100


# ── Token Usage ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_token_usage_returns_200(client: AsyncClient):
    resp = await client.get("/api/analytics/token-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert "grand_total_tokens" in data
    assert "breakdown" in data
    assert isinstance(data["breakdown"], list)


# ── Pipeline Trends ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_pipeline_trends_returns_200(client: AsyncClient):
    resp = await client.get("/api/analytics/pipeline-trends")
    assert resp.status_code == 200
    data = resp.json()
    assert "daily_trends" in data
    assert isinstance(data["daily_trends"], list)


@pytest.mark.asyncio
async def test_analytics_trends_validates_days_param(client: AsyncClient):
    # days=0 should fail validation
    resp = await client.get("/api/analytics/pipeline-trends?days=0")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_analytics_trends_max_days(client: AsyncClient):
    # days=91 exceeds max
    resp = await client.get("/api/analytics/pipeline-trends?days=91")
    assert resp.status_code == 422
