"""Unit tests for ToolRegistry: registration, permissions, discovery, invocation."""
from __future__ import annotations

import pytest

from app.agents.core.tool_registry import ToolDefinition, ToolRegistry


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def simple_tool() -> ToolDefinition:
    async def _fn(query: str) -> str:
        return f"result:{query}"

    return ToolDefinition(
        name="search",
        description="Search for materials",
        fn=_fn,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


@pytest.fixture
def restricted_tool() -> ToolDefinition:
    async def _fn() -> str:
        return "secret"

    return ToolDefinition(
        name="admin_action",
        description="Admin-only tool",
        fn=_fn,
        input_schema={"type": "object", "properties": {}},
        required_permission="admin",
    )


# ── Registration ──────────────────────────────────────────────────────────────

def test_register_and_get(registry: ToolRegistry, simple_tool: ToolDefinition):
    registry.register(simple_tool)
    assert registry.get_tool("search") is simple_tool


def test_get_nonexistent_returns_none(registry: ToolRegistry):
    assert registry.get_tool("no_such_tool") is None


def test_unregister(registry: ToolRegistry, simple_tool: ToolDefinition):
    registry.register(simple_tool)
    registry.unregister("search")
    assert registry.get_tool("search") is None


def test_register_overwrites(registry: ToolRegistry, simple_tool: ToolDefinition):
    registry.register(simple_tool)
    new_tool = ToolDefinition(name="search", description="New version", fn=None)
    registry.register(new_tool)
    assert registry.get_tool("search").description == "New version"


# ── Permissions ───────────────────────────────────────────────────────────────

def test_grant_and_check_permission(registry: ToolRegistry):
    registry.grant_permission("agent_x", "admin")
    assert registry.has_permission("agent_x", "admin") is True


def test_missing_permission_returns_false(registry: ToolRegistry):
    assert registry.has_permission("agent_x", "admin") is False


def test_revoke_permission(registry: ToolRegistry):
    registry.grant_permission("agent_x", "admin")
    registry.revoke_permission("agent_x", "admin")
    assert registry.has_permission("agent_x", "admin") is False


# ── Discovery ─────────────────────────────────────────────────────────────────

def test_list_tools_no_permission_filter(
    registry: ToolRegistry,
    simple_tool: ToolDefinition,
    restricted_tool: ToolDefinition,
):
    registry.register(simple_tool)
    registry.register(restricted_tool)
    # Without agent_name, only unrestricted tools are visible
    tools = registry.list_tools(agent_name=None, fmt="openai")
    names = [t["function"]["name"] for t in tools]
    assert "search" in names
    assert "admin_action" not in names


def test_list_tools_with_permission(
    registry: ToolRegistry,
    simple_tool: ToolDefinition,
    restricted_tool: ToolDefinition,
):
    registry.register(simple_tool)
    registry.register(restricted_tool)
    registry.grant_permission("admin_agent", "admin")
    tools = registry.list_tools(agent_name="admin_agent", fmt="openai")
    names = [t["function"]["name"] for t in tools]
    assert "search" in names
    assert "admin_action" in names


def test_list_tools_without_permission_hides_restricted(
    registry: ToolRegistry,
    simple_tool: ToolDefinition,
    restricted_tool: ToolDefinition,
):
    registry.register(simple_tool)
    registry.register(restricted_tool)
    tools = registry.list_tools(agent_name="normal_agent", fmt="openai")
    names = [t["function"]["name"] for t in tools]
    assert "admin_action" not in names


def test_list_tools_claude_format(registry: ToolRegistry, simple_tool: ToolDefinition):
    registry.register(simple_tool)
    tools = registry.list_tools(fmt="claude")
    assert tools[0]["name"] == "search"
    assert "input_schema" in tools[0]


def test_list_tool_definitions(
    registry: ToolRegistry,
    simple_tool: ToolDefinition,
):
    registry.register(simple_tool)
    defs = registry.list_tool_definitions()
    assert any(d.name == "search" for d in defs)


# ── Format converters ─────────────────────────────────────────────────────────

def test_to_openai_tool_format(simple_tool: ToolDefinition):
    result = simple_tool.to_openai_tool()
    assert result["type"] == "function"
    assert result["function"]["name"] == "search"
    assert "parameters" in result["function"]


def test_to_claude_tool_format(simple_tool: ToolDefinition):
    result = simple_tool.to_claude_tool()
    assert result["name"] == "search"
    assert "input_schema" in result
    assert result["description"] == "Search for materials"


# ── Invocation ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invoke_success(registry: ToolRegistry, simple_tool: ToolDefinition):
    registry.register(simple_tool)
    result = await registry.invoke("search", agent_name="any_agent", query="beach")
    assert result == "result:beach"


@pytest.mark.asyncio
async def test_invoke_nonexistent_raises_key_error(registry: ToolRegistry):
    with pytest.raises(KeyError, match="not found"):
        await registry.invoke("ghost_tool", agent_name="any_agent")


@pytest.mark.asyncio
async def test_invoke_without_permission_raises(
    registry: ToolRegistry,
    restricted_tool: ToolDefinition,
):
    registry.register(restricted_tool)
    with pytest.raises(PermissionError, match="lacks permission"):
        await registry.invoke("admin_action", agent_name="unprivileged_agent")


@pytest.mark.asyncio
async def test_invoke_with_permission_succeeds(
    registry: ToolRegistry,
    restricted_tool: ToolDefinition,
):
    registry.register(restricted_tool)
    registry.grant_permission("admin_agent", "admin")
    result = await registry.invoke("admin_action", agent_name="admin_agent")
    assert result == "secret"
