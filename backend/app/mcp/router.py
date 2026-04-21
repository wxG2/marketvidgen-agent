"""FastAPI router that mounts the vidgen MCP server over HTTP/SSE transport.

This allows MCP clients that support HTTP transport to connect to the vidgen
MCP server at /mcp without launching a separate process.

Mount in main.py:
    from app.mcp.router import mcp_router
    app.include_router(mcp_router)
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.mcp.server import mcp, list_materials, get_pipeline_status, search_project_history, list_agent_tools

mcp_router = APIRouter(prefix="/mcp", tags=["MCP"])


@mcp_router.get("/tools")
async def describe_mcp_tools() -> JSONResponse:
    """List all MCP tools exposed by this server (discovery endpoint).

    Returns the tool name, description, and input JSON Schema for each tool.
    Compatible with any MCP client that needs to enumerate available tools.
    """
    tools = [
        {
            "name": "list_materials",
            "description": list_materials.__doc__,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Filter by category"},
                    "media_type": {"type": "string", "description": "image | video | (empty=all)"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
            },
        },
        {
            "name": "get_pipeline_status",
            "description": get_pipeline_status.__doc__,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pipeline_run_id": {"type": "string", "description": "UUID of the pipeline run"},
                },
                "required": ["pipeline_run_id"],
            },
        },
        {
            "name": "search_project_history",
            "description": search_project_history.__doc__,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Search keyword"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["keyword"],
            },
        },
        {
            "name": "list_agent_tools",
            "description": list_agent_tools.__doc__,
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    return JSONResponse({"server": "vidgen", "protocol": "mcp", "tools": tools})
