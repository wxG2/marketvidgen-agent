from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.stages.remix_assembler import RemixAssemblerAgent
from app.agents.stages.remix_planner import RemixPlannerAgent, validate_remix_plan
from app.agents.executors.shared import PipelineExecutorSupportMixin
from app.models.material import Material
from app.services.video_editing.video_profiler import ShotProfile, VideoProfile


class _DummyLLM:
    async def generate_structured(self, **kwargs):
        raise AssertionError("should not call llm in this unit test")


class _DummyProfiler:
    async def profile_video(self, *args, **kwargs):
        raise AssertionError("should not call profiler in this unit test")


def _shot(
    *,
    video_id: str,
    shot_idx: int,
    start: float,
    end: float,
    emotion: str,
    quality: float,
    desc: str,
) -> ShotProfile:
    return ShotProfile(
        video_id=video_id,
        shot_idx=shot_idx,
        start_seconds=start,
        end_seconds=end,
        duration_seconds=end - start,
        keyframe_path=f"/tmp/{video_id}_{shot_idx}.jpg",
        scene_change_score=1.0,
        audio_mean_volume=-20.0,
        audio_max_volume=-8.0,
        description=desc,
        emotion_tag=emotion,
        visual_quality_score=quality,
    )


def _planner() -> RemixPlannerAgent:
    return RemixPlannerAgent(llm_service=_DummyLLM(), video_profiler=_DummyProfiler())


class _FakeSession:
    def __init__(self, materials: dict[str, Material]):
        self.materials = materials

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, item_id):
        if model is Material:
            return self.materials.get(item_id)
        return None


class _FakeContext:
    def __init__(self, materials: dict[str, Material], user_id: str = "user-1"):
        self.user_id = user_id
        self.db_session_factory = lambda: _FakeSession(materials)


@pytest.mark.asyncio
async def test_resolve_bgm_context_accepts_explicit_audio_material(tmp_path: Path, monkeypatch):
    from app.agents.stages import remix_planner as remix_planner_module
    from app.core.config import settings

    monkeypatch.setattr(settings, "MATERIALS_ROOT", str(tmp_path))

    async def _fake_probe(*_args, **_kwargs):
        return 12.5

    monkeypatch.setattr(remix_planner_module, "_probe_duration", _fake_probe)
    audio_path = tmp_path / "user-1" / "音乐" / "beat.mp3"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"ID3" + b"\0" * 16)
    material = Material(
        id="bgm-1",
        user_id="user-1",
        category="音乐",
        filename="beat.mp3",
        file_path=str(audio_path.relative_to(tmp_path)),
        media_type="audio",
    )

    context, error = await _planner()._resolve_bgm_context(
        _FakeContext({"bgm-1": material}),
        {"remix_config": {"bgm_material_id": "bgm-1", "bgm_mood": "cinematic"}},
    )

    assert error is None
    assert context["bgm_source"] == "uploaded"
    assert context["bgm_material_id"] == "bgm-1"
    assert context["bgm_path"] == str(audio_path.resolve())
    assert context["bgm_duration_seconds"] == 12.5


@pytest.mark.asyncio
async def test_resolve_bgm_context_rejects_non_audio_material(tmp_path: Path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MATERIALS_ROOT", str(tmp_path))
    image_path = tmp_path / "user-1" / "素材" / "image.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    material = Material(
        id="image-1",
        user_id="user-1",
        category="素材",
        filename="image.png",
        file_path=str(image_path.relative_to(tmp_path)),
        media_type="image",
    )

    context, error = await _planner()._resolve_bgm_context(
        _FakeContext({"image-1": material}),
        {"remix_config": {"bgm_material_id": "image-1"}},
    )

    assert context == {}
    assert error is not None
    assert "必须是音频" in error


