from __future__ import annotations

import asyncio
import logging

from app.agents.base import BaseAgent, AgentContext, AgentResult, describe_exception
from app.config import settings
from app.services.video_generator import VideoGenerator

logger = logging.getLogger(__name__)


class VideoGeneratorAgent(BaseAgent):
    """Generates video clips for each shot using the image-to-video service.

    Reliability features
    --------------------
    - **Concurrency limit**: at most ``settings.MAX_CONCURRENT_SHOTS`` shots
      are sent to the external API simultaneously, preventing rate-limit errors.
    - **Per-shot timeout**: each shot's polling loop is wrapped in
      ``asyncio.wait_for`` with ``settings.VIDEO_GENERATION_TIMEOUT_SECONDS``
      so a stalled shot cannot block the whole pipeline indefinitely.
    """

    name = "video_generator"

    def __init__(self, video_generator: VideoGenerator) -> None:
        self.generator = video_generator

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        shot_prompts: list[dict] = input_data["shot_prompts"]
        self._no_audio = input_data.get("no_audio", settings.SEEDANCE_NO_AUDIO)
        regenerate_indices: list[int] | None = input_data.get("regenerate_indices")

        if regenerate_indices is not None:
            shots_to_process = [s for s in shot_prompts if s["shot_idx"] in regenerate_indices]
            existing_clips = context.artifacts.get("video_clips", {}).get("video_clips", [])
            existing_by_idx = {c["shot_idx"]: c for c in existing_clips}
        else:
            shots_to_process = shot_prompts
            existing_by_idx = {}

        # Semaphore limits how many shots hit the API simultaneously
        semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_SHOTS)

        async def _bounded_generate(shot: dict) -> dict:
            async with semaphore:
                return await self._generate_single_with_timeout(context, shot)

        tasks = [_bounded_generate(shot) for shot in shots_to_process]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_by_idx: dict[int, dict] = {}
        errors: list[str] = []
        for shot, result in zip(shots_to_process, results):
            if isinstance(result, Exception):
                errors.append(f"Shot {shot['shot_idx']}: {describe_exception(result)}")
            else:
                new_by_idx[result["shot_idx"]] = result

        # Build final clip list preserving unchanged clips
        all_indices = sorted({s["shot_idx"] for s in shot_prompts})
        video_clips: list[dict] = []
        for idx in all_indices:
            if idx in new_by_idx:
                video_clips.append(new_by_idx[idx])
            elif idx in existing_by_idx:
                video_clips.append(existing_by_idx[idx])

        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        if errors and not video_clips:
            return AgentResult(success=False, output_data={}, error="; ".join(errors))

        return AgentResult(success=True, output_data={"video_clips": video_clips})

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _generate_single_with_timeout(
        self, context: AgentContext, shot: dict
    ) -> dict:
        """Wrap _generate_single with a hard timeout."""
        timeout = settings.VIDEO_GENERATION_TIMEOUT_SECONDS
        try:
            return await asyncio.wait_for(
                self._generate_single(context, shot),
                timeout=float(timeout),
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Shot {shot['shot_idx']} timed out after {timeout}s"
            )

    async def _generate_single(self, context: AgentContext, shot: dict) -> dict:
        """Submit a generation task and poll until completion."""
        if await context.is_cancelled():
            raise RuntimeError("Pipeline cancelled")

        raw_duration = int(shot.get("duration_seconds", 5))
        supported = settings.SEEDANCE_SUPPORTED_DURATIONS
        duration = min(supported, key=lambda s: abs(s - raw_duration))

        logger.info(
            f"Shot {shot['shot_idx']}: submitting generation task (duration={duration}s)"
        )
        task = await self.generator.generate(
            image_path=shot["image_path"],
            prompt=shot["video_prompt"],
            duration=duration,
            no_audio=getattr(self, "_no_audio", True),
        )
        logger.info(f"Shot {shot['shot_idx']}: task_id={task.task_id}")

        # Poll with 5-second intervals; overall timeout is enforced by the caller
        poll_interval = 5
        poll_num = 0
        while True:
            if await context.is_cancelled():
                raise RuntimeError("Pipeline cancelled")

            status = await self.generator.poll_status(task.task_id)
            if poll_num % 6 == 0:  # log every ~30 s
                logger.info(
                    f"Shot {shot['shot_idx']}: poll #{poll_num} "
                    f"status={status.status} video_url={status.video_url}"
                )

            if status.status == "completed":
                logger.info(
                    f"Shot {shot['shot_idx']}: completed, video_url={status.video_url}"
                )
                return {
                    "shot_idx": shot["shot_idx"],
                    "video_path": status.video_url or f"generated_{task.task_id}.mp4",
                    "duration_seconds": shot.get("duration_seconds", 5.0),
                    "task_id": task.task_id,
                }

            if status.status == "failed":
                raise RuntimeError(f"Video generation failed: {status.error}")

            await asyncio.sleep(poll_interval)
            poll_num += 1
