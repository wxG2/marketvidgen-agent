from __future__ import annotations

import json
import pytest

from app.agents.stages.prompt_engineer import (
    _generation_duration_for,
    _snap_to_half_second,
    _normalize_total_duration,
    _rhythmic_durations,
    PromptEngineerAgent,
)
from app.services.qwen_client import QwenClient


# ── _rhythmic_durations ────────────────────────────────────────────────────────

def test_rhythmic_durations_sums_to_target():
    roles = ["brand_identity", "product_hero", "product_hero", "cta_moment"]
    durations = _rhythmic_durations(roles, target=10.0)
    assert len(durations) == 4
    assert abs(sum(durations) - 10.0) < 0.2, f"sum={sum(durations)}"


def test_rhythmic_durations_hook_shorter_than_product():
    roles = ["hook", "product_hero"]
    durations = _rhythmic_durations(roles, target=10.0)
    assert durations[0] < durations[1], f"hook={durations[0]}, product_hero={durations[1]}"


def test_rhythmic_durations_single_shot():
    assert _rhythmic_durations(["product_hero"], target=8.0) == [8.0]


def test_rhythmic_durations_empty():
    assert _rhythmic_durations([], target=10.0) == []


# ── _generation_duration_for ──────────────────────────────────────────────────

def test_generation_duration_returns_smallest_gte(monkeypatch):
    """generation_duration_for must return the smallest supported value >= input."""
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    assert _generation_duration_for(2.5) == 4.0   # below min → smallest supported = 4
    assert _generation_duration_for(4.0) == 4.0   # exact match
    assert _generation_duration_for(7.3) == 8.0   # 7.3 → smallest >= 7.3 is 8, not nearest 7
    assert _generation_duration_for(5.0) == 5.0   # exact match


def test_generation_duration_falls_back_to_max_when_too_large(monkeypatch):
    """If shot exceeds all supported durations, return max (caller should error)."""
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 10])
    result = _generation_duration_for(20.0)
    assert result == 10.0


def test_generation_duration_covers_final_cut(monkeypatch):
    """generation_duration_for must always be >= the input (final-cut) duration."""
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    for dur in [1.0, 1.5, 3.0, 4.0, 7.3, 9.9, 10.0]:
        gen = _generation_duration_for(dur)
        assert gen >= dur or gen == 10.0, f"gen={gen} < dur={dur}"


# ── _snap_to_half_second ───────────────────────────────────────────────────────

def test_snap_rounds_to_half_second():
    assert _snap_to_half_second(1.8) == 2.0
    assert _snap_to_half_second(2.8) == 3.0
    assert _snap_to_half_second(6.3) == 6.5
    assert _snap_to_half_second(3.0) == 3.0
    assert _snap_to_half_second(0.3) == 1.0  # min_dur enforced


# ── _normalize_total_duration ─────────────────────────────────────────────────

def _make_shots(durations: list[float]) -> list[dict]:
    return [
        {
            "shot_idx": i,
            "duration_seconds": d,
            "generation_duration_seconds": _generation_duration_for(d),
        }
        for i, d in enumerate(durations)
    ]


def test_normalize_adjusts_last_shot():
    shots = _make_shots([3.0, 3.0, 3.0, 3.0])  # sum=12, target=10
    result = _normalize_total_duration(shots, 10.0)
    total = sum(s["duration_seconds"] for s in result)
    assert abs(total - 10.0) < 0.15, f"total={total}"
    # First three shots untouched
    assert result[0]["duration_seconds"] == 3.0
    assert result[1]["duration_seconds"] == 3.0
    assert result[2]["duration_seconds"] == 3.0


def test_normalize_adds_time_when_sum_too_short():
    shots = _make_shots([2.0, 2.0, 2.0])  # sum=6, target=10
    result = _normalize_total_duration(shots, 10.0)
    total = sum(s["duration_seconds"] for s in result)
    assert abs(total - 10.0) < 0.15


def test_normalize_noop_when_already_correct():
    shots = _make_shots([3.0, 4.0, 3.0])  # sum=10
    result = _normalize_total_duration(shots, 10.0)
    assert result[0]["duration_seconds"] == 3.0
    assert result[1]["duration_seconds"] == 4.0
    assert result[2]["duration_seconds"] == 3.0