def test_normalize_plan_uses_shot_boundaries_and_nonzero_segments():
    planner = _planner()
    profiles = [
        VideoProfile(
            video_id="video-a",
            video_path="/tmp/video-a.mp4",
            duration_seconds=6.0,
            shots=[
                _shot(video_id="video-a", shot_idx=0, start=0.0, end=2.0, emotion="exciting", quality=6.0, desc="a0"),
                _shot(video_id="video-a", shot_idx=1, start=2.0, end=4.0, emotion="calm", quality=8.0, desc="a1"),
                _shot(video_id="video-a", shot_idx=2, start=4.0, end=6.0, emotion="warm", quality=9.0, desc="a2"),
            ],
        ),
        VideoProfile(
            video_id="video-b",
            video_path="/tmp/video-b.mp4",
            duration_seconds=6.0,
            shots=[
                _shot(video_id="video-b", shot_idx=0, start=0.0, end=2.0, emotion="neutral", quality=8.0, desc="b0"),
                _shot(video_id="video-b", shot_idx=1, start=2.0, end=4.0, emotion="dramatic", quality=9.0, desc="b1"),
                _shot(video_id="video-b", shot_idx=2, start=4.0, end=6.0, emotion="calm", quality=7.0, desc="b2"),
            ],
        ),
    ]
    shot_lookup = {(shot.video_id, shot.shot_idx): shot for profile in profiles for shot in profile.shots}

    normalized = planner._normalize_plan(
        {
            "title": "bad plan",
            "concept": "bad plan",
            "segments": [
                {"source_video_id": "video-a", "source_shot_idx": 0, "start_seconds": 1.21, "end_seconds": 1.21},
                {"source_video_id": "video-a", "source_shot_idx": 0, "start_seconds": 1.3, "end_seconds": 1.31},
                {"source_video_id": "video-a", "source_shot_idx": 0, "start_seconds": 1.35, "end_seconds": 1.36},
                {"source_video_id": "video-b", "source_shot_idx": 0, "start_seconds": 0.6, "end_seconds": 0.7},
            ],
            "audio_design": {"strategy": "silent", "bgm_mood": "none", "bgm_volume": 0.0},
        },
        profiles,
        {"duration_seconds": 8.0, "remix_config": {"target_duration_seconds": 8.0}},
    )

    assert normalized["segments"]
    unique_sources = {(seg["source_video_id"], seg["source_shot_idx"]) for seg in normalized["segments"]}
    assert len(unique_sources) > 1

    previous = None
    for seg in normalized["segments"]:
        source_key = (seg["source_video_id"], seg["source_shot_idx"])
        shot = shot_lookup[source_key]
        assert seg["start_seconds"] == pytest.approx(shot.start_seconds)
        assert seg["end_seconds"] == pytest.approx(shot.end_seconds)
        assert seg["end_seconds"] - seg["start_seconds"] >= 1.0
        assert source_key != previous
        previous = source_key

    assert validate_remix_plan(normalized) is None


def test_normalize_plan_adds_voiceover_text_when_requested():
    planner = _planner()
    profiles = [
        VideoProfile(
            video_id="video-a",
            video_path="/tmp/video-a.mp4",
            duration_seconds=2.0,
            shots=[
                _shot(video_id="video-a", shot_idx=0, start=0.0, end=2.0, emotion="warm", quality=8.0, desc="产品开场特写"),
            ],
        ),
        VideoProfile(
            video_id="video-b",
            video_path="/tmp/video-b.mp4",
            duration_seconds=2.0,
            shots=[
                _shot(video_id="video-b", shot_idx=0, start=0.0, end=2.0, emotion="calm", quality=8.0, desc="用户使用场景"),
            ],
        ),
    ]

    normalized = planner._normalize_plan(
        {
            "title": "voiceover plan",
            "concept": "voiceover plan",
            "segments": [
                {
                    "source_video_id": "video-a",
                    "source_shot_idx": 0,
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "voiceover": "先用一个细节抓住注意力。",
                },
                {
                    "source_video_id": "video-b",
                    "source_shot_idx": 0,
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                },
            ],
            "audio_design": {"strategy": "silent"},
        },
        profiles,
        {"duration_seconds": 4.0, "remix_config": {"target_duration_seconds": 4.0, "add_voiceover": True}},
    )

    assert normalized["segments"][0]["voiceover"] == "先用一个细节抓住注意力。"
    # Segment 1 has no LLM voiceover — should be rewritten to a complete sentence,
    # NOT a copy of the description.
    seg1_vo = normalized["segments"][1]["voiceover"]
    assert seg1_vo != "用户使用场景"
    assert seg1_vo.endswith(("。", "！", "？"))
    assert not seg1_vo.endswith("…")
    assert 10 <= len(seg1_vo) <= 40
    assert validate_remix_plan(normalized) is None


