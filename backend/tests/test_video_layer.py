"""Modular tests for the video-generation layer.

Covers: VideoGeneratorAgent duration semantics, no_audio flag forwarding,
and error handling. Uses mock VideoGenerator — no real API calls.
"""
from __future__ import annotations

import pytest

from app.agents.stages.video_generator import VideoGeneratorAgent
from app.services.video_generator import GenerationStatus, GenerationTask, VideoGenerator


class _MockGenerator(VideoGenerator):
    """Records calls and always returns a completed clip."""

    def __init__(self):
        self.calls: list[dict] = []

    async def generate(
        self, image_path, prompt, duration=5, no_audio=True,
        generation_model=None, platform="generic",
    ) -> GenerationTask:
        self.calls.append(
            {"duration": duration, "no_audio": no_audio, "model": generation_model}
        )
        return GenerationTask(task_id="task-mock", status="processing")

    async def poll_status(self, task_id) -> GenerationStatus:
        return GenerationStatus(
            task_id=task_id,
            status="completed",
            video_url="http://cdn.example.com/clip.mp4",
        )


class _FailingGenerator(VideoGenerator):
    async def generate(self, *args, **kwargs) -> GenerationTask:
        return GenerationTask(task_id="t-fail", status="processing")

    async def poll_status(self, task_id) -> GenerationStatus:
        return GenerationStatus(task_id=task_id, status="failed", error="upstream error")


class _FakeContext:
    pipeline_run_id = "test-run"
    artifacts: dict = {}

    async def is_cancelled(self) -> bool:
        return False

    async def report_progress(self, text: str, agent_name: str | None = None) -> None:
        pass


def _shot(
    shot_idx: int = 0,
    duration_seconds: float = 3.0,
    generation_duration_seconds: float = 4.0,
    image_path: str = "/tmp/img.jpg",
) -> dict:
    return {
        "shot_idx": shot_idx,
        "image_path": image_path,
        "video_prompt": "缓慢推镜",
        "duration_seconds": duration_seconds,
        "generation_duration_seconds": generation_duration_seconds,
    }


# ── generation duration semantics ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uses_generation_duration_seconds_not_final_cut(monkeypatch):
    """Model must be called with generation_duration_seconds, not duration_seconds."""
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)

    gen = _MockGenerator()
    agent = VideoGeneratorAgent(video_generator=gen)
    shot = _shot(duration_seconds=2.5, generation_duration_seconds=4.0)
    result = await agent._generate_single(_FakeContext(), shot)

    assert gen.calls[0]["duration"] == 4  # model called with generation_duration (4), not final cut (2.5)
    assert result["duration_seconds"] == 2.5          # final-cut preserved
    assert result["generation_duration_seconds"] == 4  # generation duration tracked


@pytest.mark.asyncio
async def test_generation_duration_covers_final_cut(monkeypatch):
    """generation_duration used by model must be >= final-cut duration."""
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)

    gen = _MockGenerator()
    agent = VideoGeneratorAgent(video_generator=gen)
    shot = _shot(duration_seconds=6.5, generation_duration_seconds=7.0)
    await agent._generate_single(_FakeContext(), shot)

    called_duration = gen.calls[0]["duration"]
    assert called_duration >= 6.5, f"Model called with {called_duration}s < final-cut 6.5s"


@pytest.mark.asyncio
async def test_fails_when_generation_duration_exceeds_all_supported(monkeypatch):
    """If generation_duration_seconds > max supported, raise RuntimeError."""
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)

    gen = _MockGenerator()
    agent = VideoGeneratorAgent(video_generator=gen)
    shot = _shot(duration_seconds=12.0, generation_duration_seconds=12.0)

    with pytest.raises(RuntimeError, match="exceeds"):
        await agent._generate_single(_FakeContext(), shot)


# ── no_audio flag forwarding ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_model_no_audio_forwarded(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)

    gen = _MockGenerator()
    agent = VideoGeneratorAgent(video_generator=gen)
    agent._no_audio = False  # model audio ON
    agent._generation_model = "seedance2.0"
    agent._platform = "douyin"

    await agent._generate_single(_FakeContext(), _shot())
    assert gen.calls[0]["no_audio"] is False


@pytest.mark.asyncio
async def test_model_no_audio_default_is_on(monkeypatch):
    """Default (no flag set) must match settings.SEEDANCE_NO_AUDIO (True = silent)."""
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)
    monkeypatch.setattr(settings, "SEEDANCE_NO_AUDIO", True)

    gen = _MockGenerator()
    agent = VideoGeneratorAgent(video_generator=gen)
    # _no_audio not explicitly set → defaults from settings
    await agent._generate_single(_FakeContext(), _shot())
    assert gen.calls[0]["no_audio"] is True


# ── error handling ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_raises_on_generation_failure(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)

    agent = VideoGeneratorAgent(video_generator=_FailingGenerator())
    with pytest.raises(RuntimeError, match="failed"):
        await agent._generate_single(_FakeContext(), _shot())


@pytest.mark.asyncio
async def test_raises_when_image_path_missing(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)

    agent = VideoGeneratorAgent(video_generator=_MockGenerator())
    shot = _shot(image_path="")  # no image
    with pytest.raises(RuntimeError, match="image_path"):
        await agent._generate_single(_FakeContext(), shot)


# ── result structure ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_contains_required_fields(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)

    agent = VideoGeneratorAgent(video_generator=_MockGenerator())
    result = await agent._generate_single(_FakeContext(), _shot(shot_idx=2, duration_seconds=3.5, generation_duration_seconds=4.0))

    assert result["shot_idx"] == 2
    assert result["video_path"]
    assert "duration_seconds" in result
    assert "generation_duration_seconds" in result
    assert result["duration_seconds"] == 3.5