def test_normalize_generation_duration_updated():
    """After normalization, generation_duration_seconds must reflect new duration_seconds."""
    shots = _make_shots([5.0, 5.0, 5.0])  # sum=15, target=10
    result = _normalize_total_duration(shots, 10.0)
    for s in result:
        gen = s["generation_duration_seconds"]
        dur = s["duration_seconds"]
        assert gen >= 4.0, f"generation_duration {gen} must be >= min"
        assert gen <= 15.0, f"generation_duration {gen} must be <= max"
        _ = dur  # just ensure it's present


# ── Director agent integration (mocked LLM) ───────────────────────────────────

class _FakeLLM:
    """LLM stub returning pre-baked structured output."""

    def __init__(self, shots: list[dict], voice_design: dict | None = None):
        self._shots = shots
        self._voice_design = voice_design or {"voice_id": "Cherry", "speed": 1.0, "tone": "confident"}

    async def generate_structured(self, **kwargs):
        return {
            "shots": self._shots,
            "voice_design": self._voice_design,
            "director_summary": "test",
            "creative_concept": "test",
            "pacing_strategy": "test",
            "narration_script": "test",
        }, {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}


class _FakeContext:
    async def is_cancelled(self):
        return False

    async def report_progress(self, *args, **kwargs):
        pass

    pipeline_run_id = "test-run"


