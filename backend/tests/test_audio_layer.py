"""Modular tests for the audio/subtitle layer.

Covers: AudioSubtitleAgent behaviour with various voiceover_no_audio combinations.
Uses mock TTS service — no real audio synthesis, no database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.agents.stages.audio_subtitle import AudioSubtitleAgent
from app.services.tts_service import RealTTSService


@dataclass
class _TtsResult:
    audio_path: str
    duration_ms: int
    usage: dict = field(default_factory=dict)


class _FakeTTS:
    """TTS stub that records calls and returns a fake result."""

    def __init__(self):
        self.calls: list[dict] = []

    async def synthesize(self, text: str, voice_id: str = "default", speed: float = 1.0, tone: str | None = None):
        self.calls.append({"text": text, "voice_id": voice_id, "speed": speed, "tone": tone})
        return _TtsResult(audio_path="/tmp/tts_fake.mp3", duration_ms=3000)

    async def generate_subtitles(self, text: str, audio_path: str) -> str:
        return "/tmp/tts_fake.srt"


class _NeverCallTTS:
    async def synthesize(self, **_):
        raise AssertionError("TTS must not be called when voiceover_no_audio=True")

    async def generate_subtitles(self, **_):
        raise AssertionError("subtitles must not be generated when voiceover_no_audio=True")


class _FakeContext:
    async def is_cancelled(self) -> bool:
        return False

    async def report_progress(self, *args, **kwargs) -> None:
        pass


# ── skip logic ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skips_when_voiceover_no_audio_true():
    agent = AudioSubtitleAgent(tts_service=_NeverCallTTS())
    result = await agent.execute(
        _FakeContext(),
        {"script": "产品展示开场。", "voiceover_no_audio": True},
    )
    assert result.success is True
    assert result.output_data["skipped"] is True
    assert result.output_data["skip_reason"] == "voiceover_no_audio"
    assert result.output_data["audio_path"] == ""


@pytest.mark.asyncio
async def test_skips_via_legacy_no_audio_flag():
    """Legacy no_audio=True must also trigger voiceover skip."""
    agent = AudioSubtitleAgent(tts_service=_NeverCallTTS())
    result = await agent.execute(
        _FakeContext(),
        {"script": "产品展示开场。", "no_audio": True},
    )
    assert result.success is True
    assert result.output_data["skipped"] is True
    assert result.output_data["skip_reason"] == "voiceover_no_audio"


@pytest.mark.asyncio
async def test_skips_when_script_empty():
    tts = _FakeTTS()
    agent = AudioSubtitleAgent(tts_service=tts)
    result = await agent.execute(
        _FakeContext(),
        {"script": "", "voiceover_no_audio": False},
    )
    assert result.success is True
    assert result.output_data["skipped"] is True
    assert result.output_data["skip_reason"] == "empty_script"
    assert tts.calls == []


# ── TTS invocation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generates_audio_when_enabled():
    tts = _FakeTTS()
    agent = AudioSubtitleAgent(tts_service=tts)
    result = await agent.execute(
        _FakeContext(),
        {
            "script": "这是一款高效保湿面霜。",
            "voiceover_no_audio": False,
            "voice_params": {"voice_id": "Cherry", "speed": 1.1},
        },
    )
    assert result.success is True
    assert result.output_data.get("audio_path") == "/tmp/tts_fake.mp3"
    assert result.output_data.get("subtitle_path") == "/tmp/tts_fake.srt"
    assert tts.calls[0]["voice_id"] == "Cherry"
    assert tts.calls[0]["speed"] == 1.1


@pytest.mark.asyncio
async def test_default_voiceover_enabled_when_no_flag():
    """With no flag at all, default is enabled (False = generate TTS)."""
    tts = _FakeTTS()
    agent = AudioSubtitleAgent(tts_service=tts)
    result = await agent.execute(
        _FakeContext(),
        {"script": "开场旁白文案。"},  # no voiceover_no_audio key
    )
    # Should NOT skip — default is to generate TTS
    assert result.success is True
    assert not result.output_data.get("skipped", False)
    assert tts.calls  # TTS was called


@pytest.mark.asyncio
async def test_voice_params_forwarded_correctly():
    tts = _FakeTTS()
    agent = AudioSubtitleAgent(tts_service=tts)
    await agent.execute(
        _FakeContext(),
        {
            "script": "展示文案。",
            "voiceover_no_audio": False,
            "voice_params": {"voice_id": "Ethan", "speed": 0.9},
        },
    )
    assert tts.calls[0]["voice_id"] == "Ethan"
    assert tts.calls[0]["speed"] == 0.9


@pytest.mark.asyncio
async def test_voice_tone_forwarded_to_tts():
    tts = _FakeTTS()
    agent = AudioSubtitleAgent(tts_service=tts)
    await agent.execute(
        _FakeContext(),
        {
            "script": "像朋友一样介绍这款新品。",
            "voiceover_no_audio": False,
            "voice_params": {"voice_id": "Cherry", "speed": 1.05, "tone": "亲切种草"},
        },
    )
    assert tts.calls[0]["tone"] == "亲切种草"


@pytest.mark.asyncio
async def test_real_tts_service_builds_instructions_from_tone_and_speed(monkeypatch, tmp_path):
    calls: list[dict] = []

    class _FakeQwenClient:
        async def tts(self, **kwargs):
            calls.append(kwargs)
            Path(kwargs["output_path"]).write_bytes(b"raw wav")
            return {"total_tokens": 12}

    async def _fake_convert_to_mp3(input_path: str, output_path: str) -> int:
        Path(output_path).write_bytes(b"mp3")
        return 1200

    monkeypatch.setattr(RealTTSService, "_convert_to_mp3", staticmethod(_fake_convert_to_mp3))
    service = RealTTSService(
        api_key="k",
        api_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3-tts-instruct-flash",
        output_dir=str(tmp_path),
    )
    service.client = _FakeQwenClient()

    result = await service.synthesize("新品开场介绍。", voice_id="Cherry", speed=1.2, tone="exciting")

    assert result.duration_ms == 1200
    assert calls[0]["voice"] == "Cherry"
    assert "明快有活力" in calls[0]["instructions"]
    assert "语速偏快" in calls[0]["instructions"]