def test_normalize_plan_omits_voiceover_text_by_default():
    planner = _planner()
    profiles = [
        VideoProfile(
            video_id="video-a",
            video_path="/tmp/video-a.mp4",
            duration_seconds=2.0,
            shots=[
                _shot(video_id="video-a", shot_idx=0, start=0.0, end=2.0, emotion="warm", quality=8.0, desc="产品开场特写"),
            ],
        ),
        VideoProfile(
            video_id="video-b",
            video_path="/tmp/video-b.mp4",
            duration_seconds=2.0,
            shots=[
                _shot(video_id="video-b", shot_idx=0, start=0.0, end=2.0, emotion="calm", quality=8.0, desc="用户使用场景"),
            ],
        ),
    ]

    normalized = planner._normalize_plan(
        {
            "title": "silent plan",
            "concept": "silent plan",
            "segments": [
                {
                    "source_video_id": "video-a",
                    "source_shot_idx": 0,
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "voiceover": "不应默认出现。",
                },
                {
                    "source_video_id": "video-b",
                    "source_shot_idx": 0,
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                },
            ],
            "audio_design": {"strategy": "silent"},
        },
        profiles,
        {"duration_seconds": 4.0, "remix_config": {"target_duration_seconds": 4.0}},
    )

    assert all("voiceover" not in segment for segment in normalized["segments"])
    assert validate_remix_plan(normalized) is None


def test_normalize_plan_uses_voiceover_no_audio_fallback_for_legacy_remix_config():
    planner = _planner()
    profiles = [
        VideoProfile(
            video_id="video-a",
            video_path="/tmp/video-a.mp4",
            duration_seconds=2.0,
            shots=[
                _shot(video_id="video-a", shot_idx=0, start=0.0, end=2.0, emotion="warm", quality=8.0, desc="入口环境特写"),
            ],
        ),
        VideoProfile(
            video_id="video-b",
            video_path="/tmp/video-b.mp4",
            duration_seconds=2.0,
            shots=[
                _shot(video_id="video-b", shot_idx=0, start=0.0, end=2.0, emotion="calm", quality=8.0, desc="服务流程展示"),
            ],
        ),
    ]

    normalized = planner._normalize_plan(
        {
            "title": "legacy voiceover plan",
            "concept": "legacy voiceover plan",
            "segments": [
                {"source_video_id": "video-a", "source_shot_idx": 0, "start_seconds": 0.0, "end_seconds": 2.0},
                {"source_video_id": "video-b", "source_shot_idx": 0, "start_seconds": 0.0, "end_seconds": 2.0},
            ],
            "audio_design": {"strategy": "silent"},
        },
        profiles,
        {"duration_seconds": 4.0, "voiceover_no_audio": False},
    )

    # Voiceover should be complete sentences, NOT copies of description
    seg0_vo = normalized["segments"][0]["voiceover"]
    seg1_vo = normalized["segments"][1]["voiceover"]
    assert seg0_vo != "入口环境特写"
    assert seg1_vo != "服务流程展示"
    assert seg0_vo.endswith(("。", "！", "？"))
    assert seg1_vo.endswith(("。", "！", "？"))
    assert not seg0_vo.endswith("…")
    assert not seg1_vo.endswith("…")
    assert validate_remix_plan(normalized) is None


def test_remix_audio_decision_uses_voiceover_no_audio_fallback_for_legacy_config():
    support = PipelineExecutorSupportMixin()

    assert support.should_run_remix_audio({"voiceover_no_audio": False}) is True
    assert support.should_run_remix_audio({"voiceover_no_audio": True}) is False
    assert support.should_run_remix_audio({"remix_config": {"add_voiceover": False}, "voiceover_no_audio": False}) is False


