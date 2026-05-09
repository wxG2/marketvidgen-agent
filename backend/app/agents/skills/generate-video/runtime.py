from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.security import compile_background_template
from app.core.config import settings
from app.models.background_template import BackgroundTemplate
from app.models.auto_chat import AutoChatSession
from app.models.material import Material
from app.models.pipeline import PipelineRun
from app.routers.pipeline import launch_pipeline_task
from app.schemas.pipeline import PipelineRunResponse


def _serialize_run(run: PipelineRun) -> dict[str, Any]:
    return PipelineRunResponse.model_validate(run).model_dump(mode="json")


def _looks_like_generation_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized or len(normalized) > 180:
        return False
    subject_markers = ("素材", "这些图", "这些图片", "参考图", "这个视频", "reference", "asset")
    action_markers = (
        "请",
        "帮我",
        "根据",
        "生成",
        "制作",
        "做一个",
        "设计方案",
        "营销视频",
        "generate",
        "create",
        "make",
    )
    return any(marker in normalized for marker in subject_markers) and any(
        marker in normalized for marker in action_markers
    )


def create_generate_video_skill(executor, db_factory, memory_service=None, mem0=None):
    async def generate_video(
        *,
        project_id: str,
        session_id: str,
        user_id: str,
        user_request: str = "",
        narration_script: str = "",
        script: str = "",
        image_ids: list[str],
        platform: str = "generic",
        duration_mode: str = "fixed",
        style: str = "commercial",
        no_audio: bool = True,
        video_model_no_audio: bool | None = None,
        voiceover_no_audio: bool | None = None,
        generation_model: str | None = None,
        transition: str = "none",
        bgm_mood: str = "none",
        voice_id: str = "Chelsie",
        watermark_image_id: str | None = None,
        background_template_id: str | None = None,
        duration_seconds: int | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        normalized_request = (user_request or "").strip()
        normalized_narration = (narration_script or "").strip()
        legacy_script = (script or "").strip()
        if legacy_script and not normalized_request and not normalized_narration:
            if _looks_like_generation_request(legacy_script):
                normalized_request = legacy_script
            else:
                normalized_narration = legacy_script

        normalized_image_ids = [item for item in image_ids if item]
        if not normalized_request and not normalized_narration:
            raise ValueError("缺少视频生成要求或旁白脚本，暂时还不能启动生成。")
        if not normalized_image_ids:
            raise ValueError("当前会话还没有选中素材，先选择图片再生成。")

        watermark_path = None
        background_template_name = None
        background_context = None

        async with db_factory() as db:
            session = await db.get(AutoChatSession, session_id)
            if not session or session.user_id != user_id or session.project_id != project_id:
                raise ValueError("当前会话不存在或无权访问。")

            if watermark_image_id:
                watermark = await db.get(Material, watermark_image_id)
                if watermark and watermark.user_id == user_id and watermark.file_path:
                    full_path = Path(settings.MATERIALS_ROOT) / watermark.file_path
                    if full_path.exists():
                        watermark_path = str(full_path.resolve())

            if background_template_id:
                template = await db.get(BackgroundTemplate, background_template_id)
                if template and template.user_id == user_id:
                    background_template_name = template.name
                    background_context = compile_background_template(template)

            effective_duration = duration_seconds
            if effective_duration is None:
                effective_duration = max(len(normalized_image_ids) * 5, 15)
            effective_video_model_no_audio = no_audio if video_model_no_audio is None else video_model_no_audio
            effective_voiceover_no_audio = no_audio if voiceover_no_audio is None else voiceover_no_audio

            input_config: dict[str, Any] = {
                "script": normalized_narration,
                "user_request": normalized_request,
                "image_ids": normalized_image_ids,
                "session_id": session_id,
                "background_template_id": background_template_id,
                "platform": platform,
                "duration_seconds": effective_duration,
                "duration_mode": duration_mode,
                "no_audio": effective_video_model_no_audio,
                "video_model_no_audio": effective_video_model_no_audio,
                "voiceover_no_audio": effective_voiceover_no_audio,
                "generation_model": generation_model or settings.VIDEO_GENERATION_MODEL,
                "style": style,
                "voice_id": voice_id,
                "transition": transition,
                "transition_duration": kwargs.get("transition_duration", 0.5),
                "bgm_mood": bgm_mood,
                "bgm_volume": kwargs.get("bgm_volume", 0.15),
                "watermark_image_id": watermark_image_id,
            }
            if watermark_path:
                input_config["watermark_path"] = watermark_path
            if background_template_name:
                input_config["background_template_name"] = background_template_name
            if background_context:
                input_config["background_context"] = background_context
            if kwargs.get("skip_video_generation"):
                input_config["review_prompts"] = True

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
            session.draft_script = normalized_narration or normalized_request
            session.background_template_id = background_template_id
            session.video_platform = platform
            session.video_no_audio = effective_video_model_no_audio
            session.duration_mode = duration_mode
            session.video_transition = transition
            session.bgm_mood = bgm_mood
            session.watermark_id = watermark_image_id
            session.status_preview = "准备执行"
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

        if kwargs.get("skip_video_generation"):
            return {
                "run_id": run.id,
                "status": "prompt_review_started",
                "message": "已启动镜头方案生成，右侧面板可查看进度，完成后将暂停等待确认。",
                "run": _serialize_run(run),
            }

        return {
            "run_id": run.id,
            "status": "started",
            "run": _serialize_run(run),
        }

    return generate_video
