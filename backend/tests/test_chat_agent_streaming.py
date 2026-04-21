from __future__ import annotations

import pytest

from app.agents.chat.agent import ChatAgent
from app.agents.core.tool_registry import ToolDefinition, ToolRegistry


class _StreamingLLM:
    def __init__(self) -> None:
        self.client = object()
        self.chat_stream_calls = 0
        self.generate_structured_calls = 0
        self.generate_with_tools_calls = 0
        self.stream_messages: list[dict] | None = None

    async def chat_stream(self, messages: list[dict]):
        self.chat_stream_calls += 1
        self.stream_messages = messages
        for chunk in ["你", "好"]:
            yield chunk

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        image_paths=None,
        video_paths=None,
    ):
        self.generate_structured_calls += 1
        return {}, {"total_tokens": 0}

    async def generate_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        tools: list[dict],
        tool_executor,
        image_paths=None,
        video_paths=None,
    ):
        self.generate_with_tools_calls += 1
        return {"reply": "不应该走到工具链"}, [], {"total_tokens": 0}


class _ToolModeLLM(_StreamingLLM):
    async def chat_stream(self, messages: list[dict]):
        self.chat_stream_calls += 1
        self.stream_messages = messages
        for chunk in ["不", "应", "走", "真", "流"]:
            yield chunk

    async def generate_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        tools: list[dict],
        tool_executor,
        image_paths=None,
        video_paths=None,
    ):
        self.generate_with_tools_calls += 1
        return {"reply": "已记录，准备生成"}, [], {"total_tokens": 12}

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        image_paths=None,
        video_paths=None,
    ):
        self.generate_structured_calls += 1
        return {
            "user_request": "帮我生成这个视频",
            "platform": "douyin",
            "style": "vlog",
        }, {"total_tokens": 8}


class _StoryboardSkillLLM(_StreamingLLM):
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        image_paths=None,
        video_paths=None,
    ):
        self.generate_structured_calls += 1
        return {
            "brief": "请按这个参考视频给我出一个分镜故事板",
        }, {"total_tokens": 6}


@pytest.mark.asyncio
async def test_chat_agent_streams_plain_conversation_from_llm():
    llm = _StreamingLLM()
    agent = ChatAgent(llm=llm, tool_registry=ToolRegistry())

    messages = [{"role": "user", "content": "你好，帮我想一句开场白"}]
    session_context = {
        "project_id": "project-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "platform": "generic",
        "duration_mode": "fixed",
        "selected_materials": [],
    }

    events = [event async for event in agent.chat_stream(messages, session_context)]

    assert events[0].type == "status"
    assert "已收到" in events[0].content
    assert [event.content for event in events if event.type == "text"] == ["你", "好"]
    assert events[-1].type == "done"
    assert llm.chat_stream_calls == 1
    assert llm.generate_structured_calls == 0
    assert llm.generate_with_tools_calls == 0
    assert llm.stream_messages is not None
    assert llm.stream_messages[0]["role"] == "system"
    assert llm.stream_messages[-1] == {"role": "user", "content": "你好，帮我想一句开场白"}


@pytest.mark.asyncio
async def test_chat_agent_keeps_tool_mode_for_explicit_generate_intent():
    llm = _ToolModeLLM()
    tool_registry = ToolRegistry()

    async def _fake_generate_video(**kwargs):
        return {
            "status": "started",
            "echo": kwargs,
        }

    tool_registry.register(
        ToolDefinition(
            name="generate_video",
            description="启动视频生成流程。",
            input_schema={
                "type": "object",
                "properties": {
                    "user_request": {"type": "string"},
                    "platform": {"type": "string"},
                    "style": {"type": "string"},
                },
                "required": ["user_request"],
            },
            fn=_fake_generate_video,
            required_permission="generate_video",
            metadata={
                "routing_hints": ["生成", "制作", "开始"],
                "required_inputs": ["project_id", "session_id", "user_id", "image_ids"],
                "use_when": ["用户明确要求开始生成视频。"],
            },
        )
    )
    tool_registry.grant_permission("chat_agent", "generate_video")
    agent = ChatAgent(llm=llm, tool_registry=tool_registry)

    messages = [{"role": "user", "content": "开始生成这个视频"}]
    session_context = {
        "project_id": "project-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "platform": "generic",
        "duration_mode": "fixed",
        "selected_materials": [{"material_id": "material-1"}],
    }

    events = [event async for event in agent.chat_stream(messages, session_context)]

    assert llm.chat_stream_calls == 0
    assert llm.generate_structured_calls == 0
    assert llm.generate_with_tools_calls == 0
    assert events[0].type == "status"
    assert any(event.type == "status" and "直接启动视频流水线" in event.content for event in events)
    tool_call = next(event for event in events if event.type == "tool_call")
    tool_result = next(event for event in events if event.type == "tool_result")
    assert events[-1].type == "done"
    assert tool_call.tool_name == "generate_video"
    assert tool_call.tool_args == {"user_request": "开始生成这个视频"}
    assert tool_result.tool_result["status"] == "started"
    assert tool_result.tool_result["echo"]["user_request"] == "开始生成这个视频"