def test_align_remix_plan_compresses_segments_to_audio_duration():
    support = PipelineExecutorSupportMixin()
    artifacts = {
        "remix_plan": {
            "target_duration_seconds": 4.0,
            "segments": [
                {"segment_idx": 0, "source_video_id": "video-a", "start_seconds": 0.0, "end_seconds": 2.0},
                {"segment_idx": 1, "source_video_id": "video-b", "start_seconds": 0.0, "end_seconds": 2.0},
            ],
        },
        "remix_planner": {
            "video_profiles": [
                {"video_id": "video-a", "duration_seconds": 4.0},
                {"video_id": "video-b", "duration_seconds": 4.0},
            ],
        },
    }

    result = support.align_remix_plan_to_audio(artifacts, 2.0)

    assert result["changed"] is True
    plan = result["remix_plan"]
    assert plan["target_duration_seconds"] == pytest.approx(2.0)
    assert [segment["end_seconds"] - segment["start_seconds"] for segment in plan["segments"]] == [1.0, 1.0]
    assert plan["timing_adjustment"]["strategy"] == "compressed"


def test_align_remix_plan_extends_segments_without_exceeding_source_duration():
    support = PipelineExecutorSupportMixin()
    artifacts = {
        "remix_plan": {
            "target_duration_seconds": 4.0,
            "segments": [
                {"segment_idx": 0, "source_video_id": "video-a", "start_seconds": 0.0, "end_seconds": 2.0},
                {"segment_idx": 1, "source_video_id": "video-b", "start_seconds": 0.0, "end_seconds": 2.0},
            ],
        },
        "remix_planner": {
            "video_profiles": [
                {"video_id": "video-a", "duration_seconds": 3.0},
                {"video_id": "video-b", "duration_seconds": 5.0},
            ],
        },
    }

    # Video-priority strategy: target is capped at voiceover_cap (4.8s),
    # not the full 6.0s audio duration.
    result = support.align_remix_plan_to_audio(artifacts, 6.0)

    assert result["changed"] is True
    plan = result["remix_plan"]
    # Cap = min(4.0 * 1.2, 4.0 + 6.0) = 4.8
    assert plan["target_duration_seconds"] == pytest.approx(4.8)
    assert plan["timing_adjustment"]["strategy"] == "extended"


def test_align_remix_plan_requests_replan_when_material_insufficient_for_cap():
    """Video-priority: replan only when source material can't even cover the cap."""
    support = PipelineExecutorSupportMixin()
    artifacts = {
        "remix_plan": {
            "target_duration_seconds": 4.0,
            "segments": [
                {"segment_idx": 0, "source_video_id": "video-a", "start_seconds": 0.0, "end_seconds": 2.0},
                {"segment_idx": 1, "source_video_id": "video-b", "start_seconds": 0.0, "end_seconds": 2.0},
            ],
        },
        "remix_planner": {
            "video_profiles": [
                {"video_id": "video-a", "duration_seconds": 1.5},
                {"video_id": "video-b", "duration_seconds": 1.5},
            ],
        },
    }

    # Cap = min(4.0 * 1.2, 4.0 + 6.0) = 4.8
    # But max material = 1.5 + 1.5 = 3.0, which is < cap → replan needed
    result = support.align_remix_plan_to_audio(artifacts, 10.0)

    assert result["requires_replan"] is True
    assert result["voiceover_cap_seconds"] == pytest.approx(4.8)


@pytest.mark.asyncio
async def test_remix_assembler_uses_png_overlay_instead_of_subtitles_filter(tmp_path: Path, monkeypatch):
    from app.agents.stages import remix_assembler as remix_assembler_module

    input_path = tmp_path / "input.mp4"
    audio_path = tmp_path / "voice.mp3"
    subtitle_path = tmp_path / "subtitles.srt"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    audio_path.write_bytes(b"audio")
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n字幕一\n\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []

    async def _fake_run_subprocess(*args):
        calls.append(tuple(str(arg) for arg in args))
        if args[0] == "ffprobe" and "-select_streams" in args:
            return 1, "", ""
        if args[0] == "ffprobe":
            return 0, "5.0\n", ""
        if "-f" in args and "null" in args:
            return 0, "", "Stream #0:0: Video: h264, yuv420p, 540x960"
        return 0, "", ""

    monkeypatch.setattr(remix_assembler_module, "run_subprocess", _fake_run_subprocess)
    assembler = RemixAssemblerAgent(clip_extractor=object(), ffmpeg_bin="ffmpeg")

    result = await assembler._apply_voiceover_subtitles(
        str(input_path),
        str(output_path),
        {"add_voiceover": True},
        [],
        {"audio_path": str(audio_path), "subtitle_path": str(subtitle_path)},
    )

    assert result == str(output_path)
    final_call = calls[-1]
    assert "subtitles=" not in " ".join(final_call)
    assert "-filter_complex" in final_call
    filter_complex = final_call[final_call.index("-filter_complex") + 1]
    assert "overlay=0:0" in filter_complex
    assert "[vout]" in final_call
    assert "1:a:0" in final_call


