from __future__ import annotations

from typing_extensions import TypedDict

from app.agents.core.base import AgentContext


class LangGraphPipelineState(TypedDict, total=False):
    context: AgentContext
    input_config: dict
    parsed_requirement: dict
    orchestrator_plan: dict
    prompt_plan: dict
    audio: dict
    video_clips: dict
    final_video: dict
    qa_report: dict
    qa_retry_count: int
    error: str
