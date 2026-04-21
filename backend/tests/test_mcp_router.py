"""Tests for the MCP discovery endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_mcp_tools_endpoint_returns_200(client: AsyncClient):
    resp = await client.get("/mcp/tools")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_mcp_tools_response_schema(client: AsyncClient):
    resp = await client.get("/mcp/tools")
    data = resp.json()
    assert data["server"] == "vidgen"
    assert data["protocol"] == "mcp"
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) >= 1


@pytest.mark.asyncio
async def test_mcp_tools_have_required_fields(client: AsyncClient):
    resp = await client.get("/mcp/tools")
    tools = resp.json()["tools"]
    for tool in tools:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool missing 'description': {tool}"
        assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"


@pytest.mark.asyncio
async def test_mcp_tools_include_expected_tools(client: AsyncClient):
    resp = await client.get("/mcp/tools")
    names = {t["name"] for t in resp.json()["tools"]}
    expected = {"list_materials", "get_pipeline_status", "search_project_history", "list_agent_tools"}
    assert expected.issubset(names), f"Missing tools: {expected - names}"
