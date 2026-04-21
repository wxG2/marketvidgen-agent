"""Agent Analytics API — observability metrics for pipeline runs and agent executions.

Provides aggregated statistics that answer questions like:
  - Which agents are slowest / most likely to fail?
  - What is the QA pass/fail rate?
  - How many tokens are consumed per pipeline run?
  - How does performance change over time?

Endpoints:
  GET /api/analytics/overview        — high-level dashboard summary
  GET /api/analytics/agents          — per-agent duration & success rate
  GET /api/analytics/qa              — QA reviewer pass/fail distribution
  GET /api/analytics/token-usage     — token consumption by model & operation
  GET /api/analytics/pipeline-trends — daily pipeline run counts & success rate
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.pipeline import AgentExecution, PipelineRun
from app.models.usage import ModelUsage

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


# ── Helper ────────────────────────────────────────────────────────────────────

def _utc_days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ── GET /api/analytics/overview ───────────────────────────────────────────────

@router.get("/overview")
async def get_overview(
    days: int = Query(default=7, ge=1, le=90, description="Time window in days"),
    db: AsyncSession = Depends(get_db),
):
    """High-level dashboard: total runs, success rate, avg duration, token cost."""
    since = _utc_days_ago(days)

    # Total runs in window
    total_result = await db.execute(
        select(func.count()).where(PipelineRun.created_at >= since)
    )
    total_runs = total_result.scalar_one()

    # Runs by status
    status_result = await db.execute(
        select(PipelineRun.status, func.count())
        .where(PipelineRun.created_at >= since)
        .group_by(PipelineRun.status)
    )
    status_counts = {row[0]: row[1] for row in status_result.all()}

    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    success_rate = round(completed / total_runs * 100, 1) if total_runs > 0 else 0.0

    # Average agent execution duration across all completed executions
    avg_dur_result = await db.execute(
        select(func.avg(AgentExecution.duration_ms))
        .where(
            AgentExecution.created_at >= since,
            AgentExecution.status == "completed",
        )
    )
    avg_agent_duration_ms = avg_dur_result.scalar_one() or 0

    # Total tokens consumed
    token_result = await db.execute(
        select(func.sum(ModelUsage.total_tokens))
        .where(ModelUsage.created_at >= since)
    )
    total_tokens = token_result.scalar_one() or 0

    return {
        "window_days": days,
        "total_pipeline_runs": total_runs,
        "completed": completed,
        "failed": failed,
        "success_rate_pct": success_rate,
        "status_breakdown": status_counts,
        "avg_agent_duration_ms": round(avg_agent_duration_ms),
        "total_tokens_consumed": total_tokens,
    }


# ── GET /api/analytics/agents ─────────────────────────────────────────────────

@router.get("/agents")
async def get_agent_stats(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Per-agent metrics: avg duration, success rate, retry rate."""
    since = _utc_days_ago(days)

    result = await db.execute(
        select(
            AgentExecution.agent_name,
            AgentExecution.status,
            func.count().label("count"),
            func.avg(AgentExecution.duration_ms).label("avg_duration_ms"),
            func.max(AgentExecution.duration_ms).label("max_duration_ms"),
        )
        .where(AgentExecution.created_at >= since)
        .group_by(AgentExecution.agent_name, AgentExecution.status)
    )
    rows = result.all()

    # Aggregate by agent
    agents: dict[str, dict] = {}
    for row in rows:
        name, status, count, avg_dur, max_dur = row
        if name not in agents:
            agents[name] = {
                "agent_name": name,
                "total_executions": 0,
                "completed": 0,
                "failed": 0,
                "avg_duration_ms": 0,
                "max_duration_ms": 0,
                "success_rate_pct": 0.0,
            }
        agents[name]["total_executions"] += count
        if status == "completed":
            agents[name]["completed"] += count
            agents[name]["avg_duration_ms"] = round(avg_dur or 0)
            agents[name]["max_duration_ms"] = max_dur or 0
        elif status == "failed":
            agents[name]["failed"] += count

    # Compute success rate
    for agent in agents.values():
        total = agent["total_executions"]
        agent["success_rate_pct"] = (
            round(agent["completed"] / total * 100, 1) if total > 0 else 0.0
        )

    return {
        "window_days": days,
        "agents": sorted(agents.values(), key=lambda x: x["total_executions"], reverse=True),
    }


# ── GET /api/analytics/qa ─────────────────────────────────────────────────────

