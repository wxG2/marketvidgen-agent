from __future__ import annotations

import asyncio
import logging
from app.agents.executors.langgraph.exceptions import WaitingConfirmation, WaitingPromptReview

from app.agents.executors.langgraph.state import LangGraphPipelineState
from app.config import settings
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.agents.stages.requirement_parser import RequirementParserAgent
    from app.agents.stages.orchestrator import OrchestratorAgent
    from app.agents.stages.prompt_engineer import PromptEngineerAgent
    from app.agents.stages.audio_subtitle import AudioSubtitleAgent
    from app.agents.stages.video_editor import VideoEditorAgent
    from app.agents.stages.video_generator import VideoGeneratorAgent
logger = logging.getLogger(__name__)

class LangGraphPipelineNodeMixin:
    """Node implementations for the LangGraph video pipeline."""
    requirement_parser: "RequirementParserAgent | None"
    orchestrator: "OrchestratorAgent"
    prompt_engineer: "PromptEngineerAgent"
    audio_agent: "AudioSubtitleAgent"
    video_gen_agent: "VideoGeneratorAgent"
    editor_agent: "VideoEditorAgent"

    async def _requirement_parser_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        input_config = state["input_config"]
        if self.requirement_parser is None or input_config.get("reference_video_id"):
            return {}

        result = await self.requirement_parser.run(
            context,
            self.build_agent_input("requirement_parser", context.artifacts, input_config),
        )
        if result.success:
            context.artifacts["parsed_requirement"] = result.output_data
            await context.save_checkpoint()
            return {"parsed_requirement": result.output_data}

        logger.warning("Requirement Parser failed; continuing with raw input: %s", result.error)
        return {}

    async def _orchestrator_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        input_config = state["input_config"]
        result = await self.orchestrator.run(
            context,
            self.build_agent_input("orchestrator", context.artifacts, input_config),
        )
        if not result.success:
            raise RuntimeError(f"Orchestrator failed: {result.error}")
        context.artifacts["orchestrator_plan"] = result.output_data
        await context.save_checkpoint()
        return {"orchestrator_plan": result.output_data}

    async def _replication_planner_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        input_config = state["input_config"]
        result = await self.replication_planner.run(context, input_config)
        if not result.success:
            raise RuntimeError(f"Replication Planner failed: {result.error}")
        context.artifacts["orchestrator_plan"] = result.output_data
        await context.save_checkpoint()

        if result.output_data.get("requires_confirmation"):
            context.artifacts["replication_plan"] = result.output_data.get("replication_plan", {})
            raise WaitingConfirmation()

        return {"orchestrator_plan": result.output_data}

    async def _prompt_engineer_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        input_config = state.get("input_config", {})
        result = await self.prompt_engineer.run(
            context,
            self.build_agent_input("prompt_engineer", context.artifacts, input_config),
        )
        if not result.success:
            raise RuntimeError(f"Prompt Engineer failed: {result.error}")
        context.artifacts["prompt_plan"] = result.output_data
        await context.save_checkpoint()

        # Persist director plan as a chat message regardless of review gate,
        # so the user can see the shot plan in the chat window.
        from app.services.director_plan_chat import persist_director_plan_message
        await persist_director_plan_message(
            self.db_session_factory,
            context.pipeline_run_id,
            result.output_data,
        )

        review_prompts = input_config.get("review_prompts")
        if review_prompts is None:
            review_prompts = settings.HUMAN_IN_LOOP_PROMPT_REVIEW
        if review_prompts:
            raise WaitingPromptReview()

        return {"prompt_plan": result.output_data}

    async def _audio_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        audio_input = self.build_audio_input(context.artifacts, state["input_config"])
        result = await self.audio_agent.run(context, audio_input)
        if not result.success:
            await self._update_run(
                context.pipeline_run_id,
                status="running",
                current_agent="audio_subtitle",
            )
            raise RuntimeError(f"Audio Agent failed: {result.error}")
        context.artifacts["audio"] = result.output_data
        await context.save_checkpoint()
        return {"audio": result.output_data}

    async def _video_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        video_input = self.build_video_input(context.artifacts, state["input_config"])
        result = await self.video_gen_agent.run(context, video_input)
        if not result.success:
            await self._update_run(
                context.pipeline_run_id,
                status="running",
                current_agent="video_generator",
            )
            raise RuntimeError(f"Video Generator failed: {result.error}")
        context.artifacts["video_clips"] = result.output_data
        await context.save_checkpoint()
        return {"video_clips": result.output_data}

    async def _editor_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        editor_input = self.build_editor_input(context.artifacts, state["input_config"])
        result = await self.video_editor.run(context, editor_input)
        if not result.success:
            raise RuntimeError(f"Video Editor failed: {result.error}")
        context.artifacts["final_video"] = result.output_data
        await context.save_checkpoint()
        return {"final_video": result.output_data}

    async def _qa_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        context = state["context"]
        qa_retry_count = state.get("qa_retry_count", 0)
        qa_input = self.build_qa_input(context.artifacts, state["input_config"])
        qa_result = await self.qa_reviewer.run(context, qa_input)
        if not qa_result.success:
            logger.warning("[%s] QA agent execution failed; skipping QA", context.trace_id)
            return {"qa_report": {"passed": True, "overall_score": 0.5, "issues": [], "recommendation": "pass"}}

        report = qa_result.output_data
        context.artifacts["qa_report"] = report
        await context.save_checkpoint()

        return {"qa_report": report, "qa_retry_count": qa_retry_count + 1}

    def _qa_routing(self, state: LangGraphPipelineState) -> str:
        report = state.get("qa_report", {})
        retry_count = state.get("qa_retry_count", 0)

        if report.get("passed", True):
            return "pass"

        if not settings.QA_AUTO_RETRY_ENABLED:
            return "pass"

        if retry_count > settings.MAX_QA_RETRIES:
            logger.warning("QA retry limit (%s) reached; delivering anyway", settings.MAX_QA_RETRIES)
            return "pass"

        recommendation = report.get("recommendation", "pass")
        if recommendation in ("retry_video_generator", "retry_audio", "retry_editor"):
            logger.info("QA routing -> %s (retry #%s)", recommendation, retry_count)
            return recommendation

        return "pass"

    async def resume_from_confirmation(self, context, input_config: dict) -> dict:
        result = await self.prompt_engineer.run(
            context,
            self.build_agent_input("prompt_engineer", context.artifacts, input_config),
        )
        if not result.success:
            raise RuntimeError(f"Prompt Engineer failed: {result.error}")
        context.artifacts["prompt_plan"] = result.output_data
        await context.save_checkpoint()
        return await self._run_av_editor_qa(context, input_config)

    async def resume_from_prompt_review(self, context, input_config: dict) -> dict:
        return await self._run_av_editor_qa(context, input_config)

    async def _run_av_editor_qa(self, context, input_config: dict) -> dict:
        audio_input = self.build_audio_input(context.artifacts, input_config)
        video_input = self.build_video_input(context.artifacts, input_config)
        audio_result, video_result = await asyncio.gather(
            self.audio_agent.run(context, audio_input),
            self.video_gen_agent.run(context, video_input),
        )
        if not audio_result.success:
            await self._update_run(
                context.pipeline_run_id,
                status="running",
                current_agent="audio_subtitle",
            )
            raise RuntimeError(f"Audio Agent failed: {audio_result.error}")
        if not video_result.success:
            await self._update_run(
                context.pipeline_run_id,
                status="running",
                current_agent="video_generator",
            )
            raise RuntimeError(f"Video Generator failed: {video_result.error}")

        context.artifacts["audio"] = audio_result.output_data
        context.artifacts["video_clips"] = video_result.output_data
        await context.save_checkpoint()

        editor_result = await self.video_editor.run(
            context,
            self.build_editor_input(context.artifacts, input_config),
        )
        if not editor_result.success:
            raise RuntimeError(f"Video Editor failed: {editor_result.error}")
        context.artifacts["final_video"] = editor_result.output_data
        await context.save_checkpoint()

        if settings.QA_REVIEW_ENABLED and self.qa_reviewer is not None:
            qa_result = await self.qa_reviewer.run(
                context,
                self.build_qa_input(context.artifacts, input_config),
            )
            if qa_result.success:
                context.artifacts["qa_report"] = qa_result.output_data
                await context.save_checkpoint()

        return editor_result.output_data
