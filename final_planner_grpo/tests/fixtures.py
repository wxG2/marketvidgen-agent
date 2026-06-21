"""marketvidgen-agent-shaped verifier fixtures."""

from __future__ import annotations

import json

CANDIDATE_POOL = [
    {
        "video_id": "video-a",
        "shot_idx": 0,
        "start_seconds": 0.0,
        "end_seconds": 4.0,
        "duration_seconds": 4.0,
        "keyframe_path": "",
        "scene_change_score": 1.0,
        "audio_mean_volume": -20.0,
        "audio_max_volume": -8.0,
        "description": "城市街道晨景",
        "emotion_tag": "warm",
        "visual_quality_score": 8.5,
        "scene_tags": ["city", "opening"],
    },
    {
        "video_id": "video-b",
        "shot_idx": 0,
        "start_seconds": 1.0,
        "end_seconds": 5.0,
        "duration_seconds": 4.0,
        "keyframe_path": "",
        "scene_change_score": 1.0,
        "audio_mean_volume": -18.0,
        "audio_max_volume": -6.0,
        "description": "跑者穿过街道",
        "emotion_tag": "neutral",
        "visual_quality_score": 9.0,
        "scene_tags": ["running", "sports"],
    },
    {
        "video_id": "video-a",
        "shot_idx": 1,
        "start_seconds": 4.0,
        "end_seconds": 8.0,
        "duration_seconds": 4.0,
        "keyframe_path": "",
        "scene_change_score": 1.0,
        "audio_mean_volume": -21.0,
        "audio_max_volume": -9.0,
        "description": "城市跑道远景",
        "emotion_tag": "calm",
        "visual_quality_score": 8.0,
        "scene_tags": ["city", "running"],
    },
]

ABSTRACT_SLOTS = [
    {"segment_idx": 0, "type": "intro", "required": True, "expected_order": 0},
    {"segment_idx": 1, "type": "outro", "required": True, "expected_order": 1},
]

USER_CONSTRAINTS = {
    "target_duration_seconds": 8.0,
    "tol": 0.1,
    "must_include_scenes": ["city", "running"],
    "bgm_mood": "energetic",
    "add_voiceover": True,
    "include_source_audio": False,
}

GOOD_PLAN_DICT = {
    "title": "城市跑步混剪",
    "concept": "用城市和运动镜头形成节奏递进",
    "target_duration_seconds": 8.0,
    "source_videos": [
        {"video_id": "video-a", "duration_seconds": 8.0, "total_shots": 2, "analysis_summary": "城市环境"},
        {"video_id": "video-b", "duration_seconds": 5.0, "total_shots": 1, "analysis_summary": "跑步动作"},
    ],
    "segments": [
        {
            "segment_idx": 0,
            "source_video_id": "video-a",
            "source_shot_idx": 0,
            "start_seconds": 0.0,
            "end_seconds": 4.0,
            "description": "城市全景建立空间",
            "emotion_tag": "warm",
            "voiceover": "从城市清晨出发。",
            "role": "intro",
            "quality_score": 8.5,
            "transition_to_next": "fade",
            "transition_duration": 0.25,
            "reference_keyframe_path": "",
        },
        {
            "segment_idx": 1,
            "source_video_id": "video-b",
            "source_shot_idx": 0,
            "start_seconds": 1.0,
            "end_seconds": 5.0,
            "description": "跑步动作完成高潮",
            "emotion_tag": "neutral",
            "voiceover": "每一步都更接近目标。",
            "role": "outro",
            "quality_score": 9.0,
            "transition_to_next": "cut",
            "transition_duration": 0.0,
            "reference_keyframe_path": "",
        },
    ],
    "audio_design": {
        "strategy": "bgm_only",
        "bgm_source": "library",
        "bgm_material_id": None,
        "bgm_path": "",
        "bgm_filename": "",
        "bgm_duration_seconds": None,
        "bgm_mood": "energetic",
        "bgm_volume": 0.25,
        "voice_id": "default",
        "voice_speed": 1.0,
        "voice_tone": "轻松活泼",
        "narration_notes": "短句、节奏清晰",
    },
    "analysis_report": "两个源视频均有贡献，镜头边界与 ShotProfile 一致。",
    "requires_more_material": False,
}

GOOD_PLAN = json.dumps(GOOD_PLAN_DICT, ensure_ascii=False)

HALLUCINATED_PLAN = json.dumps(
    {
        **GOOD_PLAN_DICT,
        "source_videos": [],
        "segments": [
            {
                **GOOD_PLAN_DICT["segments"][0],
                "source_video_id": "not-in-pool",
                "source_shot_idx": 99,
                "transition_to_next": "cut",
                "transition_duration": 0.0,
            }
        ],
        "target_duration_seconds": 4.0,
    },
    ensure_ascii=False,
)

INVALID_JSON = '{"segments": ['

MISSING_REQUIRED_SLOT_PLAN = json.dumps(
    {
        **GOOD_PLAN_DICT,
        "segments": [
            {
                **GOOD_PLAN_DICT["segments"][0],
                "transition_to_next": "cut",
                "transition_duration": 0.0,
            }
        ],
        "target_duration_seconds": 4.0,
    },
    ensure_ascii=False,
)