@pytest.mark.asyncio
async def test_remix_assembler_png_overlay_mixes_existing_audio(tmp_path: Path, monkeypatch):
    from app.agents.stages import remix_assembler as remix_assembler_module

    input_path = tmp_path / "input.mp4"
    audio_path = tmp_path / "voice.mp3"
    subtitle_path = tmp_path / "subtitles.srt"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    audio_path.write_bytes(b"audio")
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n字幕一\n\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []

    async def _fake_run_subprocess(*args):
        calls.append(tuple(str(arg) for arg in args))
        if args[0] == "ffprobe" and "-select_streams" in args:
            return 0, "0\n", ""
        if args[0] == "ffprobe":
            return 0, "5.0\n", ""
        if "-f" in args and "null" in args:
            return 0, "", "Stream #0:0: Video: h264, yuv420p, 540x960"
        return 0, "", ""

    monkeypatch.setattr(remix_assembler_module, "run_subprocess", _fake_run_subprocess)
    assembler = RemixAssemblerAgent(clip_extractor=object(), ffmpeg_bin="ffmpeg")

    await assembler._apply_voiceover_subtitles(
        str(input_path),
        str(output_path),
        {"add_voiceover": True},
        [],
        {"audio_path": str(audio_path), "subtitle_path": str(subtitle_path)},
    )

    final_call = calls[-1]
    filter_complex = final_call[final_call.index("-filter_complex") + 1]
    assert "overlay=0:0" in filter_complex
    assert "amix=inputs=2" in filter_complex
    assert "[aout]" in final_call


def test_audio_design_preserves_uploaded_bgm_context():
    planner = _planner()
    design = planner._audio_design(
        {},
        {
            "remix_config": {
                "bgm_material_id": "bgm-1",
                "bgm_mood": "cinematic",
                "bgm_volume": 0.2,
                "include_source_audio": False,
            },
            "_resolved_bgm_context": {
                "bgm_source": "uploaded",
                "bgm_material_id": "bgm-1",
                "bgm_path": "/tmp/bgm.mp3",
                "bgm_filename": "bgm.mp3",
                "bgm_duration_seconds": 8.0,
            },
        },
    )

    assert design["strategy"] == "bgm_only"
    assert design["bgm_source"] == "uploaded"
    assert design["bgm_material_id"] == "bgm-1"
    assert design["bgm_path"] == "/tmp/bgm.mp3"
    assert design["bgm_volume"] == pytest.approx(0.2)


def test_audio_design_forces_uploaded_bgm_over_llm_strategy():
    planner = _planner()

    design = planner._audio_design(
        {"audio_design": {"strategy": "source_audio"}},
        {
            "remix_config": {
                "bgm_material_id": "bgm-1",
                "bgm_mood": "cinematic",
                "include_source_audio": False,
            },
            "_resolved_bgm_context": {
                "bgm_source": "uploaded",
                "bgm_material_id": "bgm-1",
                "bgm_path": "/tmp/bgm.mp3",
            },
        },
    )

    assert design["strategy"] == "bgm_only"


def test_audio_design_uses_mix_only_when_source_audio_explicitly_enabled():
    planner = _planner()

    design = planner._audio_design(
        {"audio_design": {"strategy": "silent"}},
        {
            "remix_config": {
                "bgm_material_id": "bgm-1",
                "bgm_mood": "cinematic",
                "include_source_audio": True,
            },
            "_resolved_bgm_context": {
                "bgm_source": "uploaded",
                "bgm_material_id": "bgm-1",
                "bgm_path": "/tmp/bgm.mp3",
            },
        },
    )

    assert design["strategy"] == "mix"


