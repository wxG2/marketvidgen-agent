from __future__ import annotations

import logging
import asyncio

from app.agents.executors.langgraph.exceptions import (
    WaitingConfirmation,
    WaitingPromptReview,
    WaitingRemixConfirmation,
)

from app.agents.executors.langgraph.state import LangGraphPipelineState
from app.core.config import settings
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.agents.stages.orchestrator import OrchestratorAgent
    from app.agents.stages.prompt_engineer import PromptEngineerAgent
    from app.agents.stages.audio_subtitle import AudioSubtitleAgent
    from app.agents.stages.video_editor import VideoEditorAgent
    from app.agents.stages.video_generator import VideoGeneratorAgent
    from app.agents.stages.remix_assembler import RemixAssemblerAgent
    from app.agents.stages.remix_planner import RemixPlannerAgent
logger = logging.getLogger(__name__)

class LangGraphPipelineNodeMixin:
    """Node implementations for the LangGraph video pipeline."""
    orchestrator: "OrchestratorAgent"
    prompt_engineer: "PromptEngineerAgent"
    audio_agent: "AudioSubtitleAgent"
    video_gen_agent: "VideoGeneratorAgent"
    editor_agent: "VideoEditorAgent"
    remix_planner: "RemixPlannerAgent | None"
    remix_assembler: "RemixAssemblerAgent | None"

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

    async def _remix_planner_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        if self.remix_planner is None:
            raise RuntimeError("Remix planner is not configured")
        context = state["context"]
        input_config = state["input_config"]
        result = await self.remix_planner.run(context, input_config)
        if not result.success:
            raise RuntimeError(f"Remix Planner failed: {result.error}")

        remix_plan = result.output_data.get("remix_plan", {})
        context.artifacts["remix_plan"] = remix_plan
        context.artifacts["remix_planner"] = result.output_data
        await context.save_checkpoint()

        if result.output_data.get("requires_confirmation"):
            raise WaitingRemixConfirmation()

        return {"remix_plan": remix_plan}

    async def _remix_assembler_node(self, state: LangGraphPipelineState) -> LangGraphPipelineState:
        if self.remix_assembler is None:
            raise RuntimeError("Remix assembler is not configured")
        context = state["context"]
        waiting_result = await self.prepare_remix_after_audio(context, state["input_config"])
        if waiting_result:
            raise WaitingRemixConfirmation()
        input_config = self.build_remix_assembler_input(context.artifacts, state["input_config"])
        result = await self.remix_assembler.run(context, input_config)
        if not result.success:
            raise RuntimeError(f"Remix Assembler failed: {result.error}")
        context.artifacts["final_video"] = result.output_data
        await context.save_checkpoint()
        return {"final_video": result.output_data}

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

    async def resume_from_remix_confirmation(self, context, input_config: dict) -> dict:
        if self.remix_assembler is None:
            raise RuntimeError("Remix assembler is not configured")
        waiting_result = await self.prepare_remix_after_audio(context, input_config)
        if waiting_result:
            return waiting_result
        result = await self.remix_assembler.run(
            context,
            self.build_remix_assembler_input(context.artifacts, input_config),
        )
        if not result.success:
            raise RuntimeError(f"Remix Assembler failed: {result.error}")
        context.artifacts["final_video"] = result.output_data
        await context.save_checkpoint()
        return result.output_data

    async def resume_from_prompt_review(self, context, input_config: dict) -> dict:
        return await self._run_av_editor_qa(context, input_config)

    async def run_named_agent(self, context, agent_name: str, input_config: dict) -> dict:
        agent = self.get_agent_map()[agent_name]
        agent_input = self.build_agent_input(agent_name, context.artifacts, input_config)
        result = await agent.run(context, agent_input)
        if not result.success:
            await self._update_run(
                context.pipeline_run_id,
                status="running",
                current_agent=agent_name,
            )
            raise RuntimeError(f"Agent {agent_name} failed: {result.error}")

        artifact_key = self.get_agent_to_artifact_key().get(agent_name)
        if artifact_key:
            context.artifacts[artifact_key] = result.output_data
            await context.save_checkpoint()
        return result.output_data

    async def _run_qa_after_retry(self, context, input_config: dict) -> None:
        if not settings.QA_REVIEW_ENABLED or self.qa_reviewer is None:
            return

        qa_input = self.build_qa_input(context.artifacts, input_config)
        qa_result = await self.qa_reviewer.run(context, qa_input)
        if qa_result.success:
            context.artifacts["qa_report"] = qa_result.output_data
            await context.save_checkpoint()
        else:
            logger.warning("[%s] QA agent execution failed after retry; skipping QA", context.trace_id)

    async def _run_editor_qa_after_retry(self, context, input_config: dict) -> dict:
        await self.run_named_agent(context, "video_editor", input_config)
        await self._run_qa_after_retry(context, input_config)
        return context.artifacts.get("final_video", {})

    async def continue_from_retry(self, context, agent_name: str, input_config: dict) -> dict:
        if agent_name == "orchestrator":
            await self.run_named_agent(context, "prompt_engineer", input_config)
            await asyncio.gather(
                self.run_named_agent(context, "audio_subtitle", input_config),
                self.run_named_agent(context, "video_generator", input_config),
            )
            return await self._run_editor_qa_after_retry(context, input_config)

        if agent_name == "prompt_engineer":
            await asyncio.gather(
                self.run_named_agent(context, "audio_subtitle", input_config),
                self.run_named_agent(context, "video_generator", input_config),
            )
            return await self._run_editor_qa_after_retry(context, input_config)

        if agent_name == "audio_subtitle":
            if "video_clips" not in context.artifacts:
                await self.run_named_agent(context, "video_generator", input_config)
            return await self._run_editor_qa_after_retry(context, input_config)

        if agent_name == "video_generator":
            if "audio" not in context.artifacts:
                await self.run_named_agent(context, "audio_subtitle", input_config)
            return await self._run_editor_qa_after_retry(context, input_config)

        if agent_name == "video_editor":
            await self._run_qa_after_retry(context, input_config)
            return context.artifacts.get("final_video", {})

        if agent_name == "remix_assembler":
            return context.artifacts.get("final_video", {})

        if agent_name == "qa_reviewer":
            return context.artifacts.get("final_video", {})

        raise ValueError(f"Cannot continue from retry for agent: {agent_name}")

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
