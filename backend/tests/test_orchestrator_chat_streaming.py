from __future__ import annotations

import pytest

from app.agents.core.tool_registry import ToolDefinition, ToolRegistry
from app.agents.stages.orchestrator import OrchestratorAgent


class _StreamingLLM:
    def __init__(self) -> None:
        self.chat_stream_calls = 0
        self.stream_messages: list[dict] | None = None

    async def chat_stream(self, messages: list[dict]):
        self.chat_stream_calls += 1
        self.stream_messages = messages
        for chunk in ["你", "好"]:
            yield chunk


def _base_context(**extra):
    data = {
        "project_id": "project-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "platform": "generic",
        "duration_mode": "fixed",
        "selected_materials": [],
        "reference_video_id": None,
        "reference_video_ids": [],
    }
    data.update(extra)
    return data


def _agent_with_tools(llm: _StreamingLLM, tools: list[ToolDefinition]) -> OrchestratorAgent:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
        if tool.required_permission:
            registry.grant_permission("orchestrator", tool.required_permission)
    agent = OrchestratorAgent(llm_service=llm)
    agent.configure_chat(tool_registry=registry)
    return agent


@pytest.mark.asyncio
async def test_orchestrator_streams_plain_conversation_from_llm():
    llm = _StreamingLLM()
    agent = _agent_with_tools(llm, [])

    events = [
        event async for event in agent.chat_stream(
            [{"role": "user", "content": "你好，帮我想一句开场白"}],
            _base_context(),
        )
    ]

    assert [event.content for event in events if event.type == "text"] == ["你", "好"]
    assert events[-1].type == "done"
    assert llm.chat_stream_calls == 1
    assert llm.stream_messages[0]["role"] == "system"


@pytest.mark.asyncio
async def test_orchestrator_keeps_design_plan_request_in_plain_chat():
    llm = _StreamingLLM()

    async def _fake_generate_video(**kwargs):
        return {"status": "started", "echo": kwargs}

    agent = _agent_with_tools(
        llm,
        [
            ToolDefinition(
                name="generate_video",
                description="启动视频生成流程。",
                fn=_fake_generate_video,
                required_permission="generate_video",
                metadata={
                    "routing_hints": ["生成", "制作", "设计方案", "营销视频"],
                    "required_inputs": ["project_id", "session_id", "user_id", "image_ids"],
                },
            )
        ],
    )

    events = [
        event async for event in agent.chat_stream(
            [{"role": "user", "content": "生成新的营销视频设计方案"}],
            _base_context(selected_materials=[{"material_id": "material-1"}]),
        )
    ]

    assert llm.chat_stream_calls == 1
    assert not any(event.type == "tool_call" for event in events)


@pytest.mark.asyncio
async def test_orchestrator_launches_generate_video_with_selected_images():
    llm = _StreamingLLM()

    async def _fake_generate_video(**kwargs):
        return {"status": "started", "echo": kwargs}

    agent = _agent_with_tools(
        llm,
        [
            ToolDefinition(
                name="generate_video",
                description="启动视频生成流程。",
                fn=_fake_generate_video,
                required_permission="generate_video",
                metadata={
                    "routing_hints": ["生成视频"],
                    "required_inputs": ["project_id", "session_id", "user_id", "image_ids"],
                },
            )
        ],
    )

    events = [
        event async for event in agent.chat_stream(
            [{"role": "user", "content": "帮我生成一个10s的营销视频"}],
            _base_context(selected_materials=[{"material_id": "material-1"}]),
        )
    ]

    assert llm.chat_stream_calls == 0
    tool_call = next(event for event in events if event.type == "tool_call")
    tool_result = next(event for event in events if event.type == "tool_result")
    assert tool_call.tool_name == "generate_video"
    assert tool_result.tool_result["echo"]["user_request"] == "帮我生成一个10s的营销视频"
    assert tool_result.tool_result["echo"]["image_ids"] == ["material-1"]


