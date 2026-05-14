from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.core.base import AgentContext
from app.agents.executors.shared import PipelineExecutorSupportMixin
from app.agents.stages.audio_subtitle import AudioSubtitleAgent
from app.agents.stages.orchestrator import OrchestratorAgent
from app.agents.stages.prompt_engineer import PromptEngineerAgent
from app.agents.stages.qa_reviewer import QAReviewerAgent
from app.agents.stages.replication_planner import ReplicationPlannerAgent
from app.agents.stages.remix_assembler import RemixAssemblerAgent
from app.agents.stages.remix_planner import RemixPlannerAgent
from app.agents.stages.video_editor import VideoEditorAgent
from app.agents.stages.video_generator import VideoGeneratorAgent
from app.core.config import settings
from app.services.usage_service import UsageRecorder

import asyncio

logger = logging.getLogger(__name__)


class PipelineExecutor(PipelineExecutorSupportMixin):
    """Runs the full agent DAG: orchestrator → prompt → audio+video → editor → qa.

    New in this version
    -------------------
    - **QA reviewer** node after video_editor; auto-retries on critical failures
      (controlled by ``settings.QA_REVIEW_ENABLED`` and ``settings.QA_AUTO_RETRY_ENABLED``).
    - **Checkpoint** saved to DB after every agent so the run can be resumed.
    - **Human-in-the-Loop prompt review**: when ``settings.HUMAN_IN_LOOP_PROMPT_REVIEW``
      is True the pipeline pauses after PromptEngineer with status
      ``"waiting_prompt_review"`` until the caller invokes
      :meth:`resume_from_prompt_review`.
    """

    engine_name = "pipeline"

    def __init__(
        self,
        orchestrator: OrchestratorAgent,
        prompt_engineer: PromptEngineerAgent,
        audio_agent: AudioSubtitleAgent,
        video_gen_agent: VideoGeneratorAgent,
        video_editor: VideoEditorAgent,
        db_session_factory: async_sessionmaker,
        qa_reviewer: Optional[QAReviewerAgent] = None,
        replication_planner: Optional[ReplicationPlannerAgent] = None,
        remix_planner: Optional[RemixPlannerAgent] = None,
        remix_assembler: Optional[RemixAssemblerAgent] = None,
    ):
        self.orchestrator = orchestrator
        self.replication_planner = replication_planner
        self.remix_planner = remix_planner
        self.remix_assembler = remix_assembler
        self.prompt_engineer = prompt_engineer
        self.audio_agent = audio_agent
        self.video_gen_agent = video_gen_agent
        self.video_editor = video_editor
        self.qa_reviewer = qa_reviewer
        self.db_session_factory = db_session_factory

    async def run(
        self,
        pipeline_run_id: str,
        project_id: str,
        input_config: dict,
        user_id: Optional[str] = None,
        memory_service=None,
        mem0=None,
        rag_service=None,
    ) -> dict:
        """Execute the full pipeline. Returns the final video info dict."""
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            db_session_factory=self.db_session_factory,
            usage_recorder=UsageRecorder(self.db_session_factory),
            artifacts={},
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
            rag_service=rag_service,
        )

        try:
            await self._update_run(pipeline_run_id, engine=self.engine_name, status="running")

            result = await self._execute_pipeline(context, input_config)

            if result.get("status") in ("waiting_confirmation", "waiting_prompt_review", "waiting_remix_confirmation"):
                return result

            await self._maybe_learn_background_template(context, input_config)

            final_video = result.get("final_video_path")
            await self._update_run(
                pipeline_run_id,
                engine=self.engine_name,
                status="completed",
                final_video_path=final_video,
                completed_at=datetime.now(timezone.utc),
            )
            return result

        except Exception as e:
            logger.error(f"[{context.trace_id}] Pipeline failed: {e}", exc_info=True)
            await self._update_run(pipeline_run_id, engine=self.engine_name, status="failed", error_message=str(e))
            return {"error": str(e)}

    async def _execute_pipeline(self, context: AgentContext, input_config: dict) -> dict:
        """Core pipeline logic with checkpoints, QA, and HITL support."""

        # ── Step 1: Route by intent ───────────────────────────────────────────
        reference_video_ids = input_config.get("reference_video_ids") or []
        if len(reference_video_ids) >= 2 and self.remix_planner is not None:
            planner_result = await self.remix_planner.run(context, input_config)
            if not planner_result.success:
                raise RuntimeError(f"Remix Planner failed: {planner_result.error}")
            context.artifacts["remix_plan"] = planner_result.output_data.get("remix_plan", {})
            context.artifacts["remix_planner"] = planner_result.output_data
            await context.save_checkpoint()

            if planner_result.output_data.get("requires_confirmation"):
                await self._update_run(
                    context.pipeline_run_id,
                    status="waiting_remix_confirmation",
                    current_agent="remix_planner",
                )
                return {"status": "waiting_remix_confirmation"}
            return await self.resume_from_remix_confirmation(context, input_config)

        if input_config.get("reference_video_id") and self.replication_planner is not None:
            # Replication path: plan first, then proceed to production after confirmation
            planner_result = await self.replication_planner.run(
                context, input_config
            )
            if not planner_result.success:
                raise RuntimeError(f"Replication Planner failed: {planner_result.error}")
            context.artifacts["orchestrator_plan"] = planner_result.output_data
            await context.save_checkpoint()

            if planner_result.output_data.get("requires_confirmation"):
                context.artifacts["replication_plan"] = planner_result.output_data.get("replication_plan", {})
                await self._update_run(
                    context.pipeline_run_id,
                    status="waiting_confirmation",
                    current_agent="replication_planner",
                )
                return {"status": "waiting_confirmation"}
        else:
            # Standard generation path
            orch_result = await self.orchestrator.run(
                context, self.build_agent_input("orchestrator", context.artifacts, input_config)
            )
            if not orch_result.success:
                raise RuntimeError(f"Orchestrator failed: {orch_result.error}")
            context.artifacts["orchestrator_plan"] = orch_result.output_data
            await context.save_checkpoint()

        # ── Step 2: Prompt Engineer ───────────────────────────────────────────
        prompt_result = await self.prompt_engineer.run(
            context,
            self.build_agent_input("prompt_engineer", context.artifacts, input_config),
        )
        if not prompt_result.success:
            raise RuntimeError(f"Prompt Engineer failed: {prompt_result.error}")
        context.artifacts["prompt_plan"] = prompt_result.output_data
        await context.save_checkpoint()

        # ── HITL: Pause for prompt review if requested ────────────────────────
        review_prompts = input_config.get("review_prompts")
        if review_prompts is None:
            review_prompts = settings.HUMAN_IN_LOOP_PROMPT_REVIEW
        if review_prompts:
            await self._update_run(
                context.pipeline_run_id,
                status="waiting_prompt_review",
                current_agent="prompt_engineer",
            )
            return {"status": "waiting_prompt_review"}

        return await self._run_av_and_finish(context, input_config)

    async def _run_av_and_finish(
        self, context: AgentContext, input_config: dict, qa_retry: int = 0
    ) -> dict:
        """Run audio+video generation, editor, and QA (with optional retry)."""

        # ── Step 3: Audio + Video in parallel ────────────────────────────────
        audio_result, video_result = await asyncio.gather(
            self.audio_agent.run(context, self.build_agent_input("audio_subtitle", context.artifacts, input_config)),
            self.video_gen_agent.run(context, self.build_agent_input("video_generator", context.artifacts, input_config)),
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

        # ── Step 4: Video Editor ──────────────────────────────────────────────
        editor_result = await self.video_editor.run(
            context,
            self.build_agent_input("video_editor", context.artifacts, input_config),
        )
        if not editor_result.success:
            raise RuntimeError(f"Video Editor failed: {editor_result.error}")
        context.artifacts["final_video"] = editor_result.output_data
        await context.save_checkpoint()

        # ── Step 5: QA Reviewer ───────────────────────────────────────────────
        if settings.QA_REVIEW_ENABLED and self.qa_reviewer is not None:
            qa_input = self.build_qa_input(context.artifacts, input_config)
            qa_result = await self.qa_reviewer.run(context, qa_input)
            if qa_result.success:
                context.artifacts["qa_report"] = qa_result.output_data
                await context.save_checkpoint()

                report = qa_result.output_data
                if (
                    not report.get("passed")
                    and settings.QA_AUTO_RETRY_ENABLED
                    and qa_retry < settings.MAX_QA_RETRIES
                ):
                    recommendation = report.get("recommendation", "pass")
                    logger.warning(
                        f"[{context.trace_id}] QA failed (score={report.get('overall_score'):.2f}), "
                        f"auto-retry via '{recommendation}' (attempt {qa_retry + 1}/{settings.MAX_QA_RETRIES})"
                    )
                    return await self._qa_retry(context, input_config, recommendation, qa_retry)

        return editor_result.output_data

    async def _qa_retry(
        self,
        context: AgentContext,
        input_config: dict,
        recommendation: str,
        qa_retry: int,
    ) -> dict:
        """Re-run the recommended upstream agent(s) and then re-run QA."""
        if recommendation == "retry_video_generator":
            video_result = await self.video_gen_agent.run(
                context, self.build_agent_input("video_generator", context.artifacts, input_config)
            )
            if not video_result.success:
                await self._update_run(
                    context.pipeline_run_id,
                    status="running",
                    current_agent="video_generator",
                )
                raise RuntimeError(f"Video Generator retry failed: {video_result.error}")
            context.artifacts["video_clips"] = video_result.output_data

        elif recommendation == "retry_audio":
            audio_result = await self.audio_agent.run(
                context, self.build_agent_input("audio_subtitle", context.artifacts, input_config)
            )
            if not audio_result.success:
                await self._update_run(
                    context.pipeline_run_id,
                    status="running",
                    current_agent="audio_subtitle",
                )
                raise RuntimeError(f"Audio Agent retry failed: {audio_result.error}")
            context.artifacts["audio"] = audio_result.output_data

        # Always re-run editor after any upstream retry
        editor_result = await self.video_editor.run(
            context, self.build_agent_input("video_editor", context.artifacts, input_config)
        )
        if not editor_result.success:
            raise RuntimeError(f"Video Editor retry failed: {editor_result.error}")
        context.artifacts["final_video"] = editor_result.output_data
        await context.save_checkpoint()

        # Re-run QA
        if settings.QA_REVIEW_ENABLED and self.qa_reviewer is not None:
            qa_input = self.build_qa_input(context.artifacts, input_config)
            qa_result = await self.qa_reviewer.run(context, qa_input)
            if qa_result.success:
                context.artifacts["qa_report"] = qa_result.output_data
                await context.save_checkpoint()

                report = qa_result.output_data
                if (
                    not report.get("passed")
                    and settings.QA_AUTO_RETRY_ENABLED
                    and qa_retry + 1 < settings.MAX_QA_RETRIES
                ):
                    return await self._qa_retry(context, input_config, report.get("recommendation", "pass"), qa_retry + 1)

        return editor_result.output_data

    # ── Resume methods ────────────────────────────────────────────────────────

    async def resume_from_prompt_review(self, context: AgentContext, input_config: dict) -> dict:
        """Resume pipeline after user approves the prompt plan."""
        return await self._run_av_and_finish(context, input_config)

    async def resume_from_confirmation(self, context: AgentContext, input_config: dict) -> dict:
        """Resume pipeline after user confirms the replication plan."""
        prompt_result = await self.prompt_engineer.run(
            context,
            self.build_agent_input("prompt_engineer", context.artifacts, input_config),
        )
        if not prompt_result.success:
            raise RuntimeError(f"Prompt Engineer failed: {prompt_result.error}")
        context.artifacts["prompt_plan"] = prompt_result.output_data
        await context.save_checkpoint()

        return await self._run_av_and_finish(context, input_config)

    async def resume_from_remix_confirmation(self, context: AgentContext, input_config: dict) -> dict:
        """Resume pipeline after user confirms the remix plan."""
        if self.remix_assembler is None:
            raise RuntimeError("Remix assembler is not configured")
        waiting_result = await self.prepare_remix_after_audio(context, input_config)
        if waiting_result:
            return waiting_result
        assembler_result = await self.remix_assembler.run(
            context,
            self.build_remix_assembler_input(context.artifacts, input_config),
        )
        if not assembler_result.success:
            raise RuntimeError(f"Remix Assembler failed: {assembler_result.error}")
        context.artifacts["final_video"] = assembler_result.output_data
        await context.save_checkpoint()
        return assembler_result.output_data

    # ── Agent map / input builder ─────────────────────────────────────────────

    async def run_named_agent(self, context: AgentContext, agent_name: str, input_config: dict) -> dict:
        agent = self.get_agent_map()[agent_name]
        agent_input = self.build_agent_input(agent_name, context.artifacts, input_config)
        result = await agent.run(context, agent_input)
        if not result.success:
            raise RuntimeError(f"Agent {agent_name} failed: {result.error}")
        artifact_key = self.get_agent_to_artifact_key().get(agent_name)
        if artifact_key:
            context.artifacts[artifact_key] = result.output_data
        return result.output_data

    async def continue_from_retry(self, context: AgentContext, agent_name: str, input_config: dict):
        if agent_name == "orchestrator":
            await self.run_named_agent(context, "prompt_engineer", input_config)
            audio_result, video_result = await asyncio.gather(
                self.run_named_agent(context, "audio_subtitle", input_config),
                self.run_named_agent(context, "video_generator", input_config),
            )
            context.artifacts["audio"] = audio_result
            context.artifacts["video_clips"] = video_result
            await self.run_named_agent(context, "video_editor", input_config)
            return

        if agent_name == "prompt_engineer":
            audio_result, video_result = await asyncio.gather(
                self.run_named_agent(context, "audio_subtitle", input_config),
                self.run_named_agent(context, "video_generator", input_config),
            )
            context.artifacts["audio"] = audio_result
            context.artifacts["video_clips"] = video_result
            await self.run_named_agent(context, "video_editor", input_config)
            return

        if agent_name == "audio_subtitle":
            if "video_clips" not in context.artifacts:
                await self.run_named_agent(context, "video_generator", input_config)
            await self.run_named_agent(context, "video_editor", input_config)
            return

        if agent_name == "video_generator":
            if "audio" not in context.artifacts:
                await self.run_named_agent(context, "audio_subtitle", input_config)
            await self.run_named_agent(context, "video_editor", input_config)
            return

        if agent_name == "video_editor":
            await self.run_named_agent(context, "video_editor", input_config)
            return

        if agent_name == "remix_assembler":
            await self.run_named_agent(context, "remix_assembler", input_config)
            return

        if agent_name == "qa_reviewer":
            return

        raise ValueError(f"Cannot continue from retry for agent: {agent_name}")
