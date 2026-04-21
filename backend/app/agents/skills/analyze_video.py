from __future__ import annotations

from app.agents.skills.loader import load_runtime_skill_factory, load_runtime_skill_spec

ANALYZE_VIDEO_SKILL = load_runtime_skill_spec("analyze_video")
ANALYZE_VIDEO_DESCRIPTION = ANALYZE_VIDEO_SKILL.description
ANALYZE_VIDEO_INPUT_SCHEMA = ANALYZE_VIDEO_SKILL.input_schema


def create_analyze_video_skill(*args, **kwargs):
    factory = load_runtime_skill_factory("analyze_video")
    return factory(*args, **kwargs)
