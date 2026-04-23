"""vidgen MCP Server — exposes pipeline tools via Model Context Protocol.

Tools exposed:
  - list_materials        : List available image/video materials with metadata
  - get_pipeline_status   : Get real-time status and progress of a pipeline run
  - search_project_history: Search past projects by keyword (RAG-style context retrieval)
  - list_agent_tools      : Discover registered vidgen agent tools

Usage (stdio transport, compatible with Claude Desktop):

    python -m app.mcp.server

Add to Claude Desktop config (~/Library/Application Support/Claude/claude_desktop_config.json):

    {
      "mcpServers": {
        "vidgen": {
          "command": "python",
          "args": ["-m", "app.mcp.server"],
          "cwd": "/path/to/vidgen/backend",
          "env": {"DATABASE_URL": "sqlite+aiosqlite:///./data/vidgen.db"}
        }
      }
    }
"""

import json
import os
import sys

# Ensure the backend directory is on the path when run as __main__
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from mcp.server.fastmcp import FastMCP
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.material import Material
from app.models.pipeline import AgentExecution, PipelineRun
from app.models.project import Project

# ── Database setup ────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/vidgen.db")
_engine = create_async_engine(DATABASE_URL, echo=False)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "vidgen",
    instructions=(
        "You are connected to the vidgen AI video generation platform. "
        "Use these tools to list materials, check pipeline progress, search project history, "
        "and discover registered agent tools."
    ),
)


# ── Tool 1: list_materials ────────────────────────────────────────────────────

@mcp.tool()
async def list_materials(
    category: str = "",
    media_type: str = "",
    limit: int = 20,
) -> str:
    """List available materials (images/videos) stored in vidgen.

    Args:
        category: Filter by category name (e.g. "product", "lifestyle"). Empty = all.
        media_type: Filter by type: "image" | "video" | "". Empty = all.
        limit: Max number of results to return (default 20, max 100).

    Returns:
        JSON list of materials with id, filename, category, media_type, tags.
    """
    limit = min(limit, 100)
    async with _session_factory() as session:
        stmt = select(Material)
        if category:
            stmt = stmt.where(Material.category == category)
        if media_type:
            stmt = stmt.where(Material.media_type == media_type)
        stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    result = [
        {
            "id": m.id,
            "filename": m.filename,
            "category": m.category,
            "media_type": m.media_type,
            "tags": m.tags,
            "width": m.width,
            "height": m.height,
            "duration_seconds": m.duration_seconds,
        }
        for m in rows
    ]
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Tool 2: get_pipeline_status ───────────────────────────────────────────────

@mcp.tool()
async def get_pipeline_status(pipeline_run_id: str) -> str:
    """Get real-time status and agent progress for a pipeline run.

    Args:
        pipeline_run_id: The UUID of the pipeline run to inspect.

    Returns:
        JSON object with status, current_agent, agent_executions, error_message,
        final_video_path, created_at, updated_at.
    """
    async with _session_factory() as session:
        run: PipelineRun | None = await session.get(PipelineRun, pipeline_run_id)
        if run is None:
            return json.dumps({"error": f"Pipeline run '{pipeline_run_id}' not found"})

        stmt = (
            select(AgentExecution)
            .where(AgentExecution.pipeline_run_id == pipeline_run_id)
            .order_by(AgentExecution.created_at)
        )
        executions = (await session.execute(stmt)).scalars().all()

    agent_steps = [
        {
            "agent": e.agent_name,
            "status": e.status,
            "attempt": e.attempt_number,
            "duration_ms": e.duration_ms,
            "progress": e.progress_text,
            "error": e.error_message,
        }
        for e in executions
    ]

    return json.dumps(
        {
            "pipeline_run_id": run.id,
            "status": run.status,
            "current_agent": run.current_agent,
            "overall_score": run.overall_score,
            "final_video_path": run.final_video_path,
            "error_message": run.error_message,
            "agent_executions": agent_steps,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        },
        ensure_ascii=False,
        indent=2,
    )


# ── Tool 3: search_project_history ───────────────────────────────────────────

@mcp.tool()
async def search_project_history(
    keyword: str,
    limit: int = 5,
) -> str:
    """Search past video generation projects by keyword for context retrieval.

    Useful for finding similar historical projects and reusing their prompts,
    shot plans, or settings as reference for new generations (RAG-style).

    Args:
        keyword: Search term matched against project names and pipeline input configs.
        limit: Max number of historical results (default 5).

    Returns:
        JSON list of past projects with their input configs and output video paths.
    """
    limit = min(limit, 20)
    async with _session_factory() as session:
        # Search projects by name
        project_stmt = (
            select(Project)
            .where(Project.name.ilike(f"%{keyword}%"))
            .limit(limit)
        )
        projects = (await session.execute(project_stmt)).scalars().all()
        project_ids = {p.id for p in projects}

        # Also search pipeline run input_config (JSON text search)
        run_stmt = (
            select(PipelineRun)
            .where(
                or_(
                    PipelineRun.project_id.in_(project_ids),
                    PipelineRun.input_config.ilike(f"%{keyword}%"),
                )
            )
            .where(PipelineRun.status == "completed")
            .order_by(PipelineRun.created_at.desc())
            .limit(limit)
        )
        runs = (await session.execute(run_stmt)).scalars().all()

    # Build project name lookup
    project_map = {p.id: p.name for p in projects}

    results = []
    for run in runs:
        try:
            input_cfg = json.loads(run.input_config) if run.input_config else {}
        except json.JSONDecodeError:
            input_cfg = {}

        results.append(
            {
                "pipeline_run_id": run.id,
                "project_id": run.project_id,
                "project_name": project_map.get(run.project_id, ""),
                "status": run.status,
                "requirement": input_cfg.get("requirement", ""),
                "platform": input_cfg.get("platform", ""),
                "duration_seconds": input_cfg.get("duration_seconds", ""),
                "final_video_path": run.final_video_path,
                "overall_score": run.overall_score,
                "created_at": run.created_at.isoformat(),
            }
        )

    return json.dumps(results, ensure_ascii=False, indent=2)


# ── Tool 4: list_agent_tools ──────────────────────────────────────────────────

@mcp.tool()
async def list_agent_tools() -> str:
    """List all registered agent tools in the vidgen ToolRegistry.

    Returns the name, description, and input schema for every tool that
    pipeline agents can invoke during video generation.

    Returns:
        JSON list of tool definitions (name, description, input_schema).
    """
    # Import here to avoid circular imports at module level
    from app.agents.tool_registry import build_default_registry  # type: ignore

    try:
        registry = build_default_registry()
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "required_permission": t.required_permission,
                "input_schema": t.input_schema,
            }
            for t in registry.list_tool_definitions()
        ]
        return json.dumps(tools, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "tools": []})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
