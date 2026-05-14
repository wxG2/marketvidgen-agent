from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.models.auto_chat import AutoChatSession, AutoSessionMaterialSelection
from app.models.material import Material
from app.models.pipeline import PipelineRun
from app.models.video_upload import VideoUpload
from app.routers.pipeline import launch_pipeline_task
from app.schemas.pipeline import PipelineRunResponse


def _serialize_run(run: PipelineRun) -> dict[str, Any]:
    return PipelineRunResponse.model_validate(run).model_dump(mode="json")


def _unique_video_ids(video_ids: list[str] | None) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for video_id in video_ids or []:
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        unique.append(video_id)
    return unique


def create_remix_video_skill(executor, db_factory, memory_service=None, mem0=None):
    async def remix_video(
        *,
        project_id: str,
        session_id: str,
        user_id: str,
        reference_video_ids: list[str],
        direction: str = "",
        platform: str = "generic",
        style: str = "commercial",
        generation_model: str | None = None,
        background_template_id: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        normalized_ids = _unique_video_ids(reference_video_ids)
        if len(normalized_ids) < 2:
            raise ValueError("当前会话至少需要选择 2 个参考视频，才能启动混剪。")

        direction_text = (direction or "").strip()
        script = "请基于这些参考视频做多视频混剪。"
        if direction_text:
            script += f"\n混剪要求：{direction_text}"

        async with db_factory() as db:
            session = await db.get(AutoChatSession, session_id)
            if not session or session.user_id != user_id or session.project_id != project_id:
                raise ValueError("当前会话不存在或无权访问。")

            for video_id in normalized_ids:
                upload = await db.get(VideoUpload, video_id)
                if not upload or upload.project_id != project_id:
                    raise ValueError("参考视频不存在或不属于当前项目。")

            legacy_no_audio = kwargs.get("no_audio", True)
            video_model_no_audio = kwargs.get("video_model_no_audio", legacy_no_audio)
            voiceover_no_audio = kwargs.get("voiceover_no_audio", legacy_no_audio)

            # Resolve BGM material: prefer explicitly passed ID, then first audio in session
            bgm_material_id = kwargs.get("bgm_material_id") or None
            if not bgm_material_id:
                result = await db.execute(
                    select(Material.id)
                    .join(AutoSessionMaterialSelection, AutoSessionMaterialSelection.material_id == Material.id)
                    .where(
                        AutoSessionMaterialSelection.session_id == session_id,
                        Material.user_id == user_id,
                        Material.media_type == "audio",
                    )
                    .order_by(AutoSessionMaterialSelection.sort_order.asc(), AutoSessionMaterialSelection.created_at.asc())
                    .limit(1)
                )
                bgm_material_id = result.scalars().first() or None

            remix_config = {
                "target_duration_seconds": kwargs.get("target_duration_seconds") or kwargs.get("duration_seconds", 30),
                "bgm_material_id": bgm_material_id,
                "bgm_mood": kwargs.get("bgm_mood", "none"),
                "bgm_volume": kwargs.get("bgm_volume", 0.15),
                "include_source_audio": not bool(video_model_no_audio),
                "add_voiceover": not bool(voiceover_no_audio),
                "voiceover_script": kwargs.get("voiceover_script"),
            }
            input_config: dict[str, Any] = {
                "script": script,
                "image_ids": [],
                "session_id": session_id,
                "reference_video_id": normalized_ids[0],
                "reference_video_ids": normalized_ids,
                "remix_config": remix_config,
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
            session.reference_video_id = normalized_ids[0]
            session.background_template_id = background_template_id
            session.draft_script = direction_text or session.draft_script
            session.video_platform = platform
            session.status_preview = "准备混剪"
            session.last_activity_at = datetime.now(timezone.utc)

            await db.commit()
            await db.refresh(run)

        launch_pipeline_task(
            executor,
            run.id,
            project_id,
            input_config,
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )

        return {
            "run_id": run.id,
            "status": "started",
            "message": "已启动多视频混剪流程，完成规划后会暂停等待确认。",
            "run": _serialize_run(run),
        }

    return remix_video
