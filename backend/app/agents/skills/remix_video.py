from __future__ import annotations

from app.agents.skills.loader import load_runtime_skill_factory, load_runtime_skill_spec

REMIX_VIDEO_SKILL = load_runtime_skill_spec("remix_video")
REMIX_VIDEO_DESCRIPTION = REMIX_VIDEO_SKILL.description
REMIX_VIDEO_INPUT_SCHEMA = REMIX_VIDEO_SKILL.input_schema


def create_remix_video_skill(*args, **kwargs):
    factory = load_runtime_skill_factory("remix_video")
    return factory(*args, **kwargs)
