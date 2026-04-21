from __future__ import annotations

from app.agents.skills.loader import load_runtime_skill_factory, load_runtime_skill_spec

REPLICATE_VIDEO_SKILL = load_runtime_skill_spec("replicate_video")
REPLICATE_VIDEO_DESCRIPTION = REPLICATE_VIDEO_SKILL.description
REPLICATE_VIDEO_INPUT_SCHEMA = REPLICATE_VIDEO_SKILL.input_schema


def create_replicate_video_skill(*args, **kwargs):
    factory = load_runtime_skill_factory("replicate_video")
    return factory(*args, **kwargs)