def test_remix_assembler_forces_uploaded_bgm_strategy_before_render():
    assembler = RemixAssemblerAgent(clip_extractor=object())

    default_strategy = assembler._effective_audio_strategy(
        {"strategy": "source_audio"},
        {"remix_config": {"include_source_audio": False}},
    )
    mix_strategy = assembler._effective_audio_strategy(
        {"strategy": "silent"},
        {"remix_config": {"include_source_audio": True}},
    )
    uploaded_strategy = assembler._effective_audio_strategy(
        {"strategy": "source_audio", "bgm_source": "uploaded", "bgm_material_id": "bgm-1"},
        {"remix_config": {"include_source_audio": False}},
    )
    uploaded_mix_strategy = assembler._effective_audio_strategy(
        {"strategy": "silent", "bgm_source": "uploaded", "bgm_material_id": "bgm-1"},
        {"remix_config": {"include_source_audio": True}},
    )
    config_strategy = assembler._effective_audio_strategy(
        {"strategy": "source_audio"},
        {"remix_config": {"bgm_material_id": "bgm-1", "include_source_audio": False}},
    )

    assert default_strategy == "source_audio"
    assert mix_strategy == "silent"
    assert uploaded_strategy == "bgm_only"
    assert uploaded_mix_strategy == "mix"
    assert config_strategy == "bgm_only"


def test_normalize_plan_adjusts_target_when_material_is_insufficient():
    planner = _planner()
    profiles = [
        VideoProfile(
            video_id="video-a",
            video_path="/tmp/video-a.mp4",
            duration_seconds=4.0,
            shots=[
                _shot(video_id="video-a", shot_idx=0, start=0.0, end=2.0, emotion="calm", quality=8.0, desc="a0"),
                _shot(video_id="video-a", shot_idx=1, start=2.0, end=4.0, emotion="warm", quality=8.0, desc="a1"),
            ],
        ),
        VideoProfile(
            video_id="video-b",
            video_path="/tmp/video-b.mp4",
            duration_seconds=2.0,
            shots=[
                _shot(video_id="video-b", shot_idx=0, start=0.0, end=2.0, emotion="neutral", quality=8.0, desc="b0"),
            ],
        ),
    ]

    normalized = planner._normalize_plan(
        {"title": "fallback", "concept": "fallback", "segments": []},
        profiles,
        {"duration_seconds": 30.0, "remix_config": {"target_duration_seconds": 30.0}},
    )

    assert normalized["target_duration_seconds"] == pytest.approx(6.0)
    assert normalized["requires_more_material"] is True
    assert validate_remix_plan(normalized) is None


def test_validate_remix_plan_rejects_invalid_segments():
    error = validate_remix_plan(
        {
            "target_duration_seconds": 30.0,
            "segments": [
                {
                    "segment_idx": 0,
                    "source_video_id": "video-a",
                    "source_shot_idx": 0,
                    "start_seconds": 1.0,
                    "end_seconds": 1.0,
                    "transition_to_next": "cut",
                    "transition_duration": 0.0,
                }
            ],
        }
    )
    assert error is not None


def test_remix_assembler_reads_nested_voiceover_config():
    assembler = RemixAssemblerAgent(clip_extractor=object())

    config = assembler._voiceover_config(
        {
            "voice_id": "narrator",
            "remix_config": {
                "add_voiceover": True,
                "voiceover_script": "逐步介绍亮点。",
            },
        }
    )

    assert config["add_voiceover"] is True
    assert config["voiceover_script"] == "逐步介绍亮点。"
    assert config["voice_id"] == "narrator"


@pytest.mark.asyncio
async def test_remix_assembler_rejects_invalid_plan_before_render():
    class _ClipExtractor:
        def __init__(self):
            self.called = False

        async def extract_clip(self, *args, **kwargs):
            self.called = True
            raise AssertionError("extract_clip should not be called for invalid plan")

    class _Context:
        db_session_factory = None

        async def report_progress(self, *_args, **_kwargs):
            return None

    extractor = _ClipExtractor()
    assembler = RemixAssemblerAgent(clip_extractor=extractor)
    result = await assembler.execute(
        _Context(),
        {
            "remix_plan": {
                "target_duration_seconds": 10.0,
                "segments": [
                    {
                        "segment_idx": 0,
                        "source_video_id": "video-a",
                        "source_shot_idx": 0,
                        "start_seconds": 1.0,
                        "end_seconds": 1.0,
                        "transition_to_next": "cut",
                        "transition_duration": 0.0,
                    }
                ],
            }
        },
    )
    assert result.success is False
    assert "校验失败" in (result.error or "")
    assert extractor.called is False
