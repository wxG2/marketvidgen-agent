from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker
from typing_extensions import TypedDict

from app.agents.audio_subtitle import AudioSubtitleAgent
from app.agents.base import AgentContext
from app.agents.orchestrator import OrchestratorAgent
from app.agents.prompt_engineer import PromptEngineerAgent
from app.agents.video_editor import VideoEditorAgent
from app.agents.video_generator_agent import VideoGeneratorAgent
from app.models.pipeline import PipelineRun
from app.services.usage_service import UsageRecorder

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    context: AgentContext
    input_config: dict
    orchestrator_plan: dict
    prompt_plan: dict
    audio: dict
    video_clips: dict
    final_video: dict
    error: str


class LangGraphPipelineExecutor:
    """LangGraph-based pipeline executor for VidGen.

    Keeps the existing agent implementations intact and only replaces the
    orchestration layer with a state graph.
    """

    def __init__(
        self,
        orchestrator: OrchestratorAgent,
        prompt_engineer: PromptEngineerAgent,
        audio_agent: AudioSubtitleAgent,
        video_gen_agent: VideoGeneratorAgent,
        video_editor: VideoEditorAgent,
        db_session_factory: async_sessionmaker,
    ):
        self.orchestrator = orchestrator
        self.prompt_engineer = prompt_engineer
        self.audio_agent = audio_agent
        self.video_gen_agent = video_gen_agent
        self.video_editor = video_editor
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
            await self._update_run(pipeline_run_id, status="running")
            result_state = await self.graph.ainvoke(
                {
                    "context": context,
                    "input_config": input_config,
                }
            )

            final_video = (result_state.get("final_video") or {}).get("final_video_path")
            await self._update_run(
                pipeline_run_id,
                status="completed",
                final_video_path=final_video,
                completed_at=datetime.now(timezone.utc),
            )
            return result_state.get("final_video", {})
        except Exception as e:
            logger.error(f"[{context.trace_id}] LangGraph pipeline failed: {e}", exc_info=True)
            await self._update_run(pipeline_run_id, status="failed", error_message=str(e))
            return {"error": str(e)}

    async def _orchestrator_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        input_config = state["input_config"]
        result = await self.orchestrator.run(context, input_config)
        if not result.success:
            raise RuntimeError(f"Orchestrator failed: {result.error}")
        context.artifacts["orchestrator_plan"] = result.output_data
        return {"orchestrator_plan": result.output_data}

    async def _prompt_engineer_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        orch_plan = state["orchestrator_plan"]
        result = await self.prompt_engineer.run(context, orch_plan)
        if not result.success:
            raise RuntimeError(f"Prompt Engineer failed: {result.error}")
        context.artifacts["prompt_plan"] = result.output_data
        return {"prompt_plan": result.output_data}

    async def _audio_node(self, state: PipelineState) -> PipelineState:
        context = state["context"]
        input_config = state["input_config"]
        prompt_plan = state["prompt_plan"]
        audio_input = {
            "script": input_config["script"],
            "voice_params": prompt_plan["voice_params"],
        }
        result = await self.audio_agent.run(context, audio_input)
        if not result.success:
            raise RuntimeError(f"Audio Agent failed: {result.error}")
        context.artifacts["audio"] = result.output_data
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
        return {"final_video": result.output_data}

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
