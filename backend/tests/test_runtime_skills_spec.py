from __future__ import annotations

from app.agents.skills import (
    ANALYZE_VIDEO_INPUT_SCHEMA,
    ANALYZE_VIDEO_SKILL,
    GENERATE_VIDEO_INPUT_SCHEMA,
    GENERATE_VIDEO_SKILL,
    REMIX_VIDEO_INPUT_SCHEMA,
    REMIX_VIDEO_SKILL,
    REPLICATE_VIDEO_INPUT_SCHEMA,
    REPLICATE_VIDEO_SKILL,
)


async def _noop_tool(**kwargs):
    return kwargs


def test_runtime_skill_specs_expose_required_metadata():
    for skill in (ANALYZE_VIDEO_SKILL, GENERATE_VIDEO_SKILL, REMIX_VIDEO_SKILL, REPLICATE_VIDEO_SKILL):
        metadata = skill.metadata()
        assert metadata["skill_name"]
        assert metadata["name"]
        assert metadata["description"]
        assert metadata["use_when"]
        assert metadata["do_not_use_when"]
        assert metadata["required_inputs"]
        assert metadata["validation_rules"]
        assert metadata["routing_hints"]
        assert metadata["context"] == "direct"
        assert metadata["user_invocable"] is False
        assert metadata["instructions"]
        assert metadata["supporting_files"]


def test_runtime_skill_specs_convert_to_tool_definitions():
    analyze_tool = ANALYZE_VIDEO_SKILL.to_tool_definition(_noop_tool)
    generate_tool = GENERATE_VIDEO_SKILL.to_tool_definition(_noop_tool)
    remix_tool = REMIX_VIDEO_SKILL.to_tool_definition(_noop_tool)
    replicate_tool = REPLICATE_VIDEO_SKILL.to_tool_definition(_noop_tool)

    assert analyze_tool.name == "analyze_video"
    assert analyze_tool.input_schema == ANALYZE_VIDEO_INPUT_SCHEMA
    assert analyze_tool.required_permission == "analyze_video"

    assert generate_tool.name == "generate_video"
    assert generate_tool.input_schema == GENERATE_VIDEO_INPUT_SCHEMA
    assert generate_tool.required_permission == "generate_video"

    assert remix_tool.name == "remix_video"
    assert remix_tool.input_schema == REMIX_VIDEO_INPUT_SCHEMA
    assert remix_tool.required_permission == "remix_video"

    assert replicate_tool.name == "replicate_video"
    assert replicate_tool.input_schema == REPLICATE_VIDEO_INPUT_SCHEMA
    assert replicate_tool.required_permission == "replicate_video"
