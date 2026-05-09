from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.models.auto_chat import AutoChatSession
from app.models.pipeline import PipelineRun
from app.routers.pipeline import _run_pipeline
from app.schemas.pipeline import PipelineRunResponse


def _serialize_run(run: PipelineRun) -> dict[str, Any]:
    return PipelineRunResponse.model_validate(run).model_dump(mode="json")


def create_replicate_video_skill(executor, db_factory, memory_service=None, mem0=None):
    async def replicate_video(
        *,
        project_id: str,
        session_id: str,
        user_id: str,
        reference_video_id: str,
        direction: str = "",
        platform: str = "generic",
        style: str = "commercial",
        generation_model: str | None = None,
        background_template_id: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        if not reference_video_id:
            raise ValueError("当前会话还没有参考视频，暂时无法复刻。")

        direction_text = (direction or "").strip()
        script = "请复刻这个参考视频。"
        if direction_text:
            script += f"\n额外要求：{direction_text}"

        async with db_factory() as db:
            session = await db.get(AutoChatSession, session_id)
            if not session or session.user_id != user_id or session.project_id != project_id:
                raise ValueError("当前会话不存在或无权访问。")

            legacy_no_audio = kwargs.get("no_audio", True)
            video_model_no_audio = kwargs.get("video_model_no_audio", legacy_no_audio)
            voiceover_no_audio = kwargs.get("voiceover_no_audio", legacy_no_audio)
            input_config: dict[str, Any] = {
                "script": script,
                "image_ids": [],
                "session_id": session_id,
                "reference_video_id": reference_video_id,
                "background_template_id": background_template_id,
                "platform": platform,
                "duration_seconds": kwargs.get("duration_seconds", 30),
                "duration_mode": kwargs.get("duration_mode", "fixed"),
                "no_audio": video_model_no_audio,
                "video_model_no_audio": video_model_no_audio,
                "voiceover_no_audio": voiceover_no_audio,
                "generation_model": generation_model or settings.VIDEO_GENERATION_MODEL,
                "style": style,
                "voice_id": kwargs.get("voice_id", "Chelsie"),
                "transition": kwargs.get("transition", "none"),
                "transition_duration": kwargs.get("transition_duration", 0.5),
                "bgm_mood": kwargs.get("bgm_mood", "none"),
                "bgm_volume": kwargs.get("bgm_volume", 0.15),
                "watermark_image_id": kwargs.get("watermark_image_id"),
            }

            run = PipelineRun(
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
                engine=getattr(executor, "engine_name", "pipeline"),
                status="pending",
                input_config=json.dumps(input_config, ensure_ascii=False),
            )
            db.add(run)
            await db.flush()

            session.current_run_id = run.id
            session.reference_video_id = reference_video_id
            session.background_template_id = background_template_id
            session.draft_script = direction_text or session.draft_script
            session.video_platform = platform
            session.status_preview = "准备执行"
            session.last_activity_at = datetime.now(timezone.utc)

            await db.commit()
            await db.refresh(run)

        asyncio.create_task(
            _run_pipeline(
                executor,
                run.id,
                project_id,
                input_config,
                user_id=user_id,
                memory_service=memory_service,
                mem0=mem0,
            )
        )

        return {
            "run_id": run.id,
            "status": "started",
            "run": _serialize_run(run),
        }

    return replicate_video
