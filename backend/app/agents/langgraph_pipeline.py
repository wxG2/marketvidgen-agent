from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker
from typing_extensions import TypedDict

from app.agents.audio_subtitle import AudioSubtitleAgent
from app.agents.base import AgentContext
from app.agents.orchestrator import OrchestratorAgent
from app.agents.prompt_engineer import PromptEngineerAgent
from app.agents.qa_reviewer import QAReviewerAgent
from app.agents.video_editor import VideoEditorAgent
from app.agents.video_generator_agent import VideoGeneratorAgent
from app.config import settings
from app.models.pipeline import PipelineRun
from app.services.background_template_learning import learn_background_template_from_run
from app.services.usage_service import UsageRecorder

logger = logging.getLogger(__name__)


class _WaitingConfirmation(Exception):
    """Raised when the orchestrator needs user confirmation before continuing."""
    pass


class _AnalysisOnly(Exception):
    """Raised when the orchestrator answered the query without needing video generation."""
    pass


class _WaitingPromptReview(Exception):
    """Raised after PromptEngineer when HITL prompt review is enabled."""
    pass


class _QARetry(Exception):
    """Raised by the QA node to signal that an upstream agent should be retried."""
    def __init__(self, recommendation: str, attempt: int) -> None:
        self.recommendation = recommendation
        self.attempt = attempt


class PipelineState(TypedDict, total=False):
    context: AgentContext
    input_config: dict
    orchestrator_plan: dict
    prompt_plan: dict
    audio: dict
    video_clips: dict
    final_video: dict
    qa_report: dict
    qa_retry_count: int
    error: str


