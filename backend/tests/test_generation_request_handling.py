from __future__ import annotations

import pytest

from app.agents.executors.shared import PipelineExecutorSupportMixin
from app.agents.stages.audio_subtitle import AudioSubtitleAgent
from app.agents.stages.orchestrator import (
    _build_brief_segments,
    _infer_duration_from_text,
    _infer_platform_from_text,
    _infer_style_from_text,
    _looks_like_meta_instruction,
)
from app.agents.stages.prompt_engineer import PromptEngineerAgent
from app.agents.stages.requirement_utils import merge_requirement_fields


class _NeverCallTTS:
    async def synthesize(self, **_kwargs):
        raise AssertionError("TTS should not be called when voiceover_no_audio is true")

    async def generate_subtitles(self, **_kwargs):
        raise AssertionError("subtitles should not be generated when voiceover_no_audio is true")


class _Context:
    async def is_cancelled(self) -> bool:
        return False

    async def report_progress(self, *args, **kwargs) -> None:
        pass


def test_meta_generation_request_is_not_used_as_fallback_shot_text():
    request = "这是我的素材，请根据这些素材，生成一个新的营销视频设计方案"

    assert _looks_like_meta_instruction(request)

    segments = _build_brief_segments(request, 3)

    assert len(segments) == 3
    assert request not in segments


def test_audio_input_preserves_no_audio_flag():
    helper = PipelineExecutorSupportMixin()

    audio_input = helper.build_audio_input(
        {
            "prompt_plan": {
                "shot_prompts": [{"script_segment": "产品开场展示。"}],
                "voice_params": {"voice_id": "Chelsie"},
            },
            "orchestrator_plan": {},
        },
        {"no_audio": True},
    )

    assert audio_input["script"] == "产品开场展示。"
    assert audio_input["no_audio"] is True
    assert audio_input["voiceover_no_audio"] is True


def test_audio_and_video_audio_flags_are_separate():
    helper = PipelineExecutorSupportMixin()

    artifacts = {
        "prompt_plan": {
            "shot_prompts": [
                {
                    "shot_idx": 0,
                    "image_path": "/tmp/frame.jpg",
                    "video_prompt": "slow push in",
                    "duration_seconds": 5,
                }
            ],
            "voice_params": {"voice_id": "Chelsie"},
        },
        "orchestrator_plan": {"source_images": [], "platform": "douyin"},
    }
    input_config = {
        "no_audio": True,
        "video_model_no_audio": False,
        "voiceover_no_audio": True,
        "generation_model": "seedance2.0",
    }

    audio_input = helper.build_audio_input(artifacts, input_config)
    video_input = helper.build_video_input(artifacts, input_config)

    assert audio_input["no_audio"] is True
    assert audio_input["voiceover_no_audio"] is True
    assert video_input["no_audio"] is False
    assert video_input["video_model_no_audio"] is False


def test_orchestrator_intent_helpers_parse_user_message():
    text = "帮我用这些图做一条小红书种草视频，控制在20秒左右"

    assert _infer_platform_from_text(text, "generic") == "xiaohongshu"
    assert _infer_style_from_text(text, "commercial") == "lifestyle"
    assert _infer_duration_from_text(text, 30) == 20


def test_orchestrator_requirement_merge_preserves_generation_controls():
    parsed = merge_requirement_fields(
        {
            "user_request": "帮我用这些图做一条抖音10秒商业视频，BGM不要",
            "image_ids": ["img-1"],
            "generation_model": "seedance2.0",
            "no_audio": False,
            "voice_id": "Cherry",
        },
        {},
    )

    assert parsed["platform"] == "douyin"
    assert parsed["duration_seconds"] == 10
    assert parsed["style"] == "commercial"
    assert parsed["bgm_mood"] == "none"
    assert parsed["generation_model"] == "seedance2.0"
    assert parsed["no_audio"] is False
    assert parsed["video_model_no_audio"] is False
    assert parsed["voiceover_no_audio"] is False
    assert parsed["voice_id"] == "Cherry"


def test_video_input_trusts_director_image_path_when_present():
    helper = PipelineExecutorSupportMixin()

    video_input = helper.build_video_input(
        {
            "orchestrator_plan": {
                "platform": "douyin",
                "source_images": [
                    {
                        "shot_idx": 0,
                        "image_path": "/tmp/direct-from-orchestrator.jpg",
                        "image_content": "产品瓶身特写",
                    }
                ],
            },
            "prompt_plan": {
                "shot_prompts": [
                    {
                        "shot_idx": 0,
                        "image_path": "/tmp/stale-prompt-image.jpg",
                        "video_prompt": "slow push in",
                        "duration_seconds": 5,
                    }
                ]
            },
        },
        {"platform": "generic", "no_audio": True, "generation_model": "seedance1.5-pro"},
    )

    assert video_input["platform"] == "douyin"
    assert video_input["source_images"][0]["image_path"] == "/tmp/direct-from-orchestrator.jpg"
    assert video_input["shot_prompts"][0]["image_path"] == "/tmp/stale-prompt-image.jpg"


def test_video_input_falls_back_to_source_images_when_director_image_missing():
    helper = PipelineExecutorSupportMixin()

    video_input = helper.build_video_input(
        {
            "orchestrator_plan": {
                "platform": "douyin",
                "source_images": [
                    {
                        "image_idx": 0,
                        "image_path": "/tmp/direct-from-orchestrator.jpg",
                        "image_content": "产品瓶身特写",
                    }
                ],
            },
            "prompt_plan": {
                "shot_prompts": [
                    {
                        "shot_idx": 0,
                        "source_image_idx": 0,
                        "video_prompt": "slow push in",
                        "duration_seconds": 5,
                    }
                ]
            },
        },
        {"platform": "generic", "no_audio": True, "generation_model": "seedance1.5-pro"},
    )

    assert video_input["shot_prompts"][0]["image_path"] == "/tmp/direct-from-orchestrator.jpg"
    assert video_input["shot_prompts"][0]["image_content"] == "产品瓶身特写"


def test_prompt_engineer_builds_shots_from_source_images_without_orchestrator_shots():
    agent = PromptEngineerAgent(llm_service=None)

    shot_prompts = agent._build_shot_prompts(
        llm_shots=[],
        source_images=[
            {
                "image_idx": 0,
                "image_path": "/tmp/product.jpg",
                "image_content": "产品瓶身特写",
            }
        ],
        existing_shots=None,
        style="commercial",
        video_type="commercial",
    )

    assert len(shot_prompts) == 1
    assert shot_prompts[0]["image_path"] == "/tmp/product.jpg"
    assert shot_prompts[0]["video_prompt"]


@pytest.mark.asyncio
async def test_audio_subtitle_agent_skips_tts_when_voiceover_no_audio():
    agent = AudioSubtitleAgent(tts_service=_NeverCallTTS())

    result = await agent.execute(
        _Context(),
        {"script": "产品开场展示。", "voice_params": {"voice_id": "Chelsie"}, "no_audio": True},
    )

    assert result.success is True
    assert result.output_data["skipped"] is True
    assert result.output_data["skip_reason"] == "voiceover_no_audio"
    assert result.output_data["audio_path"] == ""
    assert result.output_data["subtitle_path"] == ""
