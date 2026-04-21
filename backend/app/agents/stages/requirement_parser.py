from __future__ import annotations

import logging
from typing import Any

from app.agents.core.base import AgentContext, AgentResult, BaseAgent
from app.agents.stages.requirement_utils import (
    REQUIREMENT_PARSER_SYSTEM_PROMPT,
    build_requirement_prompt,
    build_requirement_schema,
    merge_requirement_fields,
    needs_llm_requirement_parsing,
)
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class RequirementParserAgent(BaseAgent):
    """Pre-pipeline stage: parses free-form user input into structured video parameters.

    Runs before OrchestratorAgent so that all downstream agents receive
    clean, validated configuration instead of raw user text.
    """

    name = "requirement_parser"

    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        await context.report_progress("requirement_parser: 解析用户需求，提取结构化视频参数。", agent_name=self.name)

        raw_message: str = str(input_data.get("user_request") or input_data.get("script") or "").strip()
        image_ids: list[str] = input_data.get("image_ids") or []

        # If all parameters are already clear from keywords and session context, skip LLM
        needs_llm = needs_llm_requirement_parsing(raw_message)

        usage_records: list[dict] = []
        llm_result: dict[str, Any] = {}

        if needs_llm and getattr(self.llm, "client", None) is not None:
            schema = build_requirement_schema()
            prompt = build_requirement_prompt(input_data, raw_message, len(image_ids))

            try:
                llm_result, usage = await self.llm.generate_structured(
                    system_prompt=REQUIREMENT_PARSER_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    schema=schema,
                )
                usage_records.append({
                    "provider": "qwen",
                    "model_name": getattr(getattr(self.llm, "client", None), "model", "mock"),
                    "operation": "requirement_parse",
                    **usage,
                })
            except Exception as exc:
                logger.warning("RequirementParser LLM call failed: %s", exc, exc_info=True)
                llm_result = {}

        output = merge_requirement_fields(input_data, llm_result)
        parsed_platform = output["platform"]
        parsed_duration = output["duration_seconds"]
        parsed_style = output["style"]
        parsed_bgm = output["bgm_mood"]

        await context.report_progress(
            f"requirement_parser: 完成。平台={parsed_platform}, 时长={parsed_duration}s, "
            f"风格={parsed_style}, BGM={parsed_bgm}",
            agent_name=self.name,
        )

        return AgentResult(success=True, output_data=output, usage_records=usage_records)

    def _needs_llm_parsing(self, message: str) -> bool:
        """Use LLM only when the message is long enough to contain implicit parameters."""
        if not message or len(message) < 10:
            return False
        # Short simple messages (e.g. "生成视频") don't need LLM extraction
        if len(message) < 30:
            return False
        return True
