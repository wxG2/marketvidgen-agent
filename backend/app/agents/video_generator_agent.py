from __future__ import annotations

import asyncio
import logging

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.config import settings
from app.services.video_generator import VideoGenerator

logger = logging.getLogger(__name__)


class VideoGeneratorAgent(BaseAgent):
    """Generates video clips for each shot using the image-to-video service."""

    name = "video_generator"

    def __init__(self, video_generator: VideoGenerator):
        self.generator = video_generator

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        shot_prompts: list[dict] = input_data["shot_prompts"]
        self._no_audio = input_data.get("no_audio", settings.SEEDANCE_NO_AUDIO)
        # Optional: specific shot indices to regenerate (for L1 rollback)
        regenerate_indices: list[int] | None = input_data.get("regenerate_indices")

        # If rollback specifies which shots to redo, only redo those
        if regenerate_indices is not None:
            shots_to_process = [s for s in shot_prompts if s["shot_idx"] in regenerate_indices]
            # Keep existing clips for shots not being regenerated
            existing_clips = context.artifacts.get("video_clips", {}).get("video_clips", [])
            existing_by_idx = {c["shot_idx"]: c for c in existing_clips}
        else:
            shots_to_process = shot_prompts
            existing_by_idx = {}

        # Generate all shots in parallel
        tasks = [
            self._generate_single(context, shot)
            for shot in shots_to_process
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        video_clips = []
        errors = []

        # Merge existing (unchanged) clips with newly generated ones
        new_by_idx = {}
        for shot, result in zip(shots_to_process, results):
            if isinstance(result, Exception):
                errors.append(f"Shot {shot['shot_idx']}: {result}")
                continue
            new_by_idx[result["shot_idx"]] = result

        # Build final clip list in order
        all_indices = sorted(set(
            [s["shot_idx"] for s in shot_prompts]
        ))
        for idx in all_indices:
            if idx in new_by_idx:
                video_clips.append(new_by_idx[idx])
            elif idx in existing_by_idx:
                video_clips.append(existing_by_idx[idx])

        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        if errors and not video_clips:
            return AgentResult(success=False, output_data={}, error="; ".join(errors))

        output = {
            "video_clips": video_clips,
        }
        return AgentResult(success=True, output_data=output)

    async def _generate_single(self, context: AgentContext, shot: dict) -> dict:
        """Generate a single video clip and poll until complete."""
        if await context.is_cancelled():
            raise RuntimeError("Pipeline cancelled")

        raw_duration = int(shot.get("duration_seconds", 5))
        # Clamp to nearest model-supported duration
        supported = settings.SEEDANCE_SUPPORTED_DURATIONS
        duration = min(supported, key=lambda s: abs(s - raw_duration))
        logger.info(f"Shot {shot['shot_idx']}: submitting generation task (duration={duration}s)")
        task = await self.generator.generate(
            image_path=shot["image_path"],
            prompt=shot["video_prompt"],
            duration=duration,
            no_audio=getattr(self, "_no_audio", True),
        )
        logger.info(f"Shot {shot['shot_idx']}: task_id={task.task_id}")

        # Poll until done — Kling v3 can take 3-5 min
        max_polls = 120  # 10 minutes max at 5s intervals
        for poll_num in range(max_polls):
            if await context.is_cancelled():
                raise RuntimeError("Pipeline cancelled")
            status = await self.generator.poll_status(task.task_id)
            if poll_num % 6 == 0:  # log every 30s
                logger.info(f"Shot {shot['shot_idx']}: poll #{poll_num} status={status.status} video_url={status.video_url}")
            if status.status == "completed":
                logger.info(f"Shot {shot['shot_idx']}: completed, video_url={status.video_url}")
                return {
                    "shot_idx": shot["shot_idx"],
                    "video_path": status.video_url or f"generated_{task.task_id}.mp4",
                    "duration_seconds": shot.get("duration_seconds", 5.0),
                    "task_id": task.task_id,
                }
            if status.status == "failed":
                raise RuntimeError(f"Video generation failed: {status.error}")
            await asyncio.sleep(5)

        raise TimeoutError(f"Video generation timed out for shot {shot['shot_idx']}")
