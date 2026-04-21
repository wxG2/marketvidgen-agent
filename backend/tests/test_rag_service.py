"""Unit tests for RagService (without real Qdrant — tests graceful degradation)."""
from __future__ import annotations

import pytest

from app.services.rag_service import RagService


@pytest.fixture
def rag_offline() -> RagService:
    """RagService pointing at a non-existent Qdrant — should degrade gracefully."""
    return RagService(
        qwen_api_key="mock",
        qwen_api_url="http://localhost:9999",  # nothing listening here
        qdrant_host="localhost",
        qdrant_port=19999,  # nothing listening here
    )


# ── Graceful degradation ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_initialize_offline_does_not_raise(rag_offline: RagService):
    """initialize() must not raise even if Qdrant is unavailable."""
    await rag_offline.initialize()
    assert rag_offline._ready is False


@pytest.mark.asyncio
async def test_retrieve_offline_returns_empty(rag_offline: RagService):
    """retrieve_similar() returns [] when Qdrant is unavailable."""
    await rag_offline.initialize()
    results = await rag_offline.retrieve_similar("make a douyin video", platform="douyin")
    assert results == []


@pytest.mark.asyncio
async def test_index_offline_returns_false(rag_offline: RagService):
    """index_pipeline_run() returns False when not ready."""
    await rag_offline.initialize()
    ok = await rag_offline.index_pipeline_run(
        run_id="abc",
        requirement="test",
        platform="douyin",
        style="commercial",
        plan_summary="3 shots",
    )
    assert ok is False


# ── format_for_prompt ─────────────────────────────────────────────────────────

def test_format_for_prompt_empty(rag_offline: RagService):
    """Returns empty string when no examples provided."""
    result = rag_offline.format_for_prompt([])
    assert result == ""


def test_format_for_prompt_single_example(rag_offline: RagService):
    examples = [
        {
            "run_id": "run-1",
            "requirement": "创意产品视频",
            "platform": "douyin",
            "style": "commercial",
            "plan_summary": "3个镜头，产品特写+场景+CTA",
            "overall_score": 8.5,
            "similarity_score": 0.91,
        }
    ]
    result = rag_offline.format_for_prompt(examples)
    assert "RAG" in result
    assert "douyin" in result
    assert "commercial" in result
    assert "0.91" in result


def test_format_for_prompt_multiple_examples(rag_offline: RagService):
    examples = [
        {"run_id": f"run-{i}", "requirement": f"req{i}", "platform": "douyin",
         "style": "lifestyle", "plan_summary": "...", "overall_score": 7.0,
         "similarity_score": 0.8 - i * 0.05}
        for i in range(3)
    ]
    result = rag_offline.format_for_prompt(examples)
    assert "参考案例 1" in result
    assert "参考案例 2" in result
    assert "参考案例 3" in result


def test_format_for_prompt_missing_fields(rag_offline: RagService):
    """format_for_prompt should handle missing fields without crashing."""
    examples = [{"run_id": "x", "similarity_score": 0.7}]
    result = rag_offline.format_for_prompt(examples)
    assert "参考案例 1" in result


# ── retrieve_similar not ready ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieve_without_initialize_returns_empty(rag_offline: RagService):
    """retrieve_similar() with _ready=False returns [] without crashing."""
    # Don't call initialize() — _ready stays False
    results = await rag_offline.retrieve_similar("test query")
    assert results == []
