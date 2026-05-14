from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.agents.core.tool_registry import ToolRegistry
from app.agents.skills import discover_runtime_skill_bindings, register_runtime_skills
from app.agents.skills.loader import load_runtime_skill_factory
from app.models.auto_chat import AutoChatSession
from app.models.pipeline import PipelineRun
from app.models.video_upload import VideoUpload


def test_runtime_skill_loader_discovers_existing_skills():
    bindings = discover_runtime_skill_bindings()

    assert [binding.tool_name for binding in bindings] == [
        "analyze_video",
        "generate_video",
        "remix_video",
        "replicate_video",
    ]
    assert [binding.skill_name for binding in bindings] == [
        "analyze-video",
        "generate-video",
        "remix-video",
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
        agent_name="orchestrator",
    )

    visible_tools = tool_registry.list_tool_definitions(agent_name="orchestrator")
    assert [tool.name for tool in visible_tools] == [
        "analyze_video",
        "generate_video",
        "remix_video",
        "replicate_video",
    ]
    assert [binding.tool_name for binding in bindings] == [
        "analyze_video",
        "generate_video",
        "remix_video",
        "replicate_video",
    ]
    assert all(not tool.is_loaded for tool in visible_tools)
    for binding in bindings:
        tool = tool_registry.get_tool(binding.tool_name)
        assert tool is not None
        assert binding.required_permission is not None
        assert tool_registry.has_permission("orchestrator", binding.required_permission)

    analyze_tool = tool_registry.get_tool("analyze_video")
    assert analyze_tool is not None
    analyze_tool.ensure_loaded()
    assert analyze_tool.is_loaded is True
    assert "instructions" in analyze_tool.metadata
    assert analyze_tool.metadata["skill_name"] == "analyze-video"


class _FakeRemixDb:
    def __init__(self):
        self.session = AutoChatSession(id="session-1", project_id="project-1", user_id="user-1")
        self.videos = {
            "video-1": VideoUpload(
                id="video-1",
                project_id="project-1",
                filename="video-1.mp4",
                file_path="/tmp/video-1.mp4",
                file_size=1,
                mime_type="video/mp4",
            ),
            "video-2": VideoUpload(
                id="video-2",
                project_id="project-1",
                filename="video-2.mp4",
                file_path="/tmp/video-2.mp4",
                file_size=1,
                mime_type="video/mp4",
            ),
        }
        self.run: PipelineRun | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, item_id):
        if model is AutoChatSession:
            return self.session if item_id == self.session.id else None
        if model is VideoUpload:
            return self.videos.get(item_id)
        return None

    def add(self, item):
        if isinstance(item, PipelineRun):
            item.id = "run-1"
            item.trace_id = "trace-1"
            item.retry_count = 0
            item.created_at = datetime.now(timezone.utc)
            item.updated_at = item.created_at
            self.run = item

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, _item):
        return None


@pytest.mark.asyncio
async def test_remix_runtime_populates_voiceover_remix_config(monkeypatch):
    db = _FakeRemixDb()
    launched: dict[str, object] = {}

    def _fake_launch_pipeline_task(_executor, run_id, _project_id, input_config, **_kwargs):
        launched["run_id"] = run_id
        launched["input_config"] = input_config

    factory = load_runtime_skill_factory("remix_video")
    monkeypatch.setitem(factory.__globals__, "launch_pipeline_task", _fake_launch_pipeline_task)
    skill = factory(type("Executor", (), {"engine_name": "test"})(), lambda: db)

    result = await skill(
        project_id="project-1",
        session_id="session-1",
        user_id="user-1",
        reference_video_ids=["video-1", "video-2"],
        voiceover_no_audio=False,
        video_model_no_audio=True,
        bgm_mood="cinematic",
    )

    assert result["run_id"] == "run-1"
    assert launched["run_id"] == "run-1"
    input_config = launched["input_config"]
    assert input_config["remix_config"]["add_voiceover"] is True
    assert input_config["remix_config"]["include_source_audio"] is False
    assert input_config["remix_config"]["bgm_mood"] == "cinematic"
    assert json.loads(db.run.input_config)["remix_config"]["add_voiceover"] is True
