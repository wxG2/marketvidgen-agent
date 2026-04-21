from __future__ import annotations

from app.agents.skills.loader import load_runtime_skill_factory, load_runtime_skill_spec

GENERATE_VIDEO_SKILL = load_runtime_skill_spec("generate_video")
GENERATE_VIDEO_DESCRIPTION = GENERATE_VIDEO_SKILL.description
GENERATE_VIDEO_INPUT_SCHEMA = GENERATE_VIDEO_SKILL.input_schema


def create_generate_video_skill(*args, **kwargs):
    factory = load_runtime_skill_factory("generate_video")
    return factory(*args, **kwargs)
