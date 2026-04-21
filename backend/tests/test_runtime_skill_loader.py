from __future__ import annotations

from app.agents.core.tool_registry import ToolRegistry
from app.agents.skills import discover_runtime_skill_bindings, register_runtime_skills


def test_runtime_skill_loader_discovers_existing_skills():
    bindings = discover_runtime_skill_bindings()

    assert [binding.tool_name for binding in bindings] == [
        "analyze_video",
        "generate_video",
        "replicate_video",
    ]
    assert [binding.skill_name for binding in bindings] == [
        "analyze-video",
        "generate-video",
        "replicate-video",
    ]


def test_runtime_skill_loader_registers_skills_and_permissions():
    tool_registry = ToolRegistry()

    bindings = register_runtime_skills(
        tool_registry=tool_registry,
        dependency_map={
            "executor": object(),
            "llm_service": object(),
            "db_factory": lambda: None,
            "memory_service": object(),
        },
        agent_name="chat_agent",
    )

    visible_tools = tool_registry.list_tool_definitions(agent_name="chat_agent")
    assert [tool.name for tool in visible_tools] == [
        "analyze_video",
        "generate_video",
        "replicate_video",
    ]
    assert [binding.tool_name for binding in bindings] == [
        "analyze_video",
        "generate_video",
        "replicate_video",
    ]
    assert all(not tool.is_loaded for tool in visible_tools)
    for binding in bindings:
        tool = tool_registry.get_tool(binding.tool_name)
        assert tool is not None
        assert binding.required_permission is not None
        assert tool_registry.has_permission("chat_agent", binding.required_permission)

    analyze_tool = tool_registry.get_tool("analyze_video")
    assert analyze_tool is not None
    analyze_tool.ensure_loaded()
    assert analyze_tool.is_loaded is True
    assert "instructions" in analyze_tool.metadata
    assert analyze_tool.metadata["skill_name"] == "analyze-video"