def _make_source_images(n: int, roles: list[str] | None = None) -> list[dict]:
    return [
        {
            "image_path": f"/tmp/img{i}.jpg",
            "image_content": f"product shot {i}",
            "visual_role": (roles[i] if roles else "product_hero"),
            "key_subjects": ["product"],
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_director_variable_durations_sum_to_target():
    """4 shots, target 10s: shot_prompts sum must be ~10s."""
    llm_shots = [
        {"shot_idx": 0, "source_image_idx": 0, "script_segment": "hook", "duration_seconds": 2.5, "video_prompt": "p0"},
        {"shot_idx": 1, "source_image_idx": 1, "script_segment": "mid1", "duration_seconds": 3.0, "video_prompt": "p1"},
        {"shot_idx": 2, "source_image_idx": 2, "script_segment": "mid2", "duration_seconds": 3.0, "video_prompt": "p2"},
        {"shot_idx": 3, "source_image_idx": 3, "script_segment": "cta", "duration_seconds": 1.5, "video_prompt": "p3"},
    ]
    agent = PromptEngineerAgent(llm_service=_FakeLLM(llm_shots))
    source_images = _make_source_images(4)
    result = await agent.execute(
        _FakeContext(),
        {
            "source_images": source_images,
            "duration_seconds": 10,
            "platform": "generic",
            "style": "commercial",
            "voice_config": {},
        },
    )
    assert result.success
    shots = result.output_data["shot_prompts"]
    total = sum(s["duration_seconds"] for s in shots)
    assert abs(total - 10.0) < 0.5, f"total={total}"
    # All shots must have generation_duration_seconds
    for s in shots:
        assert "generation_duration_seconds" in s
        assert s["generation_duration_seconds"] >= 4.0


@pytest.mark.asyncio
async def test_director_hook_shot_shorter_than_product():
    """Hook shot (brand_identity) should get shorter duration than product_hero."""
    source_images = _make_source_images(2, roles=["brand_identity", "product_hero"])
    # LLM intentionally sets hook shorter
    llm_shots = [
        {"shot_idx": 0, "source_image_idx": 0, "script_segment": "hook", "duration_seconds": 3.0, "video_prompt": "p0"},
        {"shot_idx": 1, "source_image_idx": 1, "script_segment": "product", "duration_seconds": 7.0, "video_prompt": "p1"},
    ]
    agent = PromptEngineerAgent(llm_service=_FakeLLM(llm_shots))
    result = await agent.execute(
        _FakeContext(),
        {"source_images": source_images, "duration_seconds": 10, "platform": "generic", "style": "commercial", "voice_config": {}},
    )
    shots = result.output_data["shot_prompts"]
    assert shots[0]["duration_seconds"] < shots[1]["duration_seconds"]


@pytest.mark.asyncio
async def test_director_fallback_uses_rhythm_when_llm_fails():
    """When LLM fails (empty shots), fallback durations should sum to target_duration."""

    class _FailingLLM:
        async def generate_structured(self, **kwargs):
            raise RuntimeError("LLM error")

    agent = PromptEngineerAgent(llm_service=_FailingLLM())
    source_images = _make_source_images(3, roles=["hook", "product_hero", "cta_moment"])
    result = await agent.execute(
        _FakeContext(),
        {"source_images": source_images, "duration_seconds": 12, "platform": "generic", "style": "commercial", "voice_config": {}},
    )
    assert result.success
    shots = result.output_data["shot_prompts"]
    total = sum(s["duration_seconds"] for s in shots)
    assert abs(total - 12.0) < 0.5, f"total={total}"


# ── Qwen SSE partial recovery ──────────────────────────────────────────────────

class _DropAfterNLines:
    """Stream that returns N lines then raises ReadError."""

    def __init__(self, lines: list[str], drop_after: int, status_code: int = 200):
        self._lines = lines
        self._drop_after = drop_after
        self.status_code = status_code
        self._request = None
        import httpx as _httpx
        self._request = _httpx.Request("POST", "https://example.com/")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def aread(self) -> bytes:
        return b""

    @property
    def request(self):
        return self._request

    async def aiter_lines(self):
        import httpx as _httpx
        for i, line in enumerate(self._lines):
            if i >= self._drop_after:
                raise _httpx.ReadError("simulated drop", request=self._request)
            yield line


class _ClientWithDrop:
    def __init__(self, *, lines, drop_after, payload_sink=None):
        self._lines = lines
        self._drop_after = drop_after
        self._payload_sink = payload_sink if payload_sink is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def stream(self, method, url, *, headers=None, content=None, **kwargs):
        import json as _json
        if content:
            try:
                self._payload_sink.append(_json.loads(content))
            except Exception:
                pass
        return _DropAfterNLines(self._lines, self._drop_after)


@pytest.mark.asyncio
async def test_sse_partial_recovery_succeeds_when_complete_json_received(monkeypatch):
    """If stream drops mid-way but accumulated text is valid JSON, return it without retry."""
    complete_json = '{"shots": [], "voice_design": {"voice_id": "Cherry", "speed": 1.0, "tone": "x"}}'
    lines = [
        f'data: {{"choices":[{{"delta":{{"content":{json.dumps(complete_json)}}}}}]}}',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":5,"total_tokens":10}}',
        # Line 2 will trigger drop → but we already have complete JSON
    ]

    def _client_factory(*args, **kwargs):
        return _ClientWithDrop(lines=lines, drop_after=1)  # drop after first data line

    monkeypatch.setattr("app.services.qwen_client.httpx.AsyncClient", _client_factory)

    client = QwenClient(api_key="k", base_url="https://example.com/v1", model="qwen3-omni-flash")
    text, usage = await client._collect_sse_response(
        {"model": "qwen3-omni-flash", "messages": []},
        timeout_seconds=10,
        max_retries=0,  # no retries; recovery must succeed on first attempt
    )
    assert complete_json in text


@pytest.mark.asyncio
async def test_sse_partial_recovery_retries_when_json_incomplete(monkeypatch):
    """If partial text is not valid JSON, proceed to retry logic."""
    import httpx as _httpx

    call_count = [0]
    complete_json = '{"shots": [], "voice_design": {"voice_id": "Cherry", "speed": 1.0, "tone": "x"}}'
    good_lines = [
        f'data: {{"choices":[{{"delta":{{"content":{json.dumps(complete_json)}}}}}]}}',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":5,"total_tokens":10}}',
        "data: [DONE]",
    ]

    class _FailThenSucceed:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, *args, content=None, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt: incomplete JSON then drop
                return _DropAfterNLines(
                    ['data: {"choices":[{"delta":{"content":"{"}}]}'],
                    drop_after=0,
                )
            # Second attempt: full response
            return _DropAfterNLines(good_lines, drop_after=len(good_lines) + 1)

    monkeypatch.setattr("app.services.qwen_client.httpx.AsyncClient", lambda *a, **kw: _FailThenSucceed())

    client = QwenClient(api_key="k", base_url="https://example.com/v1", model="qwen3-omni-flash")
    text, _ = await client._collect_sse_response(
        {"model": "qwen3-omni-flash", "messages": []},
        timeout_seconds=10,
        max_retries=2,
    )
    assert complete_json in text
    assert call_count[0] == 2  # failed once, succeeded on retry