@pytest.mark.asyncio
async def test_chat_agent_keeps_design_plan_request_in_plain_chat():
    llm = _StreamingLLM()
    tool_registry = ToolRegistry()

    async def _fake_generate_video(**kwargs):
        return {
            "status": "started",
            "echo": kwargs,
        }

    tool_registry.register(
        ToolDefinition(
            name="generate_video",
            description="启动视频生成流程。",
            input_schema={
                "type": "object",
                "properties": {
                    "user_request": {"type": "string"},
                },
                "required": ["user_request"],
            },
            fn=_fake_generate_video,
            required_permission="generate_video",
            metadata={
                "routing_hints": ["生成", "制作", "设计方案", "营销视频"],
                "required_inputs": ["project_id", "session_id", "user_id", "image_ids"],
                "use_when": ["用户明确要求开始生成视频。"],
                "do_not_use_when": ["用户只是在聊创意、修改脚本、询问方案，不希望立即开跑 pipeline。"],
            },
        )
    )
    tool_registry.grant_permission("chat_agent", "generate_video")
    agent = ChatAgent(llm=llm, tool_registry=tool_registry)

    messages = [{"role": "user", "content": "生成新的营销视频设计方案"}]
    session_context = {
        "project_id": "project-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "platform": "generic",
        "duration_mode": "fixed",
        "selected_materials": [{"material_id": "material-1"}],
    }

    events = [event async for event in agent.chat_stream(messages, session_context)]

    assert llm.chat_stream_calls == 1
    assert llm.generate_structured_calls == 0
    assert llm.generate_with_tools_calls == 0
    assert not any(event.type == "tool_call" for event in events)
    assert [event.content for event in events if event.type == "text"] == ["你", "好"]


@pytest.mark.asyncio
async def test_chat_agent_directly_launches_video_generation_request_with_selected_images():
    llm = _StreamingLLM()
    tool_registry = ToolRegistry()

    async def _fake_generate_video(**kwargs):
        return {
            "status": "started",
            "echo": kwargs,
        }

    tool_registry.register(
        ToolDefinition(
            name="generate_video",
            description="启动视频生成流程。",
            input_schema={
                "type": "object",
                "properties": {
                    "user_request": {"type": "string"},
                },
                "required": ["user_request"],
            },
            fn=_fake_generate_video,
            required_permission="generate_video",
            metadata={
                "routing_hints": ["生成视频"],
                "required_inputs": ["project_id", "session_id", "user_id", "image_ids"],
                "use_when": ["用户明确要求开始生成视频。"],
            },
        )
    )
    tool_registry.grant_permission("chat_agent", "generate_video")
    agent = ChatAgent(llm=llm, tool_registry=tool_registry)

    messages = [{"role": "user", "content": "帮我生成一个10s的营销视频"}]
    session_context = {
        "project_id": "project-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "platform": "generic",
        "duration_mode": "fixed",
        "selected_materials": [{"material_id": "material-1"}],
    }

    events = [event async for event in agent.chat_stream(messages, session_context)]

    assert llm.chat_stream_calls == 0
    assert llm.generate_structured_calls == 0
    tool_call = next(event for event in events if event.type == "tool_call")
    tool_result = next(event for event in events if event.type == "tool_result")
    assert tool_call.tool_name == "generate_video"
    assert tool_call.tool_args == {"user_request": "帮我生成一个10s的营销视频"}
    assert tool_result.tool_result["echo"]["user_request"] == "帮我生成一个10s的营销视频"


