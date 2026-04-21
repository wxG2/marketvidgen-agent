from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from app.agents.core.tool_registry import ToolDefinition, ToolRegistry
from app.prompts import CHAT_AGENT_SYSTEM_PROMPT
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


@dataclass
class ChatEvent:
    type: str
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillRouteDecision:
    selected_tool_name: str | None = None
    candidate_names: tuple[str, ...] = field(default_factory=tuple)
    should_fallback_to_tool_mode: bool = False


class ChatAgent:
    name = "chat_agent"

    def __init__(self, llm: LLMService, tool_registry: ToolRegistry, mem0=None):
        self.llm = llm
        self.tool_registry = tool_registry
        self.mem0 = mem0

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
    ) -> AsyncIterator[ChatEvent]:
        user_message = str(messages[-1].get("content") or "").strip() if messages else "" # 读取输入最新一条消息
        user_id = session_context.get("user_id")
        session_id = session_context.get("session_id")

        if not user_message:
            yield ChatEvent(type="text", content="你可以先告诉我这次想聊什么，或想让我帮你做什么视频。")
            yield ChatEvent(type="done")
            return

        yield ChatEvent(type="status", content="已收到消息，正在判断任务类型。")

        if self._is_skill_inventory_question(user_message):
            reply = self._build_skill_inventory_reply(session_context)
            for chunk in self._chunk_text(reply):
                yield ChatEvent(type="text", content=chunk)
            yield ChatEvent(type="done")
            return

        # Retrieve relevant memories before calling LLM
        relevant_memories: list[dict] = []
        if self.mem0 and user_id and user_message:
            yield ChatEvent(type="status", content="正在检索会话记忆。")
            try:
                relevant_memories = await self.mem0.search(
                    user_message, user_id, limit=self._mem0_search_limit()
                )
            except Exception:
                pass

        force_tool = session_context.get("force_tool")
        if force_tool:
            yield ChatEvent(type="status", content=f"已指定技能 {force_tool}，准备执行。")
            async for event in self._execute_forced_tool(force_tool, user_message, session_context):
                yield event
            return

        if self._should_launch_generate_video_directly(user_message, session_context):
            yield ChatEvent(type="status", content="已识别为视频生成任务，直接启动视频流水线。")
            async for event in self._execute_forced_tool("generate_video", user_message, session_context):
                yield event
            return

        yield ChatEvent(type="status", content="正在匹配 runtime skill。")
        route_decision = await self._route_runtime_skill(
            messages=messages,
            session_context=session_context,
            relevant_memories=relevant_memories,
        )
        if route_decision.selected_tool_name:
            yield ChatEvent(type="status", content=f"已命中技能 {route_decision.selected_tool_name}，正在读取参数。")
            async for event in self._execute_selected_tool(
                tool_name=route_decision.selected_tool_name,
                messages=messages,
                session_context=session_context,
                relevant_memories=relevant_memories,
            ):
                yield event
            return

        if (
            self._can_use_llm_tools()
            and not route_decision.should_fallback_to_tool_mode
            and self._should_use_direct_llm_stream(user_message, session_context)
        ):
            yield ChatEvent(type="status", content="未命中需要执行的技能，正在调用对话模型。")
            async for event in self._stream_direct_llm_reply(messages, session_context, relevant_memories):
                yield event
            return

        if not self._can_use_llm_tools():
            yield ChatEvent(type="text", content="LLM 客户端未配置，无法处理对话。")
            yield ChatEvent(type="done")
            return

        tools = self._build_tool_definitions(session_context)
        queue: asyncio.Queue[ChatEvent] = asyncio.Queue()
        tool_events: list[ChatEvent] = []

        async def tool_executor(tool_name: str, tool_args: dict[str, Any]) -> tuple[str, list[str]]:
            await queue.put(ChatEvent(type="tool_call", tool_name=tool_name, tool_args=tool_args))
            await queue.put(ChatEvent(type="status", content=f"正在执行技能 {tool_name}。"))
            invocation_kwargs = self._build_tool_invocation_kwargs(tool_name, tool_args, session_context)
            invoke_task: asyncio.Task | None = None
            try:
                invoke_task = asyncio.create_task(
                    self.tool_registry.invoke(tool_name, agent_name=self.name, **invocation_kwargs)
                )
                while True:
                    try:
                        result = await asyncio.wait_for(asyncio.shield(invoke_task), timeout=5)
                        break
                    except asyncio.TimeoutError:
                        await queue.put(
                            ChatEvent(
                                type="status",
                                content=f"技能 {tool_name} 仍在执行，正在等待模型或外部服务返回。",
                            )
                        )
            except asyncio.CancelledError:
                if invoke_task is not None:
                    invoke_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await invoke_task
                raise
            except Exception as exc:
                result = {"error": str(exc)}
            event = ChatEvent(type="tool_result", tool_name=tool_name, tool_args=tool_args, tool_result=result)
            tool_events.append(event)
            await queue.put(event)
            return json.dumps(result, ensure_ascii=False), []

        task = asyncio.create_task(
            self.llm.generate_with_tools(
                system_prompt=self._build_system_prompt(session_context, memories=relevant_memories),
                user_prompt=self._build_user_prompt(messages, session_context),
                schema={
                    "name": "chat_agent_response",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "reply": {"type": "string"},
                        },
                        "required": ["reply"],
                    },
                },
                tools=tools,
                tool_executor=tool_executor,
            )
        )
        yield ChatEvent(type="status", content="正在调用工具选择模型。")
        heartbeat_count = 0

        while True:
            if task.done() and queue.empty():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield event
            except asyncio.TimeoutError:
                heartbeat_count += 1
                if heartbeat_count >= 50:
                    heartbeat_count = 0
                    yield ChatEvent(type="status", content="模型仍在处理工具调用请求。")
                continue

        try:
            parsed, _tool_log, _usage = await task
        except Exception:
            yield ChatEvent(type="text", content="请求处理失败，请稍后重试。")
            yield ChatEvent(type="done")
            return
        final_reply = self._extract_reply_text(parsed)
        text_to_stream = self._choose_final_text(final_reply, tool_events)

        # Store conversation memory asynchronously (fire-and-forget)
        if self.mem0 and user_id and final_reply:
            try:
                conversation = [messages[-1], {"role": "assistant", "content": final_reply}]
                asyncio.create_task(
                    self.mem0.add_from_conversation(
                        conversation, user_id, session_id=session_id,
                    )
                )
            except Exception:
                pass

        for chunk in self._chunk_text(text_to_stream):
            yield ChatEvent(type="text", content=chunk)

        yield ChatEvent(type="done")

    async def _stream_direct_llm_reply(
        self,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        relevant_memories: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Use the model's native text stream for plain conversational replies.

        DashScope OpenAI-compatible mode does not support `tools` together with
        `stream=True`, so we reserve true token streaming for requests that do
        not need tool invocation.
        """
        user_id = session_context.get("user_id")
        session_id = session_context.get("session_id")
        llm_messages = self._build_direct_stream_messages(messages, session_context, relevant_memories)
        collected_chunks: list[str] = []

        next_chunk: asyncio.Task | None = None
        try:
            yield ChatEvent(type="status", content="已向对话模型发起流式请求。")
            stream = self.llm.chat_stream(llm_messages)
            while True:
                next_chunk = asyncio.create_task(stream.__anext__())
                stream_done = False
                try:
                    chunk = await asyncio.wait_for(asyncio.shield(next_chunk), timeout=5)
                except asyncio.TimeoutError:
                    while True:
                        yield ChatEvent(type="status", content="对话模型仍在生成回复。")
                        try:
                            chunk = await asyncio.wait_for(asyncio.shield(next_chunk), timeout=5)
                            break
                        except asyncio.TimeoutError:
                            continue
                        except StopAsyncIteration:
                            stream_done = True
                            break
                except StopAsyncIteration:
                    break
                if stream_done:
                    break
                if not chunk:
                    continue
                collected_chunks.append(chunk)
                yield ChatEvent(type="text", content=chunk)
        except asyncio.CancelledError:
            if next_chunk is not None:
                next_chunk.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await next_chunk
            raise
        except Exception as stream_exc:
            logger.warning("Streaming reply interrupted: %r", stream_exc)
            if collected_chunks:
                yield ChatEvent(type="text", content="\n\n[网络中断，回复可能不完整，请重试或发送【继续】。]")
                yield ChatEvent(type="done")
                return
            yield ChatEvent(type="text", content="请求处理失败，请稍后重试。")
            yield ChatEvent(type="done")
            return

        full_reply = self._normalize_reply_text("".join(collected_chunks))

        if self.mem0 and user_id and full_reply:
            try:
                conversation = [messages[-1], {"role": "assistant", "content": full_reply}]
                asyncio.create_task(
                    self.mem0.add_from_conversation(
                        conversation, user_id, session_id=session_id,
                    )
                )
            except Exception:
                pass

        yield ChatEvent(type="done")

    def _build_tool_definitions(self, session_context: dict[str, Any]) -> list[dict[str, Any]]:
        """Build the tool list to pass to the LLM.

        Schemas are derived from ToolRegistry (stored in Claude input_schema format,
        converted to OpenAI format for Qwen).  Context-sensitive filtering and
        description augmentation are applied on top.
        """
        result: list[dict[str, Any]] = []
        for tool in self.tool_registry.list_tool_definitions(agent_name=self.name):
            if not self._tool_is_available_in_session(tool, session_context):
                continue
            openai_tool = tool.to_openai_tool()
            context_note = self._tool_context_note(tool, session_context)
            if context_note:
                openai_tool = {
                    **openai_tool,
                    "function": {
                        **openai_tool["function"],
                        "description": f"{openai_tool['function']['description']} {context_note}",
                    },
                }
            result.append(openai_tool)

        return result

    def _build_system_prompt(self, session_context: dict[str, Any], memories: list[dict] | None = None) -> str:
        selected_materials = session_context.get("selected_materials") or []
        session_lines = [
            f"- 当前平台：{session_context.get('platform') or 'generic'}",
            f"- 当前已选素材数：{len(selected_materials)}",
            f"- 当前是否有参考视频：{'是' if session_context.get('reference_video_id') else '否'}",
            f"- 当前时长模式：{session_context.get('duration_mode') or 'fixed'}",
            f"- 当前视频生成模型：{session_context.get('generation_model') or 'seedance1.5-pro'}",
            f"- 当前草稿脚本：{(session_context.get('draft_script') or '无')[:160]}",
        ]
        prompt = CHAT_AGENT_SYSTEM_PROMPT + "\n\n当前会话上下文：\n" + "\n".join(session_lines)
        unavailable_lines = [
            f"- {tool.name}：{self._skill_availability_summary(tool, session_context).replace('当前不可用：', '')}"
            for tool in self.tool_registry.list_tool_definitions(agent_name=self.name)
            if not self._tool_is_available_in_session(tool, session_context)
        ]
        if unavailable_lines:
            prompt += (
                "\n\n当前会话暂不可直接调用的 skills：\n"
                + "\n".join(unavailable_lines)
                + "\n如果用户请求这些能力，请先明确告知需要补齐对应上下文，不要强行调用工具。"
            )
        if memories:
            memory_lines = [f"- {m.get('memory', '')}" for m in memories if m.get("memory")]
            if memory_lines:
                prompt += "\n\n关于这位用户你记住的信息：\n" + "\n".join(memory_lines)
        return prompt

    def _build_user_prompt(self, messages: list[dict[str, str]], session_context: dict[str, Any]) -> str:
        history_lines = []
        for message in messages[-12:]:
            role = message.get("role") or "user"
            content = str(message.get("content") or "").strip()
            if content:
                history_lines.append(f"{role}: {content}")
        return (
            "以下是最近的对话历史，请基于上下文回复用户。\n\n"
            + "\n".join(history_lines)
        )

    def _build_direct_stream_messages(
        self,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        memories: list[dict] | None = None,
    ) -> list[dict[str, str]]:
        direct_messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": self._build_system_prompt(session_context, memories=memories),
            }
        ]
        for message in messages[-12:]:
            role = str(message.get("role") or "user").strip().lower()
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if role not in {"system", "user", "assistant"}:
                role = "user"
            direct_messages.append({"role": role, "content": content})
        return direct_messages

    def _should_use_direct_llm_stream(
        self,
        user_message: str,
        session_context: dict[str, Any],
    ) -> bool:
        """Prefer true streaming for plain chat; reserve tool mode for explicit actions."""
        lowered = user_message.lower()
        if any(token in lowered for token in ("force_tool", "/tool", "tool:")):
            return False

        return True

    def _should_launch_generate_video_directly(
        self,
        user_message: str,
        session_context: dict[str, Any],
    ) -> bool:
        """Fast path for the current product mode: selected images + clear video request."""
        tool = self.tool_registry.get_tool("generate_video")
        if tool is None:
            return False
        if tool.required_permission and not self.tool_registry.has_permission(self.name, tool.required_permission):
            return False
        if self._missing_required_inputs(tool, session_context):
            return False
        if self._looks_like_plan_only_video_request(user_message):
            return False
        return self._looks_like_video_generation_request(user_message)

    def _looks_like_video_generation_request(self, user_message: str) -> bool:
        raw_text = str(user_message or "").strip()
        lowered = raw_text.lower()
        if not lowered:
            return False
        if "/generate" in lowered or "generate_video" in lowered:
            return True

        action_markers = (
            "生成",
            "制作",
            "做一个",
            "做一条",
            "做个",
            "输出",
            "开始",
            "启动",
            "开跑",
            "出片",
            "generate",
            "create",
            "make",
            "produce",
        )
        video_markers = (
            "视频",
            "短视频",
            "营销视频",
            "广告片",
            "宣传片",
            "成片",
            "片子",
            "短片",
            "video",
            "clip",
            "reel",
        )
        return any(marker in lowered or marker in raw_text for marker in action_markers) and any(
            marker in lowered or marker in raw_text for marker in video_markers
        )

    def _is_skill_inventory_question(self, user_message: str) -> bool:
        lowered = user_message.lower()
        patterns = (
            "有哪些skill",
            "有什么skill",
            "哪些skill",
            "skills",
            "有哪些技能",
            "有什么技能",
            "哪些技能",
            "会什么技能",
            "能用什么技能",
            "有哪些工具",
            "有什么工具",
            "哪些工具",
        )
        return any(pattern in lowered or pattern in user_message for pattern in patterns)

    def _build_skill_inventory_reply(self, session_context: dict[str, Any]) -> str:
        visible_tools = self.tool_registry.list_tool_definitions(agent_name=self.name)
        if not visible_tools:
            return "我当前没有读取到任何已注册的 runtime skills。"

        lines = [f"我当前从 runtime skills 文件里读取到 {len(visible_tools)} 个技能："]
        for index, tool in enumerate(visible_tools, start=1):
            metadata = tool.metadata or {}
            source_name = str(
                metadata.get("source_label")
                or Path(
                    str(
                        metadata.get("source_path")
                        or tool.source_path
                        or f"backend/app/agents/skills/{tool.name}"
                    )
                ).name
            )
            use_when = metadata.get("use_when") or []
            current_status = self._skill_availability_summary(tool, session_context)
            summary = str(tool.description or "").strip()
            if "。" in summary:
                summary = summary.split("。", 1)[0] + "。"

            lines.append(f"{index}. `{tool.name}`")
            lines.append(f"   文件：`{source_name}`")
            lines.append(f"   作用：{summary}")
            if use_when:
                lines.append(f"   适用：{use_when[0]}")
            lines.append(f"   当前会话：{current_status}")

        lines.append("如果你愿意，我也可以继续展开每个 skill 的输入参数和触发条件。")
        return "\n".join(lines)

    async def _route_runtime_skill(
        self,
        *,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        relevant_memories: list[dict] | None = None,
    ) -> SkillRouteDecision:
        user_message = str(messages[-1].get("content") or "").strip() if messages else ""
        if not user_message:
            return SkillRouteDecision()

        available_tools = [
            tool for tool in self.tool_registry.list_tool_definitions(agent_name=self.name)
            if self._tool_is_available_in_session(tool, session_context)
        ]
        if not available_tools:
            return SkillRouteDecision()

        scored_candidates = self._score_runtime_skill_candidates(user_message, available_tools)
        if not scored_candidates:
            return SkillRouteDecision()

        candidate_names = tuple(tool.name for tool, _score in scored_candidates)
        if len(scored_candidates) == 1:
            selected_tool = scored_candidates[0][0].name
            logger.info("[chat_agent] auto-routed runtime skill '%s' via routing hints", selected_tool)
            return SkillRouteDecision(selected_tool_name=selected_tool, candidate_names=candidate_names)

        top_tool, top_score = scored_candidates[0]
        second_score = scored_candidates[1][1]
        if top_score > second_score:
            logger.info(
                "[chat_agent] auto-routed runtime skill '%s' via top routing score (%s > %s)",
                top_tool.name,
                top_score,
                second_score,
            )
            return SkillRouteDecision(selected_tool_name=top_tool.name, candidate_names=candidate_names)

        selected_tool = await self._route_skill_candidates_with_llm(
            candidates=[tool for tool, _score in scored_candidates],
            messages=messages,
            session_context=session_context,
            relevant_memories=relevant_memories,
        )
        if selected_tool:
            logger.info("[chat_agent] auto-routed runtime skill '%s' via LLM candidate routing", selected_tool)
            return SkillRouteDecision(selected_tool_name=selected_tool, candidate_names=candidate_names)

        logger.info(
            "[chat_agent] runtime skill routing remained ambiguous; falling back to tool mode candidates=%s",
            list(candidate_names),
        )
        return SkillRouteDecision(
            candidate_names=candidate_names,
            should_fallback_to_tool_mode=self._can_use_llm_tools(),
        )

    def _score_runtime_skill_candidates(
        self,
        user_message: str,
        tools: list[ToolDefinition],
    ) -> list[tuple[ToolDefinition, int]]:
        lowered = user_message.lower()
        scored: list[tuple[ToolDefinition, int]] = []
        for tool in tools:
            if self._should_suppress_runtime_skill_route(user_message, tool):
                continue

            metadata = tool.metadata or {}
            score = 0
            if tool.name in lowered or tool.name.replace("_", "") in lowered:
                score += 6

            for hint in metadata.get("routing_hints") or []:
                normalized_hint = str(hint or "").strip()
                if not normalized_hint:
                    continue
                if normalized_hint.lower() in lowered or normalized_hint in user_message:
                    score += 3 if len(normalized_hint) > 1 else 1

            if score > 0:
                scored.append((tool, score))

        scored.sort(key=lambda item: (-item[1], item[0].name))
        return scored

    def _should_suppress_runtime_skill_route(self, user_message: str, tool: ToolDefinition) -> bool:
        """Apply conservative, skill-specific routing guards before scoring hints."""
        if tool.name == "generate_video" and self._looks_like_plan_only_video_request(user_message):
            return True
        return False

    def _looks_like_plan_only_video_request(self, user_message: str) -> bool:
        """Treat written video plans as chat, not production launch requests."""
        raw_text = str(user_message or "").strip()
        lowered = raw_text.lower()
        if not lowered:
            return False

        plan_markers = (
            "设计方案",
            "创意方案",
            "策划方案",
            "营销方案",
            "视频方案",
            "分镜方案",
            "脚本方案",
            "拍摄方案",
            "方案",
            "策划",
            "创意",
            "storyboard",
            "plan",
            "proposal",
        )
        if not any(marker in lowered or marker in raw_text for marker in plan_markers):
            return False

        explicit_launch_markers = (
            "/generate",
            "generate_video",
            "启动流水线",
            "启动生成",
            "开始生成",
            "立即生成",
            "马上生成",
            "直接生成",
            "开跑",
            "跑起来",
            "进入生成",
            "进入完整流程",
            "生成视频",
            "生成这个视频",
            "生成一条视频",
            "生成一个视频",
            "制作视频",
            "制作一条视频",
            "做一个视频",
            "输出视频",
            "输出成片",
            "生成成片",
            "制作成片",
            "出片",
        )
        return not any(marker in lowered or marker in raw_text for marker in explicit_launch_markers)

    async def _route_skill_candidates_with_llm(
        self,
        *,
        candidates: list[ToolDefinition],
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        relevant_memories: list[dict] | None = None,
    ) -> str | None:
        if not candidates or not self._can_use_llm_tools():
            return None

        candidate_names = [tool.name for tool in candidates]
        history_lines: list[str] = []
        for message in messages[-4:]:
            role = message.get("role") or "user"
            content = str(message.get("content") or "").strip()
            if content:
                history_lines.append(f"{role}: {content}")

        candidate_lines: list[str] = []
        for tool in candidates:
            metadata = tool.metadata or {}
            use_when = metadata.get("use_when") or []
            required_inputs = metadata.get("required_inputs") or []
            candidate_lines.append(f"- {tool.name}")
            candidate_lines.append(f"  说明：{self._short_tool_summary(tool.description)}")
            if use_when:
                candidate_lines.append(f"  适用：{use_when[0]}")
            do_not_use_when = metadata.get("do_not_use_when") or []
            if do_not_use_when:
                candidate_lines.append(f"  不适用：{do_not_use_when[0]}")
            if required_inputs:
                candidate_lines.append(f"  依赖上下文：{', '.join(required_inputs)}")

        prompt_lines = [
            "请从下面候选 runtime skills 里选择最适合立即执行的一个。",
            "只有当用户当前这条消息明确是在要求执行某个技能时才选择它，否则返回 none。",
            "如果用户只是讨论、提问、润色文案、闲聊，必须返回 none。",
            "当前会话上下文：",
            f"- 当前平台：{session_context.get('platform') or 'generic'}",
            f"- 当前已选素材数：{len(session_context.get('selected_materials') or [])}",
            f"- 当前是否有参考视频：{'是' if session_context.get('reference_video_id') else '否'}",
            f"- 当前草稿脚本：{(session_context.get('draft_script') or '无')[:120]}",
            "最近对话：",
            *history_lines,
            "候选 skills：",
            *candidate_lines,
        ]
        memory_lines = [f"- {m.get('memory', '')}" for m in (relevant_memories or []) if m.get("memory")]
        if memory_lines:
            prompt_lines.extend(["用户相关记忆：", *memory_lines[:3]])

        try:
            parsed, _usage = await self.llm.generate_structured(
                system_prompt=(
                    "你是一个保守的 runtime skill 路由器。"
                    "只在用户明确要求立刻执行某个技能时才选择 skill。"
                    "不允许臆测或过度调用；拿不准就返回 none。"
                ),
                user_prompt="\n".join(prompt_lines),
                schema={
                    "name": "runtime_skill_route",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "selected_skill": {
                                "type": "string",
                                "enum": [*candidate_names, "none"],
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["selected_skill"],
                    },
                },
            )
        except Exception:
            logger.exception("[chat_agent] runtime skill candidate routing failed")
            return None

        selected_tool = str(parsed.get("selected_skill") or "").strip()
        if selected_tool in candidate_names:
            return selected_tool
        return None

    def _tool_is_available_in_session(
        self,
        tool: ToolDefinition,
        session_context: dict[str, Any],
    ) -> bool:
        return not self._missing_required_inputs(tool, session_context)

    def _missing_required_inputs(
        self,
        tool: ToolDefinition,
        session_context: dict[str, Any],
    ) -> list[str]:
        metadata = tool.metadata or {}
        required_inputs = metadata.get("required_inputs") or []
        missing_inputs: list[str] = []
        for item in required_inputs:
            if item == "image_ids":
                if not (session_context.get("selected_materials") or []):
                    missing_inputs.append(item)
            elif item in {"project_id", "session_id", "user_id", "reference_video_id"}:
                if not session_context.get(item):
                    missing_inputs.append(item)
        return missing_inputs

    def _tool_context_note(self, tool: ToolDefinition, session_context: dict[str, Any]) -> str:
        metadata = tool.metadata or {}
        required_inputs = set(metadata.get("required_inputs") or [])
        notes: list[str] = []
        if "image_ids" in required_inputs:
            notes.append(f"当前会话已选素材数：{len(session_context.get('selected_materials') or [])}。")
        if "reference_video_id" in required_inputs and session_context.get("reference_video_id"):
            notes.append("当前会话已设置参考视频。")
        return " ".join(notes)

    def _short_tool_summary(self, description: str) -> str:
        summary = str(description or "").strip()
        if not summary:
            return ""
        if "。" in summary:
            return summary.split("。", 1)[0] + "。"
        return summary

    def _skill_availability_summary(self, tool: ToolDefinition, session_context: dict[str, Any]) -> str:
        missing_inputs = self._missing_required_inputs(tool, session_context)
        if not missing_inputs:
            return "可用"

        reason_map = {
            "project_id": "缺少 project_id",
            "session_id": "缺少 session_id",
            "user_id": "缺少 user_id",
            "reference_video_id": "还没有参考视频",
            "image_ids": "还没有选中素材",
        }
        reasons = [reason_map.get(item, f"缺少 {item}") for item in missing_inputs]
        return "当前不可用：" + "，".join(dict.fromkeys(reasons))

    def _build_tool_invocation_kwargs(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        session_context: dict[str, Any],
    ) -> dict[str, Any]:
        selected_materials = session_context.get("selected_materials") or []
        image_ids = [item["material_id"] for item in selected_materials if item.get("material_id")]
        video_model_no_audio = session_context.get(
            "video_model_no_audio",
            session_context.get("video_no_audio", True),
        )
        default_kwargs = {
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
            "script": session_context.get("draft_script") or "", # 这个draft_script是谁生成的
            "image_ids": image_ids,
            "duration_seconds": max(len(image_ids) * 5, 15) if image_ids else 30,
            "skip_video_generation": session_context.get("skip_video_generation", False),
        }
        merged_kwargs = {
            key: value for key, value in default_kwargs.items()
            if value is not None
        }
        merged_kwargs.update({
            key: value for key, value in tool_args.items()
            if value not in (None, "")
        })
        if "no_audio" in tool_args and "video_model_no_audio" not in tool_args:
            merged_kwargs["video_model_no_audio"] = tool_args["no_audio"]
        if "no_audio" in tool_args and "voiceover_no_audio" not in tool_args:
            merged_kwargs["voiceover_no_audio"] = tool_args["no_audio"]

        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            return merged_kwargs

        tool.ensure_loaded()
        signature = inspect.signature(tool.fn)
        accepts_var_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_var_kwargs:
            return merged_kwargs

        allowed_names = set(signature.parameters)
        return {key: value for key, value in merged_kwargs.items() if key in allowed_names}

    async def _execute_forced_tool(
        self,
        tool_name: str,
        user_message: str,
        session_context: dict[str, Any],
    ) -> AsyncIterator[ChatEvent]:
        """Directly invoke a specific tool without going through LLM tool selection."""
        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            yield ChatEvent(type="text", content=f"未知的技能：{tool_name}")
            yield ChatEvent(type="done")
            return
        if tool.required_permission and not self.tool_registry.has_permission(self.name, tool.required_permission):
            yield ChatEvent(type="text", content=f"未知的技能：{tool_name}")
            yield ChatEvent(type="done")
            return

        tool_args = self._fallback_tool_args(tool_name, user_message)

        yield ChatEvent(type="tool_call", tool_name=tool_name, tool_args=tool_args)
        yield ChatEvent(type="status", content=f"正在执行技能 {tool_name}。")
        invoke_task: asyncio.Task | None = None
        try:
            invocation_kwargs = self._build_tool_invocation_kwargs(tool_name, tool_args, session_context)
            invoke_task = asyncio.create_task(
                self.tool_registry.invoke(tool_name, agent_name=self.name, **invocation_kwargs)
            )
            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(invoke_task), timeout=5)
                    break
                except asyncio.TimeoutError:
                    yield ChatEvent(type="status", content=f"技能 {tool_name} 仍在执行，正在等待模型或外部服务返回。")
        except asyncio.CancelledError:
            if invoke_task is not None:
                invoke_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await invoke_task
            raise
        except Exception as exc:
            result = {"error": str(exc)}
        yield ChatEvent(type="tool_result", tool_name=tool_name, tool_args=tool_args, tool_result=result)

        error = str(result.get("error") or "").strip()
        if error:
            for chunk in self._chunk_text(error):
                yield ChatEvent(type="text", content=chunk)
        elif tool_name == "analyze_video":
            for chunk in self._chunk_text(str(result.get("analysis_report") or "")):
                yield ChatEvent(type="text", content=chunk)
        elif tool_name == "generate_video" and result.get("message"):
            for chunk in self._chunk_text(result["message"]):
                yield ChatEvent(type="text", content=chunk)

        yield ChatEvent(type="done")

    async def _execute_selected_tool(
        self,
        tool_name: str,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        relevant_memories: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        user_message = str(messages[-1].get("content") or "").strip() if messages else ""
        extract_task = asyncio.create_task(self._extract_selected_tool_args(
            tool_name=tool_name,
            user_message=user_message,
            messages=messages,
            session_context=session_context,
            relevant_memories=relevant_memories,
        ))
        try:
            while True:
                try:
                    tool_args = await asyncio.wait_for(asyncio.shield(extract_task), timeout=5)
                    break
                except asyncio.TimeoutError:
                    yield ChatEvent(type="status", content=f"仍在为技能 {tool_name} 提取参数。")
        except asyncio.CancelledError:
            extract_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await extract_task
            raise

        yield ChatEvent(type="tool_call", tool_name=tool_name, tool_args=tool_args)
        yield ChatEvent(type="status", content=f"正在执行技能 {tool_name}。")
        invoke_task: asyncio.Task | None = None
        try:
            invocation_kwargs = self._build_tool_invocation_kwargs(tool_name, tool_args, session_context)
            invoke_task = asyncio.create_task(
                self.tool_registry.invoke(tool_name, agent_name=self.name, **invocation_kwargs)
            )
            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(invoke_task), timeout=5)
                    break
                except asyncio.TimeoutError:
                    yield ChatEvent(type="status", content=f"技能 {tool_name} 仍在执行，正在等待模型或外部服务返回。")
        except asyncio.CancelledError:
            if invoke_task is not None:
                invoke_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await invoke_task
            raise
        except Exception as exc:
            result = {"error": str(exc)}
        yield ChatEvent(type="tool_result", tool_name=tool_name, tool_args=tool_args, tool_result=result)

        error = str(result.get("error") or "").strip()
        if error:
            for chunk in self._chunk_text(error):
                yield ChatEvent(type="text", content=chunk)
        elif tool_name == "analyze_video":
            for chunk in self._chunk_text(str(result.get("analysis_report") or "")):
                yield ChatEvent(type="text", content=chunk)
        elif tool_name == "generate_video" and result.get("message"):
            for chunk in self._chunk_text(result["message"]):
                yield ChatEvent(type="text", content=chunk)

        yield ChatEvent(type="done")

    async def _extract_selected_tool_args(
        self,
        *,
        tool_name: str,
        user_message: str,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        relevant_memories: list[dict] | None = None,
    ) -> dict[str, Any]:
        fallback = self._fallback_tool_args(tool_name, user_message)
        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            return fallback

        tool.ensure_loaded()
        properties = ((tool.input_schema or {}).get("properties") or {})
        if not properties or not self._can_use_llm_tools():
            return fallback

        try:
            parsed, _usage = await self.llm.generate_structured(
                system_prompt=(
                    "你是一个技能参数提取器。"
                    "只根据最近对话与当前会话上下文提取这个技能真正需要的参数。"
                    "不要编造用户没有明确表达的信息。"
                    "缺失字段可以留空，或直接省略可选字段。"
                    "只输出符合 schema 的 JSON。"
                ),
                user_prompt=self._build_tool_argument_prompt(
                    tool_name=tool_name,
                    tool_description=tool.description,
                    user_message=user_message,
                    messages=messages,
                    session_context=session_context,
                    memories=relevant_memories,
                ),
                schema={
                    "name": f"{tool_name}_args",
                    "schema": tool.input_schema,
                },
            )
        except Exception:
            return fallback

        if not isinstance(parsed, dict):
            return fallback

        extracted = {
            key: value for key, value in parsed.items()
            if key in properties and value not in (None, "")
        }
        return {**fallback, **extracted}

    def _build_tool_argument_prompt(
        self,
        *,
        tool_name: str,
        tool_description: str,
        user_message: str,
        messages: list[dict[str, str]],
        session_context: dict[str, Any],
        memories: list[dict] | None = None,
    ) -> str:
        history_lines: list[str] = []
        for message in messages[-6:]:
            role = message.get("role") or "user"
            content = str(message.get("content") or "").strip()
            if content:
                history_lines.append(f"{role}: {content}")

        session_lines = [
            f"- 当前平台：{session_context.get('platform') or 'generic'}",
            f"- 当前已选素材数：{len(session_context.get('selected_materials') or [])}",
            f"- 当前是否有参考视频：{'是' if session_context.get('reference_video_id') else '否'}",
            f"- 当前时长模式：{session_context.get('duration_mode') or 'fixed'}",
            f"- 当前视频生成模型：{session_context.get('generation_model') or 'seedance1.5-pro'}",
            f"- 当前草稿脚本：{(session_context.get('draft_script') or '无')[:160]}",
        ]
        prompt_lines = [
            f"目标技能：{tool_name}",
            f"技能说明：{tool_description}",
            "当前会话上下文：",
            *session_lines,
            "最近对话：",
            *history_lines,
            f"用户最新消息：{user_message}",
        ]
        memory_lines = [f"- {m.get('memory', '')}" for m in (memories or []) if m.get("memory")]
        if memory_lines:
            prompt_lines.extend(["用户相关记忆：", *memory_lines])

        tool = self.tool_registry.get_tool(tool_name)
        if tool is not None:
            tool.ensure_loaded()
            instructions = str((tool.metadata or {}).get("instructions") or "").strip()
            if instructions:
                prompt_lines.extend(["技能正文：", instructions])

            supporting_sections = self._load_supporting_skill_sections(tool)
            if supporting_sections:
                prompt_lines.extend(["技能参考资料：", *supporting_sections])
        return "\n".join(prompt_lines)

    def _fallback_tool_args(self, tool_name: str, user_message: str) -> dict[str, Any]:
        if tool_name == "analyze_video":
            return {"focus": user_message}
        if tool_name == "replicate_video":
            return {"direction": user_message}
        if tool_name == "generate_video":
            return {"user_request": user_message}
        tool = self.tool_registry.get_tool(tool_name)
        properties = ((tool.input_schema or {}).get("properties") or {}) if tool else {}
        preferred_fields = (
            "prompt",
            "request",
            "instruction",
            "content",
            "query",
            "brief",
            "goal",
            "task",
            "description",
        )
        for field_name in preferred_fields:
            schema = properties.get(field_name) or {}
            if schema.get("type") == "string":
                return {field_name: user_message}

        string_fields = [
            field_name for field_name, schema in properties.items()
            if schema.get("type") == "string"
        ]
        if len(string_fields) == 1:
            return {string_fields[0]: user_message}
        return {}

    def _load_supporting_skill_sections(self, tool: ToolDefinition) -> list[str]:
        sections: list[str] = []
        for file_path in (tool.metadata or {}).get("supporting_files") or []:
            path = Path(str(file_path))
            if not path.exists() or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if not content:
                continue
            sections.append(f"[{path.name}]\n{content[:800]}")
        return sections

    def _choose_final_text(self, final_reply: str, tool_events: list[ChatEvent]) -> str:
        if not tool_events:
            return self._normalize_reply_text(final_reply)

        last_event = tool_events[-1]
        result = last_event.tool_result or {}
        error_message = str(result.get("error") or "").strip()
        if error_message:
            return error_message
        if last_event.tool_name == "analyze_video":
            return str(result.get("analysis_report") or final_reply or "").strip()
        return ""

    def _normalize_reply_text(self, text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""

        for _ in range(4):
            try:
                parsed = json.loads(normalized)
            except (json.JSONDecodeError, TypeError):
                break

            if isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict):
                    reply = first.get("reply")
                    if isinstance(reply, str) and reply.strip():
                        normalized = reply.strip()
                        continue
                if isinstance(first, str) and first.strip():
                    normalized = first.strip()
                    continue

            if isinstance(parsed, dict):
                reply = parsed.get("reply")
                if isinstance(reply, str) and reply.strip():
                    normalized = reply.strip()
                    continue
            if isinstance(parsed, str) and parsed.strip():
                normalized = parsed.strip()
                continue
            break

        reply_match = re.search(r'"reply"\s*:\s*"((?:\\.|[^"])*)"', normalized, re.DOTALL)
        if reply_match:
            normalized = reply_match.group(1)

        return normalized.replace("\\n", "\n").replace("\\t", "\t").strip()

    def _extract_reply_text(self, payload: Any) -> str:
        if isinstance(payload, dict):
            candidate = payload.get("reply") or payload.get("raw_response") or ""
            return self._normalize_reply_text(str(candidate))
        if isinstance(payload, list):
            for item in payload:
                extracted = self._extract_reply_text(item)
                if extracted:
                    return extracted
            return self._normalize_reply_text(json.dumps(payload, ensure_ascii=False))
        return self._normalize_reply_text(str(payload or ""))

    def _can_use_llm_tools(self) -> bool:
        return getattr(self.llm, "client", None) is not None

    def _mem0_search_limit(self) -> int:
        try:
            from app.config import settings
            return settings.MEM0_SEARCH_LIMIT
        except Exception:
            return 5

    def _chunk_text(self, text: str, chunk_size: int = 18) -> list[str]:
        normalized = text or ""
        return [normalized[i:i + chunk_size] for i in range(0, len(normalized), chunk_size) if normalized[i:i + chunk_size]]
