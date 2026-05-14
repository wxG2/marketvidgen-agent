"""Modular tests for the requirement-parsing layer.

Covers: requirement_utils helpers used by OrchestratorAgent.
No video generation, no database, no external calls.
"""
from __future__ import annotations

import pytest

from app.agents.stages.requirement_utils import (
    fast_infer_bgm,
    fast_infer_duration,
    fast_infer_platform,
    fast_infer_style,
    looks_like_meta_instruction,
    merge_requirement_fields,
    needs_llm_requirement_parsing,
)


# ── fast_infer_platform ───────────────────────────────────────────────────────

def test_infer_douyin_from_chinese():
    assert fast_infer_platform("帮我做一个抖音视频") == "douyin"


def test_infer_xiaohongshu_from_alias():
    assert fast_infer_platform("这是一条小红书种草内容") == "xiaohongshu"


def test_infer_platform_returns_none_when_ambiguous():
    result = fast_infer_platform("做一个视频")
    assert result is None or result == "generic"


# ── fast_infer_style ─────────────────────────────────────────────────────────

def test_infer_lifestyle_from_zhongcao():
    assert fast_infer_style("种草风格的内容") == "lifestyle"


def test_infer_commercial_from_keyword():
    assert fast_infer_style("商业广告风格") == "commercial"


def test_infer_style_none_when_no_match():
    assert fast_infer_style("随便做个视频") is None


# ── fast_infer_duration ───────────────────────────────────────────────────────

def test_infer_duration_seconds():
    assert fast_infer_duration("控制在30秒左右") == 30


def test_infer_duration_minutes():
    assert fast_infer_duration("视频时长1分钟") == 60


def test_infer_duration_returns_none():
    assert fast_infer_duration("随便做个视频") is None


def test_infer_duration_clamped_to_max():
    result = fast_infer_duration("做个1000秒的视频")
    assert result == 300  # clamped to max


# ── fast_infer_bgm ────────────────────────────────────────────────────────────

def test_infer_bgm_none():
    assert fast_infer_bgm("不要bgm") == "none"


def test_infer_bgm_calm():
    assert fast_infer_bgm("舒缓的背景音乐") == "calm"


# ── looks_like_meta_instruction ───────────────────────────────────────────────

def test_meta_instruction_detected():
    assert looks_like_meta_instruction("根据这些图帮我生成营销视频")


def test_meta_instruction_not_detected_for_plain_brief():
    assert not looks_like_meta_instruction("产品卖点：保湿效果好，适合干皮，持久不油腻")


# ── needs_llm_requirement_parsing ─────────────────────────────────────────────

def test_short_message_skips_llm():
    assert not needs_llm_requirement_parsing("做视频")


def test_longer_message_uses_llm():
    assert needs_llm_requirement_parsing("请帮我根据这些素材生成一个15秒的抖音种草视频，突出产品的保湿效果")


# ── merge_requirement_fields ──────────────────────────────────────────────────

def test_merge_preserves_platform_from_fast_inference():
    result = merge_requirement_fields(
        {"user_request": "帮我做一条抖音15秒广告", "image_ids": ["img-1"]},
        {},
    )
    assert result["platform"] == "douyin"
    assert result["duration_seconds"] == 15


def test_merge_voiceover_defaults_to_false():
    """Default voiceover_no_audio must be False (TTS on by default)."""
    result = merge_requirement_fields({"image_ids": ["img-1"]}, {})
    assert result["voiceover_no_audio"] is False


def test_merge_voiceover_respects_explicit_no_audio_true():
    """Legacy no_audio=True should still disable TTS."""
    result = merge_requirement_fields({"no_audio": True, "image_ids": []}, {})
    assert result["voiceover_no_audio"] is True
    assert result["no_audio"] is True


def test_merge_video_model_audio_separate_from_voiceover():
    """video_model_no_audio and voiceover_no_audio are independent."""
    result = merge_requirement_fields(
        {
            "video_model_no_audio": True,   # model audio off
            "voiceover_no_audio": False,    # TTS on
            "image_ids": [],
        },
        {},
    )
    assert result["video_model_no_audio"] is True
    assert result["voiceover_no_audio"] is False


def test_merge_generation_model_forwarded():
    result = merge_requirement_fields(
        {"generation_model": "seedance2.0", "image_ids": []},
        {},
    )
    assert result["generation_model"] == "seedance2.0"


def test_merge_llm_result_overrides_fast_inference():
    result = merge_requirement_fields(
        {"user_request": "帮我做一条小红书视频"},
        {"platform": "douyin", "duration_seconds": 20},
    )
    assert result["platform"] == "douyin"  # LLM wins over fast inference
    assert result["duration_seconds"] == 20
