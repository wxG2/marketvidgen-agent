from __future__ import annotations

import importlib

from app.agents.skills.spec import RuntimeSkillManifest, RuntimeSkillSpec
from app.agents.skills.loader import (
    RuntimeSkillBinding,
    discover_runtime_skill_bindings,
    discover_runtime_skill_manifests,
    get_runtime_skill_manifest,
    load_runtime_skill_factory,
    load_runtime_skill_spec,
    register_runtime_skills,
)

_LEGACY_EXPORTS = {
    "create_analyze_video_skill": ("app.agents.skills.analyze_video", "create_analyze_video_skill"),
    "create_generate_video_skill": ("app.agents.skills.generate_video", "create_generate_video_skill"),
    "create_remix_video_skill": ("app.agents.skills.remix_video", "create_remix_video_skill"),
    "create_replicate_video_skill": ("app.agents.skills.replicate_video", "create_replicate_video_skill"),
    "ANALYZE_VIDEO_DESCRIPTION": ("app.agents.skills.analyze_video", "ANALYZE_VIDEO_DESCRIPTION"),
    "ANALYZE_VIDEO_INPUT_SCHEMA": ("app.agents.skills.analyze_video", "ANALYZE_VIDEO_INPUT_SCHEMA"),
    "ANALYZE_VIDEO_SKILL": ("app.agents.skills.analyze_video", "ANALYZE_VIDEO_SKILL"),
    "GENERATE_VIDEO_DESCRIPTION": ("app.agents.skills.generate_video", "GENERATE_VIDEO_DESCRIPTION"),
    "GENERATE_VIDEO_INPUT_SCHEMA": ("app.agents.skills.generate_video", "GENERATE_VIDEO_INPUT_SCHEMA"),
    "GENERATE_VIDEO_SKILL": ("app.agents.skills.generate_video", "GENERATE_VIDEO_SKILL"),
    "REMIX_VIDEO_DESCRIPTION": ("app.agents.skills.remix_video", "REMIX_VIDEO_DESCRIPTION"),
    "REMIX_VIDEO_INPUT_SCHEMA": ("app.agents.skills.remix_video", "REMIX_VIDEO_INPUT_SCHEMA"),
    "REMIX_VIDEO_SKILL": ("app.agents.skills.remix_video", "REMIX_VIDEO_SKILL"),
    "REPLICATE_VIDEO_DESCRIPTION": ("app.agents.skills.replicate_video", "REPLICATE_VIDEO_DESCRIPTION"),
    "REPLICATE_VIDEO_INPUT_SCHEMA": ("app.agents.skills.replicate_video", "REPLICATE_VIDEO_INPUT_SCHEMA"),
    "REPLICATE_VIDEO_SKILL": ("app.agents.skills.replicate_video", "REPLICATE_VIDEO_SKILL"),
}


def __getattr__(name: str):
    target = _LEGACY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)

    module_name, attr_name = target
    module = importlib.import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [
    "RuntimeSkillManifest",
    "RuntimeSkillSpec",
    "RuntimeSkillBinding",
    "discover_runtime_skill_bindings",
    "discover_runtime_skill_manifests",
    "get_runtime_skill_manifest",
    "load_runtime_skill_factory",
    "load_runtime_skill_spec",
    "register_runtime_skills",
    "create_analyze_video_skill",
    "create_generate_video_skill",
    "create_remix_video_skill",
    "create_replicate_video_skill",
    "ANALYZE_VIDEO_DESCRIPTION",
    "ANALYZE_VIDEO_INPUT_SCHEMA",
    "ANALYZE_VIDEO_SKILL",
    "GENERATE_VIDEO_DESCRIPTION",
    "GENERATE_VIDEO_INPUT_SCHEMA",
    "GENERATE_VIDEO_SKILL",
    "REMIX_VIDEO_DESCRIPTION",
    "REMIX_VIDEO_INPUT_SCHEMA",
    "REMIX_VIDEO_SKILL",
    "REPLICATE_VIDEO_DESCRIPTION",
    "REPLICATE_VIDEO_INPUT_SCHEMA",
    "REPLICATE_VIDEO_SKILL",
]
