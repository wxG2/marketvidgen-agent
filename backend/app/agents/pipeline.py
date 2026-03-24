from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.base import AgentContext, AgentResult
from app.agents.orchestrator import OrchestratorAgent
from app.agents.prompt_engineer import PromptEngineerAgent
from app.agents.audio_subtitle import AudioSubtitleAgent
from app.agents.video_generator_agent import VideoGeneratorAgent
from app.agents.video_editor import VideoEditorAgent
from app.models.pipeline import PipelineRun
from app.services.usage_service import UsageRecorder

import asyncio

logger = logging.getLogger(__name__)


class PipelineExecutor:
    """Runs the full agent DAG: orchestrator → prompt → audio+video → editor."""

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

    async def run(self, pipeline_run_id: str, project_id: str, input_config: dict) -> dict:
        """Execute the full pipeline. Returns the final video info dict."""
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

            result = await self._execute_pipeline(context, input_config)

            final_video = result.get("final_video_path")
            await self._update_run(
                pipeline_run_id,
                status="completed",
                final_video_path=final_video,
                completed_at=datetime.now(timezone.utc),
            )
            return result

        except Exception as e:
            logger.error(f"[{context.trace_id}] Pipeline failed: {e}", exc_info=True)
            await self._update_run(pipeline_run_id, status="failed", error_message=str(e))
            return {"error": str(e)}

    async def _execute_pipeline(self, context: AgentContext, input_config: dict) -> dict:
        """Core pipeline logic: sequential agents with parallel audio+video."""

        # ── Step 1: Orchestrator ──
        orch_result = await self.orchestrator.run(context, input_config)
        if not orch_result.success:
            raise RuntimeError(f"Orchestrator failed: {orch_result.error}")
        context.artifacts["orchestrator_plan"] = orch_result.output_data

        # ── Step 2: Prompt Engineer ──
        prompt_result = await self.prompt_engineer.run(context, orch_result.output_data)
        if not prompt_result.success:
            raise RuntimeError(f"Prompt Engineer failed: {prompt_result.error}")
        context.artifacts["prompt_plan"] = prompt_result.output_data

        # ── Step 3: Audio + Video in parallel ──
        audio_input = {
            "script": input_config["script"],
            "voice_params": prompt_result.output_data["voice_params"],
        }
        video_input = {
            "shot_prompts": prompt_result.output_data["shot_prompts"],
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

        # ── Step 4: Video Editor (final step) ──
        editor_input = {
            "video_clips": video_result.output_data["video_clips"],
            "audio_path": audio_result.output_data["audio_path"],
            "subtitle_path": audio_result.output_data["subtitle_path"],
            "shot_prompts": prompt_result.output_data["shot_prompts"],
            "duration_mode": input_config.get("duration_mode", "fixed"),
            "shot_durations": [s["duration_seconds"] for s in context.artifacts["orchestrator_plan"]["shots"]],
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

        return editor_result.output_data

    async def _update_run(self, pipeline_run_id: str, **kwargs):
        """Update PipelineRun fields."""
        kwargs["updated_at"] = datetime.now(timezone.utc)
        async with self.db_session_factory() as session:
            run = await session.get(PipelineRun, pipeline_run_id)
            if run:
                if run.status == "cancelled" and kwargs.get("status") != "cancelled":
                    return
                for key, value in kwargs.items():
                    setattr(run, key, value)
                await session.commit()
