from __future__ import annotations

import os
import uuid

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.services.video_editor_service import VideoEditorService


class VideoEditorAgent(BaseAgent):
    """Assembles video clips, audio, and subtitles into the final video."""

    name = "video_editor"

    def __init__(self, editor_service: VideoEditorService, output_dir: str = "./data/generated"):
        self.editor = editor_service
        self.output_dir = output_dir

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        video_clips_data: list[dict] = input_data.get("video_clips", [])
        audio_path: str = input_data.get("audio_path", "")
        subtitle_path: str = input_data.get("subtitle_path", "")

        # Extract file paths from clip dicts
        clip_paths = [clip["video_path"] for clip in video_clips_data]

        if not clip_paths:
            return AgentResult(success=False, output_data={}, error="No video clips to compose")

        # Generate output path
        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, f"pipeline_{uuid.uuid4().hex[:8]}.mp4")

        result = await self.editor.compose(
            video_clips=clip_paths,
            audio_path=audio_path,
            subtitle_path=subtitle_path,
            output_path=output_path,
            context_data={
                "video_clips_data": video_clips_data,
                "shot_prompts": input_data.get("shot_prompts", []),
                "duration_mode": input_data.get("duration_mode", "fixed"),
                "shot_durations": input_data.get("shot_durations", []),
                "transition": input_data.get("transition", "none"),
                "transition_duration": input_data.get("transition_duration", 0.5),
                "bgm_mood": input_data.get("bgm_mood", "none"),
                "bgm_volume": input_data.get("bgm_volume", 0.15),
                "watermark_path": input_data.get("watermark_path"),
            },
        )
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        output = {
            "final_video_path": result.output_path,
            "duration_ms": result.duration_ms,
        }
        usage_records = []
        if result.usage:
            usage_records.append({
                "provider": "qwen",
                "model_name": getattr(getattr(self.editor, "llm", None), "client", None).model if getattr(getattr(self.editor, "llm", None), "client", None) else "mock",
                "operation": "edit_plan",
                **result.usage,
            })
        return AgentResult(success=True, output_data=output, usage_records=usage_records)