@router.get("/qa")
async def get_qa_stats(
    days: int = Query(default=30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """QA reviewer metrics: pass rate, retry count distribution, avg score."""
    since = _utc_days_ago(days)

    # QA executions
    qa_result = await db.execute(
        select(AgentExecution.status, func.count())
        .where(
            AgentExecution.created_at >= since,
            AgentExecution.agent_name == "qa_reviewer",
        )
        .group_by(AgentExecution.status)
    )
    qa_status = {row[0]: row[1] for row in qa_result.all()}
    total_qa = sum(qa_status.values())

    # Pipeline runs with QA retries > 0
    retry_result = await db.execute(
        select(PipelineRun.retry_count, func.count())
        .where(PipelineRun.created_at >= since)
        .group_by(PipelineRun.retry_count)
    )
    retry_distribution = {str(row[0]): row[1] for row in retry_result.all()}

    # Average overall score (set by QA reviewer on PipelineRun)
    score_result = await db.execute(
        select(func.avg(PipelineRun.overall_score))
        .where(
            PipelineRun.created_at >= since,
            PipelineRun.overall_score.isnot(None),
        )
    )
    avg_score = score_result.scalar_one()

    qa_pass = qa_status.get("completed", 0)
    qa_fail = qa_status.get("failed", 0)

    return {
        "window_days": days,
        "total_qa_executions": total_qa,
        "qa_pass": qa_pass,
        "qa_fail": qa_fail,
        "qa_pass_rate_pct": round(qa_pass / total_qa * 100, 1) if total_qa > 0 else 0.0,
        "avg_overall_score": round(avg_score, 2) if avg_score is not None else None,
        "retry_count_distribution": retry_distribution,
    }


# ── GET /api/analytics/token-usage ────────────────────────────────────────────

@router.get("/token-usage")
async def get_token_usage(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Token consumption breakdown by model and operation."""
    since = _utc_days_ago(days)

    result = await db.execute(
        select(
            ModelUsage.provider,
            ModelUsage.model_name,
            ModelUsage.operation,
            func.sum(ModelUsage.prompt_tokens).label("prompt_tokens"),
            func.sum(ModelUsage.completion_tokens).label("completion_tokens"),
            func.sum(ModelUsage.total_tokens).label("total_tokens"),
            func.count().label("call_count"),
        )
        .where(ModelUsage.created_at >= since)
        .group_by(ModelUsage.provider, ModelUsage.model_name, ModelUsage.operation)
        .order_by(func.sum(ModelUsage.total_tokens).desc())
    )

    rows = [
        {
            "provider": row[0],
            "model_name": row[1],
            "operation": row[2],
            "prompt_tokens": row[3] or 0,
            "completion_tokens": row[4] or 0,
            "total_tokens": row[5] or 0,
            "call_count": row[6],
        }
        for row in result.all()
    ]

    grand_total = sum(r["total_tokens"] for r in rows)

    return {
        "window_days": days,
        "grand_total_tokens": grand_total,
        "breakdown": rows,
    }


# ── GET /api/analytics/pipeline-trends ────────────────────────────────────────

@router.get("/pipeline-trends")
async def get_pipeline_trends(
    days: int = Query(default=14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Daily pipeline run counts and success rate for trend visualization."""
    since = _utc_days_ago(days)

    result = await db.execute(
        select(
            func.date(PipelineRun.created_at).label("date"),
            PipelineRun.status,
            func.count().label("count"),
        )
        .where(PipelineRun.created_at >= since)
        .group_by(func.date(PipelineRun.created_at), PipelineRun.status)
        .order_by(func.date(PipelineRun.created_at))
    )

    # Group by date
    daily: dict[str, dict] = {}
    for row in result.all():
        date_str, status, count = str(row[0]), row[1], row[2]
        if date_str not in daily:
            daily[date_str] = {"date": date_str, "total": 0, "completed": 0, "failed": 0}
        daily[date_str]["total"] += count
        if status == "completed":
            daily[date_str]["completed"] += count
        elif status == "failed":
            daily[date_str]["failed"] += count

    trends = sorted(daily.values(), key=lambda x: x["date"])
    for d in trends:
        d["success_rate_pct"] = (
            round(d["completed"] / d["total"] * 100, 1) if d["total"] > 0 else 0.0
        )

    return {"window_days": days, "daily_trends": trends}