@pytest.mark.asyncio
async def test_orchestrator_routes_reference_video_analysis_without_pipeline():
    llm = _StreamingLLM()

    async def _fake_analyze_video(**kwargs):
        return {"status": "completed", "analysis_report": f"analysis:{kwargs['reference_video_id']}"}

    agent = _agent_with_tools(
        llm,
        [
            ToolDefinition(
                name="analyze_video",
                description="分析参考视频。",
                fn=_fake_analyze_video,
                required_permission="analyze_video",
                metadata={
                    "routing_hints": ["分析", "拆解", "解析"],
                    "required_inputs": ["project_id", "session_id", "user_id", "reference_video_id"],
                },
            )
        ],
    )

    events = [
        event async for event in agent.chat_stream(
            [{"role": "user", "content": "分析一下这个视频的镜头"}],
            _base_context(reference_video_id="video-1", reference_video_ids=["video-1"]),
        )
    ]

    assert llm.chat_stream_calls == 0
    assert any(event.type == "text" and "analysis:video-1" in event.content for event in events)
    assert next(event for event in events if event.type == "tool_call").tool_name == "analyze_video"


@pytest.mark.asyncio
async def test_orchestrator_routes_single_reference_video_replication():
    llm = _StreamingLLM()

    async def _fake_replicate_video(**kwargs):
        return {"status": "started", "echo": kwargs}

    agent = _agent_with_tools(
        llm,
        [
            ToolDefinition(
                name="replicate_video",
                description="复刻参考视频。",
                fn=_fake_replicate_video,
                required_permission="replicate_video",
                metadata={
                    "routing_hints": ["复刻", "同款", "模仿"],
                    "required_inputs": ["project_id", "session_id", "user_id", "reference_video_id"],
                },
            )
        ],
    )

    events = [
        event async for event in agent.chat_stream(
            [{"role": "user", "content": "照着这个视频做一个同款"}],
            _base_context(reference_video_id="video-1", reference_video_ids=["video-1"]),
        )
    ]

    assert llm.chat_stream_calls == 0
    tool_result = next(event for event in events if event.type == "tool_result")
    assert tool_result.tool_name == "replicate_video"
    assert tool_result.tool_result["echo"]["reference_video_id"] == "video-1"


@pytest.mark.asyncio
async def test_orchestrator_routes_multi_reference_video_remix():
    llm = _StreamingLLM()

    async def _fake_generate_video(**kwargs):
        return {"status": "started", "tool": "generate_video", "echo": kwargs}

    async def _fake_remix_video(**kwargs):
        return {"status": "started", "tool": "remix_video", "echo": kwargs}

    agent = _agent_with_tools(
        llm,
        [
            ToolDefinition(
                name="generate_video",
                description="启动普通视频生成流程。",
                fn=_fake_generate_video,
                required_permission="generate_video",
                metadata={
                    "routing_hints": ["生成视频", "生成成片"],
                    "required_inputs": ["project_id", "session_id", "user_id", "image_ids"],
                },
            ),
            ToolDefinition(
                name="remix_video",
                description="启动多视频混剪流程。",
                fn=_fake_remix_video,
                required_permission="remix_video",
                metadata={
                    "routing_hints": ["混剪", "拼接", "生成成片"],
                    "required_inputs": ["project_id", "session_id", "user_id", "reference_video_ids"],
                },
            ),
        ],
    )

    events = [
        event async for event in agent.chat_stream(
            [{"role": "user", "content": "把这些视频混剪生成成片"}],
            _base_context(
                selected_materials=[{"material_id": "material-1"}],
                reference_video_id="video-1",
                reference_video_ids=["video-1", "video-2"],
            ),
        )
    ]

    assert llm.chat_stream_calls == 0
    tool_call = next(event for event in events if event.type == "tool_call")
    tool_result = next(event for event in events if event.type == "tool_result")
    assert tool_call.tool_name == "remix_video"
    assert tool_result.tool_result["echo"]["reference_video_ids"] == ["video-1", "video-2"]