@pytest.mark.asyncio
async def test_chat_agent_lists_registered_runtime_skills_without_calling_llm():
    llm = _StreamingLLM()
    tool_registry = ToolRegistry()

    async def _noop_skill(**kwargs):
        return {"status": "ok"}

    tool_registry.register(
        ToolDefinition(
            name="analyze_video",
            description="分析参考视频并输出文字报告。",
            input_schema={"type": "object", "properties": {}},
            fn=_noop_skill,
            required_permission="analyze_video",
            metadata={
                "use_when": ["用户明确要求分析、拆解、总结参考视频。"],
                "source_path": "/Users/weixiang/agent/vidgen/backend/app/agents/skills/analyze_video.py",
            },
            source_path="/Users/weixiang/agent/vidgen/backend/app/agents/skills/analyze_video.py",
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="generate_video",
            description="启动视频生成流程。",
            input_schema={"type": "object", "properties": {}},
            fn=_noop_skill,
            required_permission="generate_video",
            metadata={
                "use_when": ["用户明确要求开始生成视频。"],
                "source_path": "/Users/weixiang/agent/vidgen/backend/app/agents/skills/generate_video.py",
            },
            source_path="/Users/weixiang/agent/vidgen/backend/app/agents/skills/generate_video.py",
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="replicate_video",
            description="启动参考视频复刻流程。",
            input_schema={"type": "object", "properties": {}},
            fn=_noop_skill,
            required_permission="replicate_video",
            metadata={
                "use_when": ["用户明确要求复刻或模仿参考视频。"],
                "source_path": "/Users/weixiang/agent/vidgen/backend/app/agents/skills/replicate_video.py",
            },
            source_path="/Users/weixiang/agent/vidgen/backend/app/agents/skills/replicate_video.py",
        )
    )
    tool_registry.grant_permission("chat_agent", "analyze_video")
    tool_registry.grant_permission("chat_agent", "generate_video")
    tool_registry.grant_permission("chat_agent", "replicate_video")

    agent = ChatAgent(llm=llm, tool_registry=tool_registry)
    messages = [{"role": "user", "content": "你有哪些skills"}]
    session_context = {
        "project_id": "project-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "platform": "generic",
        "duration_mode": "fixed",
        "selected_materials": [],
        "reference_video_id": None,
    }

    events = [event async for event in agent.chat_stream(messages, session_context)]
    reply = "".join(event.content for event in events if event.type == "text")

    assert llm.chat_stream_calls == 0
    assert llm.generate_structured_calls == 0
    assert llm.generate_with_tools_calls == 0
    assert "3 个技能" in reply
    assert "`analyze_video`" in reply
    assert "`generate_video`" in reply
    assert "`replicate_video`" in reply
    assert "analyze_video.py" in reply
    assert "generate_video.py" in reply
    assert "replicate_video.py" in reply


@pytest.mark.asyncio
async def test_chat_agent_auto_routes_dynamic_runtime_skill_from_metadata():
    llm = _StoryboardSkillLLM()
    tool_registry = ToolRegistry()

    async def _storyboard_plan(
        *,
        project_id: str,
        session_id: str,
        user_id: str,
        reference_video_id: str,
        brief: str,
    ):
        return {
            "status": "started",
            "project_id": project_id,
            "session_id": session_id,
            "user_id": user_id,
            "reference_video_id": reference_video_id,
            "brief": brief,
        }

    tool_registry.register(
        ToolDefinition(
            name="storyboard_plan",
            description="基于当前参考视频生成分镜故事板。",
            input_schema={
                "type": "object",
                "properties": {
                    "brief": {"type": "string"},
                },
                "required": ["brief"],
            },
            fn=_storyboard_plan,
            required_permission="storyboard_plan",
            metadata={
                "routing_hints": ["故事板", "分镜"],
                "required_inputs": ["project_id", "session_id", "user_id", "reference_video_id"],
                "use_when": ["用户要求输出故事板或分镜。"],
                "source_path": "/Users/weixiang/agent/vidgen/backend/app/agents/skills/storyboard_plan.py",
            },
            source_path="/Users/weixiang/agent/vidgen/backend/app/agents/skills/storyboard_plan.py",
        )
    )
    tool_registry.grant_permission("chat_agent", "storyboard_plan")

    agent = ChatAgent(llm=llm, tool_registry=tool_registry)
    messages = [{"role": "user", "content": "请按这个参考视频给我出一个分镜故事板"}]
    session_context = {
        "project_id": "project-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "platform": "generic",
        "duration_mode": "fixed",
        "selected_materials": [],
        "reference_video_id": "video-1",
    }

    events = [event async for event in agent.chat_stream(messages, session_context)]

    assert llm.chat_stream_calls == 0
    assert llm.generate_structured_calls == 1
    assert llm.generate_with_tools_calls == 0
    assert events[0].type == "status"
    assert any(event.type == "status" and "已命中技能 storyboard_plan" in event.content for event in events)
    tool_call = next(event for event in events if event.type == "tool_call")
    tool_result = next(event for event in events if event.type == "tool_result")
    assert events[-1].type == "done"
    assert tool_call.tool_name == "storyboard_plan"
    assert tool_call.tool_args == {"brief": "请按这个参考视频给我出一个分镜故事板"}
    assert tool_result.tool_result["status"] == "started"
    assert tool_result.tool_result["reference_video_id"] == "video-1"
    assert tool_result.tool_result["brief"] == "请按这个参考视频给我出一个分镜故事板"