class LangGraphPipelineExecutor:
    """LangGraph-based pipeline executor with QA node, checkpoints, and HITL support.

    New in this version
    -------------------
    - **QA reviewer** node after video_editor with conditional retry routing.
    - **Checkpoint** saved to DB after every agent completes.
    - **Human-in-the-Loop prompt review**: raises ``_WaitingPromptReview``
      after PromptEngineer when enabled via ``settings.HUMAN_IN_LOOP_PROMPT_REVIEW``
      or ``input_config["review_prompts"]``.
    """

    engine_name = "langgraph"

    def __init__(
        self,
        orchestrator: OrchestratorAgent,
        prompt_engineer: PromptEngineerAgent,
        audio_agent: AudioSubtitleAgent,
        video_gen_agent: VideoGeneratorAgent,
        video_editor: VideoEditorAgent,
        db_session_factory: async_sessionmaker,
        qa_reviewer: Optional[QAReviewerAgent] = None,
    ):
        self.orchestrator = orchestrator
        self.prompt_engineer = prompt_engineer
        self.audio_agent = audio_agent
        self.video_gen_agent = video_gen_agent
        self.video_editor = video_editor
        self.qa_reviewer = qa_reviewer
        self.db_session_factory = db_session_factory
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(PipelineState)
        builder.add_node("orchestrator", self._orchestrator_node)
        builder.add_node("prompt_engineer", self._prompt_engineer_node)
        builder.add_node("audio_subtitle", self._audio_node)
        builder.add_node("video_generator", self._video_node)
        builder.add_node("video_editor", self._editor_node)

        builder.add_edge(START, "orchestrator")
        builder.add_edge("orchestrator", "prompt_engineer")
        builder.add_edge("prompt_engineer", "audio_subtitle")
        builder.add_edge("prompt_engineer", "video_generator")
        builder.add_edge("audio_subtitle", "video_editor")
        builder.add_edge("video_generator", "video_editor")

        if settings.QA_REVIEW_ENABLED and self.qa_reviewer is not None:
            builder.add_node("qa_reviewer", self._qa_node)
            builder.add_edge("video_editor", "qa_reviewer")
            builder.add_conditional_edges(
                "qa_reviewer",
                self._qa_routing,
                {
                    "pass": END,
                    "retry_video_generator": "video_generator",
                    "retry_audio": "audio_subtitle",
                    "retry_editor": "video_editor",
                },
            )
        else:
            builder.add_edge("video_editor", END)

        return builder.compile()

    async def run(self, pipeline_run_id: str, project_id: str, input_config: dict) -> dict:
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            db_session_factory=self.db_session_factory,
            usage_recorder=UsageRecorder(self.db_session_factory),
            artifacts={},
        )

        try:
            await self._update_run(pipeline_run_id, engine=self.engine_name, status="running")
            result_state = await self.graph.ainvoke(
                {
                    "context": context,
                    "input_config": input_config,
                    "qa_retry_count": 0,
                }
            )
            llm = getattr(self.prompt_engineer, "llm", None) or getattr(self.prompt_engineer, "llm_service", None)
            if llm is not None:
                await learn_background_template_from_run(
                    db_session_factory=self.db_session_factory,
                    llm=llm,
                    pipeline_run_id=pipeline_run_id,
                    input_config=input_config,
                    artifacts=context.artifacts,
                )

            final_video = (result_state.get("final_video") or {}).get("final_video_path")
            await self._update_run(
                pipeline_run_id,
                engine=self.engine_name,
                status="completed",
                final_video_path=final_video,
                completed_at=datetime.now(timezone.utc),
            )
            return result_state.get("final_video", {})
        except _WaitingConfirmation:
            await self._update_run(
                pipeline_run_id,
                status="waiting_confirmation",
                current_agent="orchestrator",
            )
            return {"status": "waiting_confirmation"}
        except _WaitingPromptReview:
            await self._update_run(
                pipeline_run_id,
                status="waiting_prompt_review",
                current_agent="prompt_engineer",
            )
            return {"status": "waiting_prompt_review"}
        except _AnalysisOnly:
            await self._update_run(
                pipeline_run_id,
                engine=self.engine_name,
                status="completed",
                final_video_path=None,
                completed_at=datetime.now(timezone.utc),
            )
            return context.artifacts.get("orchestrator_plan", {})
        except Exception as e:
            logger.error(f"[{context.trace_id}] LangGraph pipeline failed: {e}", exc_info=True)
            await self._update_run(pipeline_run_id, engine=self.engine_name, status="failed", error_message=str(e))
            return {"error": str(e)}

    # ── Graph nodes ───────────────────────────────────────────────────────────

    async def _orchestrator_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        input_config = state["input_config"]
        result = await self.orchestrator.run(context, input_config)
        if not result.success:
            raise RuntimeError(f"Orchestrator failed: {result.error}")
        context.artifacts["orchestrator_plan"] = result.output_data
        await context.save_checkpoint()

        if result.output_data.get("requires_confirmation"):
            context.artifacts["replication_plan"] = result.output_data.get("replication_plan", {})
            raise _WaitingConfirmation()

        if result.output_data.get("analysis_only"):
            context.artifacts["orchestrator_plan"] = result.output_data
            raise _AnalysisOnly()

        return {"orchestrator_plan": result.output_data}

    async def _prompt_engineer_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        orch_plan = state["orchestrator_plan"]
        result = await self.prompt_engineer.run(context, orch_plan)
        if not result.success:
            raise RuntimeError(f"Prompt Engineer failed: {result.error}")
        context.artifacts["prompt_plan"] = result.output_data
        await context.save_checkpoint()

        # HITL: pause for prompt review
        input_config = state.get("input_config", {})
        if input_config.get("review_prompts", settings.HUMAN_IN_LOOP_PROMPT_REVIEW):
            raise _WaitingPromptReview()

        return {"prompt_plan": result.output_data}

    async def _audio_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        input_config = state["input_config"]
        prompt_plan = state["prompt_plan"]
        orchestrator_plan = state["orchestrator_plan"]
        shot_prompts = prompt_plan.get("shot_prompts", [])
        shot_script = "\n".join(
            str(item.get("script_segment") or "").strip()
            for item in shot_prompts
            if isinstance(item, dict) and str(item.get("script_segment") or "").strip()
        )
        audio_input = {
            "script": shot_script or orchestrator_plan.get("script") or input_config["script"],
            "voice_params": prompt_plan["voice_params"],
        }
        result = await self.audio_agent.run(context, audio_input)
        if not result.success:
            raise RuntimeError(f"Audio Agent failed: {result.error}")
        context.artifacts["audio"] = result.output_data
        await context.save_checkpoint()
        return {"audio": result.output_data}

    async def _video_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        input_config = state["input_config"]
        prompt_plan = state["prompt_plan"]
        video_input = {
            "shot_prompts": prompt_plan["shot_prompts"],
            "no_audio": input_config.get("no_audio", True),
        }
        result = await self.video_gen_agent.run(context, video_input)
        if not result.success:
            raise RuntimeError(f"Video Generator failed: {result.error}")
        context.artifacts["video_clips"] = result.output_data
        await context.save_checkpoint()
        return {"video_clips": result.output_data}

    async def _editor_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        input_config = state["input_config"]
        orch_plan = state["orchestrator_plan"]
        prompt_plan = state["prompt_plan"]
        audio = state["audio"]
        video_clips = state["video_clips"]
        editor_input = {
            "video_clips": video_clips["video_clips"],
            "audio_path": audio["audio_path"],
            "subtitle_path": audio["subtitle_path"],
            "shot_prompts": prompt_plan["shot_prompts"],
            "duration_mode": input_config.get("duration_mode", "fixed"),
            "shot_durations": [s["duration_seconds"] for s in orch_plan["shots"]],
            "transition": input_config.get("transition", "none"),
            "transition_duration": input_config.get("transition_duration", 0.5),
            "bgm_mood": input_config.get("bgm_mood", "none"),
            "bgm_volume": input_config.get("bgm_volume", 0.15),
            "watermark_path": input_config.get("watermark_path"),
        }
        result = await self.video_editor.run(context, editor_input)
        if not result.success:
            raise RuntimeError(f"Video Editor failed: {result.error}")
        context.artifacts["final_video"] = result.output_data
        await context.save_checkpoint()
        return {"final_video": result.output_data}

    async def _qa_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        input_config = state["input_config"]
        qa_retry_count = state.get("qa_retry_count", 0)

        qa_input = {
            "shot_prompts": context.artifacts.get("prompt_plan", {}).get("shot_prompts", []),
            "video_clips": context.artifacts.get("video_clips", {}).get("video_clips", []),
            "audio": context.artifacts.get("audio", {}),
            "final_video": context.artifacts.get("final_video", {}),
            "input_config": input_config,
        }
        qa_result = await self.qa_reviewer.run(context, qa_input)
        if not qa_result.success:
            # QA agent itself failed — let through and deliver anyway
            logger.warning(f"[{context.trace_id}] QA agent execution failed; skipping QA")
            return {"qa_report": {"passed": True, "overall_score": 0.5, "issues": [], "recommendation": "pass"}}

        report = qa_result.output_data
        context.artifacts["qa_report"] = report
        await context.save_checkpoint()

        return {"qa_report": report, "qa_retry_count": qa_retry_count + 1}

    def _qa_routing(self, state: PipelineState) -> str:
        """Decide where to route after QA: pass or retry upstream agent."""
        report = state.get("qa_report", {})
        retry_count = state.get("qa_retry_count", 0)

        if report.get("passed", True):
            return "pass"

        if not settings.QA_AUTO_RETRY_ENABLED:
            return "pass"

        if retry_count > settings.MAX_QA_RETRIES:
            logger.warning(
                f"QA retry limit ({settings.MAX_QA_RETRIES}) reached; delivering anyway"
            )
            return "pass"

        recommendation = report.get("recommendation", "pass")
        if recommendation in ("retry_video_generator", "retry_audio", "retry_editor"):
            logger.info(f"QA routing → {recommendation} (retry #{retry_count})")
            return recommendation

        return "pass"

    # ── Resume helpers ────────────────────────────────────────────────────────

    async def resume_from_confirmation(self, context: AgentContext, input_config: dict) -> dict:
        """Resume pipeline from prompt_engineer onward after user confirms the replication plan."""
        orch_plan = context.artifacts.get("orchestrator_plan", {})

        result = await self.prompt_engineer.run(context, orch_plan)
        if not result.success:
            raise RuntimeError(f"Prompt Engineer failed: {result.error}")
        context.artifacts["prompt_plan"] = result.output_data
        await context.save_checkpoint()
        prompt_plan = result.output_data

        return await self._run_av_editor_qa(context, input_config, prompt_plan, orch_plan)

    async def resume_from_prompt_review(self, context: AgentContext, input_config: dict) -> dict:
        """Resume after user approves the generated prompts."""
        orch_plan = context.artifacts.get("orchestrator_plan", {})
        prompt_plan = context.artifacts.get("prompt_plan", {})
        return await self._run_av_editor_qa(context, input_config, prompt_plan, orch_plan)

    async def _run_av_editor_qa(
        self,
        context: AgentContext,
        input_config: dict,
        prompt_plan: dict,
        orch_plan: dict,
    ) -> dict:
        shot_prompts = prompt_plan.get("shot_prompts", [])
        shot_script = "\n".join(
            str(item.get("script_segment") or "").strip()
            for item in shot_prompts
            if isinstance(item, dict) and str(item.get("script_segment") or "").strip()
        )
        audio_input = {
            "script": shot_script or orch_plan.get("script") or input_config.get("script", ""),
            "voice_params": prompt_plan.get("voice_params", {}),
        }
        video_input = {
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "no_audio": input_config.get("no_audio", True),
        }
        audio_result, video_result = await asyncio.gather(
            self.audio_agent.run(context, audio_input),
            self.video_gen_agent.run(context, video_input),
        )
        if not audio_result.success:
            raise RuntimeError(f"Audio Agent failed: {audio_result.error}")
        if not video_result.success:
            raise RuntimeError(f"Video Generator failed: {video_result.error}")
        context.artifacts["audio"] = audio_result.output_data
        context.artifacts["video_clips"] = video_result.output_data
        await context.save_checkpoint()

        editor_input = {
            "video_clips": video_result.output_data.get("video_clips", []),
            "audio_path": audio_result.output_data.get("audio_path", ""),
            "subtitle_path": audio_result.output_data.get("subtitle_path", ""),
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "duration_mode": input_config.get("duration_mode", "fixed"),
            "shot_durations": [s["duration_seconds"] for s in orch_plan.get("shots", [])],
            "transition": input_config.get("transition", "none"),
            "transition_duration": input_config.get("transition_duration", 0.5),
            "bgm_mood": input_config.get("bgm_mood", "none"),
            "bgm_volume": input_config.get("bgm_volume", 0.15),
            "watermark_path": input_config.get("watermark_path"),
        }
        editor_result = await self.video_editor.run(context, editor_input)
        if not editor_result.success:
            raise RuntimeError(f"Video Editor failed: {editor_result.error}")
        context.artifacts["final_video"] = editor_result.output_data
        await context.save_checkpoint()

        # QA pass
        if settings.QA_REVIEW_ENABLED and self.qa_reviewer is not None:
            qa_input = {
                "shot_prompts": prompt_plan.get("shot_prompts", []),
                "video_clips": video_result.output_data.get("video_clips", []),
                "audio": audio_result.output_data,
                "final_video": editor_result.output_data,
                "input_config": input_config,
            }
            qa_result = await self.qa_reviewer.run(context, qa_input)
            if qa_result.success:
                context.artifacts["qa_report"] = qa_result.output_data
                await context.save_checkpoint()

        return editor_result.output_data

    # ── Agent map / artifact keys ─────────────────────────────────────────────

    def get_agent_map(self) -> dict[str, object]:
        agents = {
            "orchestrator": self.orchestrator,
            "prompt_engineer": self.prompt_engineer,
            "audio_subtitle": self.audio_agent,
            "video_generator": self.video_gen_agent,
            "video_editor": self.video_editor,
        }
        if self.qa_reviewer is not None:
            agents["qa_reviewer"] = self.qa_reviewer
        return agents

    def get_agent_to_artifact_key(self) -> dict[str, str]:
        return {
            "orchestrator": "orchestrator_plan",
            "prompt_engineer": "prompt_plan",
            "audio_subtitle": "audio",
            "video_generator": "video_clips",
            "video_editor": "final_video",
            "qa_reviewer": "qa_report",
        }

    async def _update_run(self, pipeline_run_id: str, **kwargs):
        kwargs["updated_at"] = datetime.now(timezone.utc)
        async with self.db_session_factory() as session:
            run = await session.get(PipelineRun, pipeline_run_id)
            if run:
                if run.status == "cancelled" and kwargs.get("status") != "cancelled":
                    return
                for key, value in kwargs.items():
                    setattr(run, key, value)
                await session.commit()
