from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.agents.core.tool_registry import ToolDefinition, ToolRegistry
from app.prompts import ORCHESTRATOR_CHAT_SYSTEM_PROMPT
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorChatEvent:
    type: str
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: dict[str, Any] = field(default_factory=dict)


class OrchestratorChatCoordinator:
    """Conversation entry for Orchestrator-owned skill routing."""

    agent_name = "orchestrator"

    def __init__(self, llm: LLMService, tool_registry: ToolRegistry, mem0=None):
        self.llm = llm
        self.tool_registry = tool_registry
        self.mem0 = mem0

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
    ) -> AsyncIterator[OrchestratorChatEvent]:
        user_message = str(messages[-1].get("content") or "").strip() if messages else ""
        if not user_message:
            yield OrchestratorChatEvent(type="text", content="你可以先告诉我这次想聊什么，或想让我帮你做什么视频。")
            yield OrchestratorChatEvent(type="done")
            return

        yield OrchestratorChatEvent(type="status", content="Orchestrator 已收到消息，正在判断任务类型。")
        if self._is_skill_inventory_question(user_message):
            for chunk in self._chunk_text(self._build_skill_inventory_reply(session_context)):
                yield OrchestratorChatEvent(type="text", content=chunk)
            yield OrchestratorChatEvent(type="done")
            return

        memories = await self._search_memories(user_message, session_context)
        selected_tool = self._route_runtime_skill(user_message, session_context)
        if selected_tool:
            yield OrchestratorChatEvent(type="status", content=f"Orchestrator 已命中能力 {selected_tool}。")
            async for event in self._execute_tool(selected_tool, user_message, session_context):
                yield event
            return

        yield OrchestratorChatEvent(type="status", content="未命中需要执行的能力，正在调用对话模型。")
        async for event in self._stream_direct_llm_reply(messages, session_context, memories):
            yield event

    async def _search_memories(self, user_message: str, session_context: dict[str, Any]) -> list[dict]:
        user_id = session_context.get("user_id")
        if not self.mem0 or not user_id:
            return []
        try:
            return await self.mem0.search(user_message, user_id, limit=5)
        except Exception:
            return []

    async def _stream_direct_llm_reply(
        self,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        memories: list[dict],
    ) -> AsyncIterator[OrchestratorChatEvent]:
        user_id = session_context.get("user_id")
        session_id = session_context.get("session_id")
        collected: list[str] = []
        llm_messages = [{"role": "system", "content": self._build_system_prompt(session_context, memories)}]
        llm_messages.extend(self._recent_messages(messages))
        next_chunk: asyncio.Task | None = None
        try:
            stream = self.llm.chat_stream(llm_messages)
            while True:
                next_chunk = asyncio.create_task(stream.__anext__())
                while True:
                    try:
                        chunk = await asyncio.wait_for(asyncio.shield(next_chunk), timeout=5)
                        break
                    except asyncio.TimeoutError:
                        yield OrchestratorChatEvent(type="status", content="对话模型仍在生成回复。")
                    except StopAsyncIteration:
                        next_chunk = None
                        break
                if next_chunk is None:
                    break
                if chunk:
                    collected.append(chunk)
                    yield OrchestratorChatEvent(type="text", content=chunk)
        except asyncio.CancelledError:
            if next_chunk is not None:
                next_chunk.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await next_chunk
            raise
        except Exception:
            if collected:
                yield OrchestratorChatEvent(type="text", content="\n\n[网络中断，回复可能不完整，请重试或发送【继续】。]")
            else:
                yield OrchestratorChatEvent(type="text", content="请求处理失败，请稍后重试。")

        full_reply = "".join(collected).strip()
        if self.mem0 and user_id and full_reply:
            with suppress(Exception):
                conversation = [messages[-1], {"role": "assistant", "content": full_reply}]
                asyncio.create_task(self.mem0.add_from_conversation(conversation, user_id, session_id=session_id))
        yield OrchestratorChatEvent(type="done")

    def _route_runtime_skill(self, user_message: str, session_context: dict[str, Any]) -> str | None:
        tools = [
            tool for tool in self.tool_registry.list_tool_definitions(agent_name=self.agent_name)
            if not self._missing_required_inputs(tool, session_context)
        ]
        if not tools:
            return None

        by_name = {tool.name: tool for tool in tools}
        if (
            "remix_video" in by_name
            and len(session_context.get("reference_video_ids") or []) >= 2
            and not self._looks_like_plan_only_video_request(user_message)
            and (self._looks_like_remix_request(user_message) or self._looks_like_video_generation_request(user_message))
        ):
            return "remix_video"

        if (
            "generate_video" in by_name
            and not self._looks_like_plan_only_video_request(user_message)
            and self._looks_like_video_generation_request(user_message)
        ):
            return "generate_video"

        scored: list[tuple[ToolDefinition, int]] = []
        lowered = user_message.lower()
        for tool in tools:
            if tool.name in {"generate_video", "remix_video"} and self._looks_like_plan_only_video_request(user_message):
                continue
            score = 0
            if tool.name in lowered or tool.name.replace("_", "") in lowered:
                score += 6
            for hint in (tool.metadata or {}).get("routing_hints") or []:
                text = str(hint or "").strip()
                if text and (text.lower() in lowered or text in user_message):
                    score += 3 if len(text) > 1 else 1
            if score > 0:
                scored.append((tool, score))
        if not scored:
            return None
        priority = {"remix_video": 0, "replicate_video": 1, "analyze_video": 2, "generate_video": 3}
        scored.sort(key=lambda item: (-item[1], priority.get(item[0].name, 9), item[0].name))
        return scored[0][0].name

    async def _execute_tool(
        self,
        tool_name: str,
        user_message: str,
        session_context: dict[str, Any],
    ) -> AsyncIterator[OrchestratorChatEvent]:
        tool_args = self._fallback_tool_args(tool_name, user_message)
        yield OrchestratorChatEvent(type="tool_call", tool_name=tool_name, tool_args=tool_args)
        yield OrchestratorChatEvent(type="status", content=f"正在执行能力 {tool_name}。")
        invoke_task: asyncio.Task | None = None
        try:
            kwargs = self._build_tool_invocation_kwargs(tool_name, tool_args, session_context)
            invoke_task = asyncio.create_task(
                self.tool_registry.invoke(tool_name, agent_name=self.agent_name, **kwargs)
            )
            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(invoke_task), timeout=5)
                    break
                except asyncio.TimeoutError:
                    yield OrchestratorChatEvent(type="status", content=f"能力 {tool_name} 仍在执行，正在等待返回。")
        except asyncio.CancelledError:
            if invoke_task is not None:
                invoke_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await invoke_task
            raise
        except Exception as exc:
            result = {"error": str(exc)}

        yield OrchestratorChatEvent(type="tool_result", tool_name=tool_name, tool_args=tool_args, tool_result=result)
        text = self._text_from_tool_result(tool_name, result)
        if text:
            for chunk in self._chunk_text(text):
                yield OrchestratorChatEvent(type="text", content=chunk)
        yield OrchestratorChatEvent(type="done")

    def _build_tool_invocation_kwargs(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        session_context: dict[str, Any],
    ) -> dict[str, Any]:
        selected_materials = session_context.get("selected_materials") or []
        image_ids = [item["material_id"] for item in selected_materials if item.get("material_id")]
        video_model_no_audio = session_context.get("video_model_no_audio", session_context.get("video_no_audio", True))
        kwargs = {
            "project_id": session_context["project_id"],
            "session_id": session_context["session_id"],
            "user_id": session_context["user_id"],
            "platform": session_context.get("platform") or "generic",
            "duration_mode": session_context.get("duration_mode") or "fixed",
            "generation_model": session_context.get("generation_model") or "seedance1.5-pro",
            "style": session_context.get("style") or "commercial",
            "background_template_id": session_context.get("background_template_id"),
            "watermark_image_id": session_context.get("watermark_id"),
            "no_audio": video_model_no_audio,
            "video_model_no_audio": video_model_no_audio,
            "voiceover_no_audio": session_context.get("voiceover_no_audio", False),
            "transition": session_context.get("video_transition") or "none",
            "bgm_mood": session_context.get("bgm_mood") or "none",
            "voice_id": "Chelsie",
            "reference_video_id": session_context.get("reference_video_id"),
            "reference_video_ids": session_context.get("reference_video_ids") or [],
            "script": session_context.get("draft_script") or "",
            "image_ids": image_ids,
            "duration_seconds": max(len(image_ids) * 5, 15) if image_ids else 30,
            "skip_video_generation": session_context.get("skip_video_generation", False),
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        kwargs.update({key: value for key, value in tool_args.items() if value not in (None, "")})
        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            return kwargs
        tool.ensure_loaded()
        signature = inspect.signature(tool.fn)
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return kwargs
        return {key: value for key, value in kwargs.items() if key in signature.parameters}

    def _missing_required_inputs(self, tool: ToolDefinition, session_context: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for item in (tool.metadata or {}).get("required_inputs") or []:
            if item == "image_ids" and not (session_context.get("selected_materials") or []):
                missing.append(item)
            elif item == "reference_video_ids" and len(session_context.get("reference_video_ids") or []) < 2:
                missing.append(item)
            elif item in {"project_id", "session_id", "user_id", "reference_video_id"} and not session_context.get(item):
                missing.append(item)
        return missing

    def _fallback_tool_args(self, tool_name: str, user_message: str) -> dict[str, Any]:
        if tool_name == "analyze_video":
            return {"focus": user_message}
        if tool_name == "replicate_video":
            return {"direction": user_message}
        if tool_name == "remix_video":
            return {"direction": user_message}
        if tool_name == "generate_video":
            return {"user_request": user_message}
        return {}

    def _text_from_tool_result(self, tool_name: str, result: dict[str, Any]) -> str:
        error = str(result.get("error") or "").strip()
        if error:
            return error
        if tool_name == "analyze_video":
            return str(result.get("analysis_report") or "").strip()
        return str(result.get("message") or "").strip()

    def _build_system_prompt(self, session_context: dict[str, Any], memories: list[dict]) -> str:
        selected_materials = session_context.get("selected_materials") or []
        reference_video_ids = session_context.get("reference_video_ids") or []
        lines = [
            f"- 当前平台：{session_context.get('platform') or 'generic'}",
            f"- 当前已选素材数：{len(selected_materials)}",
            f"- 当前参考视频数：{len(reference_video_ids) or (1 if session_context.get('reference_video_id') else 0)}",
            f"- 当前草稿脚本：{(session_context.get('draft_script') or '无')[:160]}",
        ]
        prompt = ORCHESTRATOR_CHAT_SYSTEM_PROMPT + "\n\n当前会话上下文：\n" + "\n".join(lines)
        memory_lines = [f"- {item.get('memory', '')}" for item in memories if item.get("memory")]
        if memory_lines:
            prompt += "\n\n关于这位用户你记住的信息：\n" + "\n".join(memory_lines)
        return prompt

    def _build_skill_inventory_reply(self, session_context: dict[str, Any]) -> str:
        tools = self.tool_registry.list_tool_definitions(agent_name=self.agent_name)
        if not tools:
            return "我当前没有读取到任何已注册的视频能力。"
        lines = [f"Orchestrator 当前按需加载 {len(tools)} 个视频能力："]
        for tool in tools:
            status = "可用" if not self._missing_required_inputs(tool, session_context) else "需补齐上下文"
            source = (tool.metadata or {}).get("source_label") or tool.name
            lines.append(f"- `{tool.name}`（{source}）：{status}")
        return "\n".join(lines)

    def _is_skill_inventory_question(self, user_message: str) -> bool:
        lowered = user_message.lower()
        return any(text in lowered or text in user_message for text in ("有哪些skill", "有什么skill", "skills", "有哪些技能", "有什么技能", "有哪些能力", "会什么"))

    def _looks_like_video_generation_request(self, user_message: str) -> bool:
        raw = str(user_message or "").strip()
        lowered = raw.lower()
        actions = ("生成", "制作", "做一个", "做一条", "做个", "输出", "开始", "启动", "开跑", "出片", "generate", "create", "make", "produce")
        videos = ("视频", "短视频", "营销视频", "广告片", "宣传片", "成片", "片子", "短片", "video", "clip", "reel")
        return ("/generate" in lowered or "generate_video" in lowered) or (
            any(item in lowered or item in raw for item in actions)
            and any(item in lowered or item in raw for item in videos)
        )

    def _looks_like_remix_request(self, user_message: str) -> bool:
        raw = str(user_message or "").strip()
        lowered = raw.lower()
        return any(text in lowered or text in raw for text in ("混剪", "拼接", "剪一个", "剪一条", "合成", "remix", "mashup"))

    def _looks_like_plan_only_video_request(self, user_message: str) -> bool:
        raw = str(user_message or "").strip()
        lowered = raw.lower()
        plan_markers = ("设计方案", "创意方案", "策划方案", "营销方案", "视频方案", "分镜方案", "脚本方案", "拍摄方案", "方案", "策划", "创意", "storyboard", "plan", "proposal")
        if not any(item in lowered or item in raw for item in plan_markers):
            return False
        launch_markers = ("启动流水线", "启动生成", "开始生成", "立即生成", "直接生成", "开跑", "进入生成", "生成视频", "制作视频", "输出视频", "输出成片", "生成成片", "出片")
        return not any(item in lowered or item in raw for item in launch_markers)

    @staticmethod
    def _recent_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for message in messages[-12:]:
            role = str(message.get("role") or "user").strip().lower()
            content = str(message.get("content") or "").strip()
            if content:
                result.append({"role": role if role in {"system", "user", "assistant"} else "user", "content": content})
        return result

    @staticmethod
    def _chunk_text(text: str, size: int = 220) -> list[str]:
        return [text[i:i + size] for i in range(0, len(text), size)] or [""]
