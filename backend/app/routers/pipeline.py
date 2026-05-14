from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from pathlib import Path

from app.core.security import (
    compile_background_template,
    get_auto_chat_session_for_user,
    get_background_template_for_user,
    get_current_user,
    get_material_for_user,
    get_pipeline_run_for_user,
    get_project_for_user,
    get_social_account_for_user,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.auto_chat import AutoChatSession, AutoSessionMaterialSelection
from app.models.material import Material
from app.models.pipeline import PipelineRun, AgentExecution
from app.models.repository_asset import RepositoryAsset
from app.models.social_account import SocialAccount
from app.models.video_delivery import VideoDelivery
from app.models.video_upload import VideoUpload
from app.models.user import User
from app.schemas.pipeline import (
    PipelineCreateRequest,
    PipelineRunResponse,
    AgentExecutionResponse,
    PipelineUsageResponse,
    PipelineDeliveryResponse,
    PlatformPreviewCardResponse,
    VideoDeliveryResponse,
    PipelineArtifactResponse,
    DeliveryActionRequest,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
    PrefightCheckRequest,
    PrefightCheckResponse,
    ConfirmPlanRequest,
    ConfirmRemixRequest,
    ConfirmPromptReviewRequest,
    RetryShotRequest,
    EstimateCostRequest,
    EstimateCostResponse,
)
from app.schemas.social_account import PublishDraftResponse, SocialAccountResponse
from app.agents.pipeline import PipelineExecutor
from app.services.video_delivery import (
    build_platform_preview_cards,
    derive_delivery_title,
    publish_video_to_douyin,
    save_video_to_repository,
    serialize_delivery,
)
from app.services.pipeline_artifact_repository import serialize_repository_asset
from app.services.social_accounts import serialize_social_account
from app.services.usage_service import UsageRecorder
from app.db.session import async_session

# ---------------------------------------------------------------------------
# In-process pipeline task registry
# Maps run_id -> asyncio.Task so cancel_pipeline can kill the in-flight task.
# ---------------------------------------------------------------------------
_pipeline_tasks: dict[str, asyncio.Task] = {}


def launch_pipeline_task(
    executor,
    run_id: str,
    project_id: str,
    input_config: dict,
    *,
    user_id: str | None = None,
    memory_service=None,
    mem0=None,
    rag_service=None,
) -> asyncio.Task:
    """Create and register a background pipeline task."""
    task = asyncio.create_task(
        _run_pipeline(executor, run_id, project_id, input_config,
                      user_id=user_id, memory_service=memory_service, mem0=mem0,
                      rag_service=rag_service)
    )
    _pipeline_tasks[run_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        _pipeline_tasks.pop(run_id, None)

    task.add_done_callback(_cleanup)
    return task


SCRIPT_GENERATION_PROMPT = """你是一名短视频脚本创作专家。用户会提供一组图片素材，请你仔细观察每张图片的内容、场景、氛围，然后为这些图片撰写一段适合短视频旁白/口播的中文脚本。

要求：
- 脚本应该是连贯的一段话，适合 TTS 口播朗读
- 语言生动、有感染力，适合营销/种草/品牌宣传类短视频
- 根据图片数量控制脚本长度，每张图片大约对应1-2句话
- 不要输出分镜编号或拍摄指导，只输出纯旁白文案
- 如果图片内容涉及商业场景（门店、产品等），要突出卖点和氛围"""


_launch_locks: dict[str, asyncio.Lock] = {}


def _get_launch_lock(lock_key: str) -> asyncio.Lock:
    lock = _launch_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _launch_locks[lock_key] = lock
    return lock


def _unique_video_ids(video_ids: list[str] | None) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for video_id in video_ids or []:
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        unique.append(video_id)
    return unique


async def _validate_pipeline_reference_videos(
    db: AsyncSession,
    *,
    project_id: str,
    reference_video_id: str | None,
    reference_video_ids: list[str],
) -> list[str]:
    candidate_ids = _unique_video_ids(
        ([reference_video_id] if reference_video_id else []) + reference_video_ids
    )
    for video_id in candidate_ids:
        upload = await db.get(VideoUpload, video_id)
        if not upload or upload.project_id != project_id:
            raise HTTPException(status_code=400, detail="Reference video does not belong to this project")
    return _unique_video_ids(reference_video_ids)


async def _validate_pipeline_bgm_material(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str | None,
    bgm_material_id: str,
) -> None:
    material = await db.get(Material, bgm_material_id)
    if not material or material.user_id != user_id:
        raise HTTPException(status_code=400, detail="BGM material does not belong to this user")
    if not str(material.media_type or "").startswith("audio"):
        raise HTTPException(status_code=400, detail="BGM material must be an audio file")


async def _first_session_audio_material_id(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str | None,
) -> str | None:
    if session_id:
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
        material_id = result.scalars().first()
        if material_id:
            return material_id
    result = await db.execute(
        select(Material.id)
        .where(Material.user_id == user_id, Material.media_type == "audio")
        .order_by(Material.indexed_at.desc())
        .limit(1)
    )
    return result.scalars().first()


def _build_replication_narration_script(replication_plan: dict, fallback_script: str) -> str:
    if not isinstance(replication_plan, dict):
        return fallback_script

    audio_design = replication_plan.get("audio_design")
    narration_notes = ""
    if isinstance(audio_design, dict):
        raw_notes = audio_design.get("narration_notes")
        if isinstance(raw_notes, str):
            narration_notes = raw_notes.strip()

    shots = replication_plan.get("shots")
    shot_lines: list[str] = []
    if isinstance(shots, list):
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            description = str(shot.get("description") or "").strip()
            if description:
                shot_lines.append(description)

    if narration_notes and shot_lines:
        return f"{narration_notes}\n\n" + "\n".join(shot_lines)
    if shot_lines:
        return "\n".join(shot_lines)
    if narration_notes:
        return narration_notes
    return fallback_script


def get_pipeline_router(executor: PipelineExecutor) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["pipeline"])

    def _serialize_run(run: PipelineRun) -> dict:
        return PipelineRunResponse.model_validate(run).model_dump(mode="json")

    async def _get_delivery_records(db: AsyncSession, user_id: str, run_id: str) -> list[dict]:
        result = await db.execute(
            select(VideoDelivery)
            .where(VideoDelivery.user_id == user_id, VideoDelivery.pipeline_run_id == run_id)
            .order_by(VideoDelivery.created_at.desc())
        )
        return [serialize_delivery(record) for record in result.scalars().all()]

    @router.post("/projects/{project_id}/pipeline", response_model=PipelineRunResponse)
    async def launch_pipeline(
        project_id: str,
        req: PipelineCreateRequest,
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Launch a new pipeline run as a background task.

        Deduplication: if a pending or running pipeline already exists for this
        project, the existing run is returned instead of creating a duplicate.
        """
        await get_project_for_user(db, user.id, project_id)
        session: AutoChatSession | None = None
        if req.session_id:
            session = await get_auto_chat_session_for_user(db, user.id, project_id, req.session_id)
        lock_key = req.session_id or project_id

        async with _get_launch_lock(lock_key):
            reference_video_ids = await _validate_pipeline_reference_videos(
                db,
                project_id=project_id,
                reference_video_id=req.reference_video_id,
                reference_video_ids=req.reference_video_ids,
            )
            # --- deduplication check ---
            dedupe_conditions = [
                PipelineRun.project_id == project_id,
                PipelineRun.user_id == user.id,
                PipelineRun.status.in_(["pending", "running"]),
            ]
            if req.session_id:
                dedupe_conditions.append(PipelineRun.session_id == req.session_id)
            else:
                dedupe_conditions.append(PipelineRun.session_id.is_(None))
            existing_result = await db.execute(
                select(PipelineRun)
                .where(*dedupe_conditions)
                .order_by(PipelineRun.created_at.desc())
                .limit(1)
            )
            existing_run = existing_result.scalars().first()
            if existing_run is not None:
                return existing_run

            # --- resolve watermark image path if provided ---
            watermark_path = None
            if req.watermark_image_id:
                wm_material = await get_material_for_user(db, user.id, req.watermark_image_id)
                if wm_material and wm_material.file_path:
                    wm_full = Path(settings.MATERIALS_ROOT) / wm_material.file_path
                    if wm_full.exists():
                        watermark_path = str(wm_full.resolve())

            background_template = None
            if req.background_template_id:
                background_template = await get_background_template_for_user(db, user.id, req.background_template_id)

            # --- create new run ---
            input_config = req.model_dump()
            input_config["reference_video_ids"] = reference_video_ids
            remix_config = input_config.get("remix_config")
            if isinstance(remix_config, dict) and len(reference_video_ids) >= 2:
                bgm_material_id = str(remix_config.get("bgm_material_id") or "").strip()
                if bgm_material_id:
                    await _validate_pipeline_bgm_material(
                        db,
                        user_id=user.id,
                        session_id=req.session_id,
                        bgm_material_id=bgm_material_id,
                    )
                elif req.session_id:
                    first_audio_id = await _first_session_audio_material_id(
                        db,
                        user_id=user.id,
                        session_id=req.session_id,
                    )
                    if first_audio_id:
                        remix_config["bgm_material_id"] = first_audio_id
            input_config["video_model_no_audio"] = (
                req.video_model_no_audio if req.video_model_no_audio is not None else req.no_audio
            )
            input_config["voiceover_no_audio"] = (
                req.voiceover_no_audio if req.voiceover_no_audio is not None else req.no_audio
            )
            input_config["no_audio"] = input_config["video_model_no_audio"]
            if watermark_path:
                input_config["watermark_path"] = watermark_path
            if background_template:
                input_config["background_template_name"] = background_template.name
                input_config["background_context"] = compile_background_template(background_template)

            run = PipelineRun(
                user_id=user.id,
                project_id=project_id,
                session_id=req.session_id,
                engine=getattr(executor, "engine_name", "pipeline"),
                status="pending",
                input_config=json.dumps(input_config, ensure_ascii=False), # 创建生成任务
            )
            db.add(run)
            await db.flush()
            if session is not None:
                session.current_run_id = run.id
                session.reference_video_id = req.reference_video_id or (input_config["reference_video_ids"][0] if input_config["reference_video_ids"] else None)
                session.background_template_id = req.background_template_id
                session.draft_script = req.script
                session.video_platform = req.platform
                session.video_no_audio = input_config["video_model_no_audio"]
                session.duration_mode = req.duration_mode
                session.video_transition = req.transition
                session.bgm_mood = req.bgm_mood
                session.watermark_id = req.watermark_image_id
                session.status_preview = "准备执行"
                session.last_activity_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(run)

        # Fire and forget — pipeline runs in background
        launch_pipeline_task(
            executor, run.id, project_id, input_config,
            user_id=user.id,
            memory_service=getattr(request.app.state, "agent_memory", None),
            mem0=getattr(request.app.state, "mem0", None),
            rag_service=getattr(request.app.state, "rag", None),
        )

        return _serialize_run(run)

    @router.get("/projects/{project_id}/pipelines", response_model=list[PipelineRunResponse])
    async def list_pipelines(
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """List all pipeline runs for a project."""
        await get_project_for_user(db, user.id, project_id)
        result = await db.execute(
            select(PipelineRun)
            .where(PipelineRun.project_id == project_id, PipelineRun.user_id == user.id)
            .order_by(PipelineRun.created_at.desc())
        )
        return [_serialize_run(run) for run in result.scalars().all()]

    @router.get("/projects/{project_id}/pipeline/{run_id}", response_model=PipelineRunResponse)
    async def get_pipeline_run(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Get pipeline run status."""
        await get_project_for_user(db, user.id, project_id)
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        return _serialize_run(run)

    @router.get("/projects/{project_id}/pipeline/{run_id}/agents", response_model=list[AgentExecutionResponse])
    async def get_agent_executions(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """List all agent executions for a pipeline run."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")

        result = await db.execute(
            select(AgentExecution)
            .where(AgentExecution.pipeline_run_id == run_id)
            .order_by(AgentExecution.created_at.asc())
        )
        executions = result.scalars().all()
        items = []
        for execution in executions:
            items.append(
                AgentExecutionResponse(
                    id=execution.id,
                    agent_name=execution.agent_name,
                    status=execution.status,
                    attempt_number=execution.attempt_number,
                    input_data=json.loads(execution.input_data) if execution.input_data else None,
                    output_data=json.loads(execution.output_data) if execution.output_data else None,
                    duration_ms=execution.duration_ms,
                    error_message=execution.error_message,
                    progress_text=execution.progress_text,
                    created_at=execution.created_at,
                    completed_at=execution.completed_at,
                )
            )
        return items

    @router.get("/projects/{project_id}/pipeline/{run_id}/artifacts", response_model=list[PipelineArtifactResponse])
    async def get_pipeline_artifacts(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """List persisted intermediate artifacts for prompt/audio/video agents."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")

        result = await db.execute(
            select(RepositoryAsset)
            .where(
                RepositoryAsset.user_id == user.id,
                RepositoryAsset.project_id == project_id,
                RepositoryAsset.pipeline_run_id == run_id,
            )
            .order_by(RepositoryAsset.created_at.asc(), RepositoryAsset.asset_key.asc())
        )
        return [serialize_repository_asset(asset) for asset in result.scalars().all()]

    @router.get("/projects/{project_id}/pipeline/{run_id}/usage", response_model=PipelineUsageResponse)
    async def get_pipeline_usage(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        recorder = UsageRecorder(async_session)
        return await recorder.get_run_summary(run_id)

    @router.get("/projects/{project_id}/pipeline/{run_id}/delivery", response_model=PipelineDeliveryResponse)
    async def get_pipeline_delivery(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if not run.final_video_path:
            raise HTTPException(status_code=400, detail="Pipeline has no final video yet")
        accounts_result = await db.execute(
            select(SocialAccount)
            .where(SocialAccount.user_id == user.id, SocialAccount.platform == "douyin")
            .order_by(SocialAccount.is_default.desc(), SocialAccount.updated_at.desc())
        )
        connected_accounts = [SocialAccountResponse(**serialize_social_account(item)) for item in accounts_result.scalars().all()]
        recommended_account = connected_accounts[0] if connected_accounts else None
        records = [VideoDeliveryResponse(**item) for item in await _get_delivery_records(db, user.id, run_id)]
        latest_publish_draft = None
        for record in records:
            if record.platform == "douyin" and record.action_type == "publish" and record.draft_payload:
                latest_publish_draft = PublishDraftResponse(**record.draft_payload)
                break
        return {
            "previews": [PlatformPreviewCardResponse(**item) for item in build_platform_preview_cards(run)],
            "records": records,
            "connected_social_accounts": connected_accounts,
            "recommended_publish_account": recommended_account,
            "latest_publish_draft": latest_publish_draft,
        }

    @router.get("/projects/{project_id}/pipeline/{run_id}/final-video")
    async def get_pipeline_final_video(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if not run.final_video_path:
            raise HTTPException(status_code=404, detail="Pipeline has no final video yet")

        video_path = Path(run.final_video_path).resolve()
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Final video file not found")
        return FileResponse(video_path, media_type="video/mp4", filename=f"{run.id}.mp4")

    @router.post("/projects/{project_id}/pipeline/{run_id}/delivery/save", response_model=VideoDeliveryResponse)
    async def save_pipeline_video(
        project_id: str,
        run_id: str,
        data: DeliveryActionRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if not run.final_video_path:
            raise HTTPException(status_code=400, detail="Pipeline has no final video yet")
        try:
            record = await save_video_to_repository(
                db,
                user_id=user.id,
                project_id=project_id,
                run=run,
                title=data.title or derive_delivery_title(run),
                description=data.description,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return VideoDeliveryResponse(**serialize_delivery(record))

    @router.post("/projects/{project_id}/pipeline/{run_id}/delivery/publish-douyin", response_model=VideoDeliveryResponse)
    async def publish_pipeline_video_to_douyin(
        project_id: str,
        run_id: str,
        data: DeliveryActionRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if not run.final_video_path:
            raise HTTPException(status_code=400, detail="Pipeline has no final video yet")
        if not data.social_account_id:
            raise HTTPException(status_code=400, detail="请选择已连接的抖音账号后再发布")
        social_account = await get_social_account_for_user(db, user.id, data.social_account_id)
        try:
            record = await publish_video_to_douyin(
                db,
                user_id=user.id,
                project_id=project_id,
                run=run,
                social_account=social_account,
                title=data.title or derive_delivery_title(run),
                description=data.description,
                hashtags=data.hashtags,
                visibility=data.visibility,
                cover_title=data.cover_title,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"抖音发布失败：{exc}") from exc
        return VideoDeliveryResponse(**serialize_delivery(record))

    @router.get("/projects/{project_id}/pipeline/{run_id}/stream")
    async def stream_pipeline(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
    ):
        """SSE stream that pushes run status + agent executions every 2s until terminal."""

        log = logging.getLogger(__name__)

        async def _event_generator() -> AsyncGenerator[dict, None]:
            while True:
                try:
                    async with async_session() as session:
                        run = await session.get(PipelineRun, run_id)
                        if not run or run.project_id != project_id or run.user_id != user.id:
                            yield {"event": "error", "data": json.dumps({"detail": "not found"})}
                            return

                        run_data = _serialize_run(run)

                        result = await session.execute(
                            select(AgentExecution)
                            .where(AgentExecution.pipeline_run_id == run_id)
                            .order_by(AgentExecution.created_at.asc())
                        )
                        execs = result.scalars().all()
                        agents_data = [
                            AgentExecutionResponse(
                                id=e.id,
                                agent_name=e.agent_name,
                                status=e.status,
                                attempt_number=e.attempt_number,
                                input_data=json.loads(e.input_data) if e.input_data else None,
                                output_data=json.loads(e.output_data) if e.output_data else None,
                                duration_ms=e.duration_ms,
                                error_message=e.error_message,
                                progress_text=e.progress_text,
                                created_at=e.created_at,
                                completed_at=e.completed_at,
                            ).model_dump(mode="json")
                            for e in execs
                        ]

                        payload = json.dumps({"run": run_data, "agents": agents_data})
                        yield {"event": "update", "data": payload}

                        if run.status in ("completed", "failed", "cancelled"):
                            yield {"event": "done", "data": payload}
                            return
                except Exception as exc:
                    log.warning(f"SSE stream error for run {run_id}: {exc}")
                    yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
                    return

                await asyncio.sleep(2)

        return EventSourceResponse(_event_generator())

    @router.post("/projects/{project_id}/pipeline/{run_id}/retry-agent")
    async def retry_failed_agent(
        project_id: str,
        run_id: str,
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Re-run only the last failed agent, reconstructing context from prior successful executions."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status != "failed":
            raise HTTPException(status_code=400, detail="Only failed pipelines can be retried")

        # Find the failed agent execution (latest)
        result = await db.execute(
            select(AgentExecution)
            .where(
                AgentExecution.pipeline_run_id == run_id,
                AgentExecution.status == "failed",
            )
            .order_by(AgentExecution.created_at.desc())
            .limit(1)
        )
        failed_exec = result.scalars().first()
        if not failed_exec:
            raise HTTPException(status_code=400, detail="No failed agent execution found")

        # Get all successful executions to rebuild context
        result = await db.execute(
            select(AgentExecution)
            .where(
                AgentExecution.pipeline_run_id == run_id,
                AgentExecution.status == "completed",
            )
            .order_by(AgentExecution.created_at.asc())
        )
        completed_execs = result.scalars().all()

        # Rebuild artifacts from completed executions
        artifacts: dict = {}
        agent_to_artifact_key = {
            "orchestrator": "orchestrator_plan",
            "prompt_engineer": "prompt_plan",
            "audio_subtitle": "audio",
            "video_generator": "video_clips",
            "video_editor": "final_video",
        }
        for exec_record in completed_execs:
            if exec_record.output_data:
                key = agent_to_artifact_key.get(exec_record.agent_name)
                if key:
                    artifacts[key] = json.loads(exec_record.output_data)

        # Reconstruct input_data for the failed agent
        failed_input = json.loads(failed_exec.input_data) if failed_exec.input_data else {}
        input_config = json.loads(run.input_config) if run.input_config else {}

        # Reset pipeline status
        run.status = "running"
        run.current_agent = failed_exec.agent_name
        run.error_message = None
        run.retry_count += 1
        run.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(run)

        # Fire background task to retry the failed agent
        asyncio.create_task(
            _retry_agent(
                executor, run.id, project_id, failed_exec.agent_name,
                failed_input, input_config, artifacts,
                user_id=user.id,
                memory_service=getattr(request.app.state, "agent_memory", None),
                mem0=getattr(request.app.state, "mem0", None),
            )
        )

        return run

    @router.post("/projects/{project_id}/pipeline/{run_id}/cancel")
    async def cancel_pipeline(
        project_id: str,
        run_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Cancel a running pipeline."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status not in ("pending", "running", "waiting_confirmation", "waiting_prompt_review", "waiting_remix_confirmation"):
            raise HTTPException(status_code=400, detail=f"Cannot cancel pipeline in '{run.status}' status")

        run.status = "cancelled"
        run.current_agent = None
        run.updated_at = datetime.now(timezone.utc)
        await db.execute(
            update(AgentExecution)
            .where(
                AgentExecution.pipeline_run_id == run_id,
                AgentExecution.status.in_(["pending", "running"]),
            )
            .values(
                status="cancelled",
                error_message="Pipeline cancelled",
                completed_at=run.updated_at,
            )
        )
        if run.session_id:
            session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
            session.status_preview = "已取消"
            session.last_activity_at = run.updated_at
        await db.commit()

        task = _pipeline_tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()

        return {"status": "cancelled"}

    @router.post("/projects/{project_id}/pipeline/{run_id}/confirm-plan")
    async def confirm_replication_plan(
        project_id: str,
        run_id: str,
        req: ConfirmPlanRequest,
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Confirm or adjust the replication plan produced by the replication planner."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status != "waiting_confirmation":
            raise HTTPException(status_code=400, detail=f"Pipeline is not waiting for confirmation (status: {run.status})")

        input_config = json.loads(run.input_config) if run.input_config else {}

        if not req.approved:
            if req.adjustments:
                # Re-run the planner with user feedback.
                run.status = "running"
                run.current_agent = "replication_planner"
                run.updated_at = datetime.now(timezone.utc)
                # Append adjustment feedback to input config
                adjusted_config = {**input_config, "adjustment_feedback": req.adjustments}
                run.input_config = json.dumps(adjusted_config, ensure_ascii=False)
                if run.session_id:
                    session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
                    session.status_preview = "重新生成方案中"
                    session.last_activity_at = datetime.now(timezone.utc)
                await db.commit()
                launch_pipeline_task(
                    executor, run.id, project_id, adjusted_config,
                    user_id=user.id,
                    memory_service=getattr(request.app.state, "agent_memory", None),
                    mem0=getattr(request.app.state, "mem0", None),
                    rag_service=getattr(request.app.state, "rag", None),
                )
                return {"status": "rerunning", "message": "正在根据反馈重新生成复刻方案"}
            else:
                # Cancel the run
                run.status = "cancelled"
                run.updated_at = datetime.now(timezone.utc)
                if run.session_id:
                    session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
                    session.status_preview = "已取消"
                    session.last_activity_at = run.updated_at
                await db.commit()
                return {"status": "cancelled"}

        # Approved — convert replication plan to standard orchestrator_plan and resume.
        # Rebuild artifacts from the completed replication planner execution.
        result = await db.execute(
            select(AgentExecution)
            .where(
                AgentExecution.pipeline_run_id == run_id,
                AgentExecution.agent_name.in_(["replication_planner", "orchestrator"]),
                AgentExecution.status == "completed",
            )
            .order_by(AgentExecution.created_at.desc())
            .limit(1)
        )
        orch_exec = result.scalars().first()
        if not orch_exec or not orch_exec.output_data:
            raise HTTPException(status_code=400, detail="No replication planner output found")

        orch_output = json.loads(orch_exec.output_data)
        replication_plan = orch_output.get("replication_plan", {})
        narration_script = _build_replication_narration_script(
            replication_plan,
            orch_output.get("script", input_config.get("script", "")),
        )

        # Convert replication plan shots to standard orchestrator_plan format
        supported_durations = settings.SEEDANCE_SUPPORTED_DURATIONS
        from app.agents.stages.orchestrator import _snap_to_supported

        standard_shots = []
        for shot in replication_plan.get("shots", []):
            raw_dur = shot.get("suggested_duration_seconds", 5)
            clamped_dur = _snap_to_supported(raw_dur, supported_durations)
            standard_shots.append({
                "shot_idx": shot["shot_idx"],
                "image_path": shot.get("material_image_path") or shot.get("reference_frame_path", ""),
                "script_segment": shot.get("description", ""),
                "duration_seconds": clamped_dur,
            })

        orchestrator_plan = {
            "shots": standard_shots,
            "video_type": replication_plan.get("overall_style", "commercial"),
            "platform": orch_output.get("platform", input_config.get("platform", "generic")),
            "duration_seconds": sum(s["duration_seconds"] for s in standard_shots),
            "style": orch_output.get("style", input_config.get("style", "commercial")),
            "voice_config": orch_output.get("voice_config", {"voice_id": "default", "speed": 1.0}),
            "script": narration_script,
            "background_context": input_config.get("background_context", ""),
            "replication_plan": replication_plan,
        }

        # Update run status
        run.status = "running"
        run.current_agent = "prompt_engineer"
        run.updated_at = datetime.now(timezone.utc)
        if run.session_id:
            session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
            session.status_preview = "生成中"
            session.last_activity_at = run.updated_at
        await db.commit()

        # Resume pipeline from prompt_engineer
        _task = asyncio.create_task(
            _continue_from_confirmation(
                executor, run.id, project_id, input_config, orchestrator_plan,
                user_id=user.id,
                memory_service=getattr(request.app.state, "agent_memory", None),
                mem0=getattr(request.app.state, "mem0", None),
            )
        )
        _pipeline_tasks[run.id] = _task
        _task.add_done_callback(lambda t: _pipeline_tasks.pop(run.id, None))
        return {"status": "confirmed", "message": "复刻方案已确认，继续生成中"}

    @router.post("/projects/{project_id}/pipeline/{run_id}/confirm-remix")
    async def confirm_remix_plan(
        project_id: str,
        run_id: str,
        req: ConfirmRemixRequest,
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Confirm or adjust the remix plan produced by the remix planner."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status != "waiting_remix_confirmation":
            raise HTTPException(status_code=400, detail=f"Pipeline is not waiting for remix confirmation (status: {run.status})")

        input_config = json.loads(run.input_config) if run.input_config else {}

        if not req.approved:
            if req.adjustments:
                adjusted_config = {**input_config, "remix_adjustment_feedback": req.adjustments}
                snapshot = json.loads(run.artifacts_snapshot or "{}")
                snapshot.pop("audio", None)
                run.status = "running"
                run.current_agent = "remix_planner"
                run.input_config = json.dumps(adjusted_config, ensure_ascii=False)
                run.artifacts_snapshot = json.dumps(snapshot, ensure_ascii=False)
                run.updated_at = datetime.now(timezone.utc)
                if run.session_id:
                    session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
                    session.status_preview = "重新生成混剪方案中"
                    session.last_activity_at = run.updated_at
                await db.commit()
                launch_pipeline_task(
                    executor, run.id, project_id, adjusted_config,
                    user_id=user.id,
                    memory_service=getattr(request.app.state, "agent_memory", None),
                    mem0=getattr(request.app.state, "mem0", None),
                    rag_service=getattr(request.app.state, "rag", None),
                )
                return {"status": "rerunning", "message": "正在根据反馈重新生成混剪方案"}

            run.status = "cancelled"
            run.updated_at = datetime.now(timezone.utc)
            if run.session_id:
                session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
                session.status_preview = "已取消"
                session.last_activity_at = run.updated_at
            await db.commit()
            return {"status": "cancelled"}

        snapshot: dict = json.loads(run.artifacts_snapshot or "{}")
        remix_plan = snapshot.get("remix_plan")
        if not isinstance(remix_plan, dict):
            remix_output = snapshot.get("remix_planner") if isinstance(snapshot.get("remix_planner"), dict) else {}
            remix_plan = remix_output.get("remix_plan")
        if not isinstance(remix_plan, dict) or not remix_plan.get("segments"):
            raise HTTPException(status_code=400, detail="No remix plan found")

        _apply_remix_edits(remix_plan, req)
        snapshot["remix_plan"] = remix_plan
        run.artifacts_snapshot = json.dumps(snapshot, ensure_ascii=False)
        run.status = "running"
        run.current_agent = "remix_assembler"
        run.updated_at = datetime.now(timezone.utc)
        if run.session_id:
            session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
            session.status_preview = "正在组装混剪视频"
            session.last_activity_at = run.updated_at
        await db.commit()

        _task = asyncio.create_task(
            _continue_from_remix_confirmation(
                executor, run.id, project_id, input_config, snapshot,
                user_id=user.id,
                memory_service=getattr(request.app.state, "agent_memory", None),
                mem0=getattr(request.app.state, "mem0", None),
            )
        )
        _pipeline_tasks[run.id] = _task
        _task.add_done_callback(lambda t: _pipeline_tasks.pop(run.id, None))
        return {"status": "confirmed", "message": "混剪方案已确认，正在组装视频"}

    @router.post("/projects/{project_id}/pipeline/{run_id}/confirm-prompt-review")
    async def confirm_prompt_review(
        project_id: str,
        run_id: str,
        request: Request,
        req: ConfirmPromptReviewRequest = ConfirmPromptReviewRequest(),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Confirm the prompt engineer's shot plan and continue the pipeline.

        Optionally accepts ``edited_shots`` to patch specific shots before
        continuing — only the fields that are not None are applied.
        """
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status != "waiting_prompt_review":
            raise HTTPException(
                status_code=400,
                detail=f"Pipeline is not waiting for prompt review (status: {run.status})",
            )

        input_config = json.loads(run.input_config) if run.input_config else {}

        # Apply user edits to the saved prompt_plan checkpoint
        edited_shots = req.edited_shots if req else None
        if edited_shots:
            snapshot: dict = json.loads(run.artifacts_snapshot or "{}")
            prompt_plan: dict = snapshot.get("prompt_plan", {})
            shots: list[dict] = prompt_plan.get("shot_prompts", [])
            edits_by_idx = {e.shot_idx: e for e in edited_shots}
            for shot in shots:
                edit = edits_by_idx.get(shot.get("shot_idx"))
                if edit is None:
                    continue
                if edit.script_segment is not None:
                    shot["script_segment"] = edit.script_segment
                if edit.video_prompt is not None:
                    shot["video_prompt"] = edit.video_prompt
                if edit.duration_seconds is not None and edit.duration_seconds >= 0.5:
                    from app.agents.stages.prompt_engineer import (
                        _generation_duration_for, _snap_to_half_second, _duration_range_label,
                    )
                    dur = _snap_to_half_second(edit.duration_seconds)
                    shot["duration_seconds"] = dur
                    shot["generation_duration_seconds"] = _generation_duration_for(dur)
                    shot["duration_range_label"] = _duration_range_label(dur)
            run.artifacts_snapshot = json.dumps(snapshot)

        run.status = "running"
        run.current_agent = "audio_subtitle"
        run.updated_at = datetime.now(timezone.utc)
        if run.session_id:
            session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
            session.status_preview = "生成中"
            session.last_activity_at = run.updated_at
        await db.commit()

        _task = asyncio.create_task(
            _continue_from_prompt_review(
                executor, run.id, project_id, input_config,
                user_id=user.id,
                memory_service=getattr(request.app.state, "agent_memory", None),
                mem0=getattr(request.app.state, "mem0", None),
            )
        )
        _pipeline_tasks[run.id] = _task
        _task.add_done_callback(lambda t: _pipeline_tasks.pop(run.id, None))
        return {"status": "confirmed", "message": "镜头方案已确认，继续生成视频"}

    @router.post("/projects/{project_id}/pipeline/{run_id}/retry-shot")
    async def retry_shot(
        project_id: str,
        run_id: str,
        req: RetryShotRequest,
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Re-generate only the specified shot indices; reuse all other completed clips."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status not in ("completed", "failed"):
            raise HTTPException(
                status_code=400,
                detail=f"Shot retry requires a terminal run (status: {run.status})",
            )
        if not req.shot_indices:
            raise HTTPException(status_code=400, detail="shot_indices must be non-empty")

        input_config = json.loads(run.input_config) if run.input_config else {}
        saved_artifacts: dict = json.loads(run.artifacts_snapshot or "{}")

        # Inject regenerate_indices so VideoGeneratorAgent only reruns those shots
        retry_input_config = dict(input_config)
        retry_input_config["regenerate_indices"] = req.shot_indices

        run.status = "running"
        run.current_agent = "video_generator"
        run.error_message = None
        run.updated_at = datetime.now(timezone.utc)
        await db.commit()

        _task = asyncio.create_task(
            _retry_shots_task(
                executor, run.id, project_id, retry_input_config, saved_artifacts,
                user_id=user.id,
                memory_service=getattr(request.app.state, "agent_memory", None),
                mem0=getattr(request.app.state, "mem0", None),
            )
        )
        _pipeline_tasks[run.id] = _task
        _task.add_done_callback(lambda t: _pipeline_tasks.pop(run.id, None))
        return {"status": "running", "message": f"重新生成镜头 {req.shot_indices}"}

    @router.post(
        "/projects/{project_id}/pipeline/estimate-cost",
        response_model=EstimateCostResponse,
    )
    async def estimate_cost(
        project_id: str,
        req: EstimateCostRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Return a rough cost estimate for the given shot plan and settings."""
        await get_project_for_user(db, user.id, project_id)
        return _compute_cost_estimate(req)

    @router.post("/projects/{project_id}/generate-script", response_model=ScriptGenerateResponse)
    async def generate_script(
        project_id: str,
        req: ScriptGenerateRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Analyze selected images and generate a suitable video script."""
        await get_project_for_user(db, user.id, project_id)
        result = await db.execute(
            select(Material).where(Material.user_id == user.id, Material.id.in_(req.image_ids))
        )
        materials_list = result.scalars().all()
        root = Path(settings.MATERIALS_ROOT)
        image_paths = [str((root / m.file_path).resolve()) for m in materials_list if m.file_path]

        if not image_paths:
            raise HTTPException(status_code=400, detail="No valid images found")

        llm = executor.orchestrator.llm
        schema = {
            "name": "script_output",
            "schema": {
                "type": "object",
                "properties": {
                    "script": {"type": "string"},
                },
                "required": ["script"],
            },
        }
        try:
            output, _ = await llm.generate_structured(
                system_prompt=SCRIPT_GENERATION_PROMPT,
                user_prompt=f"请根据以下 {len(image_paths)} 张图片素材撰写短视频脚本。",
                schema=schema,
                image_paths=image_paths,
            )
            script_text = output.get("script", "")
            if not script_text:
                raise ValueError("LLM returned empty script")
            return ScriptGenerateResponse(script=script_text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"脚本生成失败：{e}")

    @router.post("/projects/{project_id}/preflight-check", response_model=PrefightCheckResponse)
    async def preflight_check(
        project_id: str,
        req: PrefightCheckRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Pre-launch check: estimate audio duration vs available video capacity."""
        await get_project_for_user(db, user.id, project_id)
        from app.core.config import settings as _settings

        script = req.script
        image_count = req.image_count
        duration_mode = req.duration_mode
        duration_seconds = req.duration_seconds
        supported = _settings.SEEDANCE_SUPPORTED_DURATIONS
        max_d = max(supported)

        # Estimate TTS audio duration (~4 Chinese chars/sec, ~8 English chars/sec)
        cn_chars = sum(1 for c in script if '\u4e00' <= c <= '\u9fff')
        other_chars = len(script) - cn_chars
        estimated_audio_s = cn_chars / 4.0 + other_chars / 8.0

        # Max video duration achievable with current images
        max_video_s = image_count * max_d

        # In fixed mode, max video is the user's target
        if duration_mode == "fixed":
            effective_video_s = duration_seconds
        else:
            effective_video_s = max_video_s

        # How many images needed to cover the audio
        import math
        recommended_count = max(math.ceil(estimated_audio_s / max_d), 1)

        # Rough token cost estimate:
        # - Orchestrator: ~2k tokens per image (vision analysis)
        # - Prompt Engineer: ~1k tokens per shot
        # - Video Editor: ~500 tokens for edit plan
        # - TTS/Audio: not token-based, negligible
        estimated_tokens = (
            image_count * 2000  # orchestrator vision
            + image_count * 1000  # prompt engineer per-shot
            + 500  # editor plan
            + len(script) * 2  # script encoding overhead
        )

        if estimated_audio_s > effective_video_s * 1.3:
            extra_needed = recommended_count - image_count
            if duration_mode == "fixed":
                warning = (
                    f"脚本约 {len(script)} 字，预计口播 {estimated_audio_s:.0f}s，"
                    f"但目标视频仅 {duration_seconds}s。"
                    f"建议缩短脚本，或将时长增加到 {int(estimated_audio_s) + 1}s 以上"
                    f"（需至少 {recommended_count} 张素材）。"
                )
            else:
                warning = (
                    f"脚本约 {len(script)} 字，预计口播 {estimated_audio_s:.0f}s，"
                    f"但当前 {image_count} 张素材最多支撑 {max_video_s}s 视频。"
                    f"建议再补充 {max(extra_needed, 1)} 张素材"
                    f"（总共至少 {recommended_count} 张），或缩短脚本。"
                )
            return PrefightCheckResponse(
                ok=False,
                warning=warning,
                estimated_audio_seconds=round(estimated_audio_s, 1),
                max_video_seconds=effective_video_s,
                recommended_image_count=recommended_count,
                estimated_tokens=estimated_tokens,
            )

        return PrefightCheckResponse(
            ok=True,
            estimated_audio_seconds=round(estimated_audio_s, 1),
            max_video_seconds=effective_video_s,
            recommended_image_count=image_count,
            estimated_tokens=estimated_tokens,
        )

    return router


async def _run_pipeline(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    input_config: dict,
    user_id: str | None = None,
    memory_service=None,
    mem0=None,
    rag_service=None,
):
    """Background task wrapper for pipeline execution."""
    try:
        await executor.run(
            run_id, project_id, input_config,
            user_id=user_id, memory_service=memory_service, mem0=mem0,
            rag_service=rag_service,
        )
        await _auto_save_run_to_repository(run_id)
    except Exception:
        import logging
        logging.getLogger(__name__).error(f"Pipeline {run_id} background task failed", exc_info=True)


async def _auto_save_run_to_repository(run_id: str) -> None:
    log = logging.getLogger(__name__)
    try:
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if not run or run.status != "completed" or not run.final_video_path or not run.user_id:
                return
            await save_video_to_repository(
                session,
                user_id=run.user_id,
                project_id=run.project_id,
                run=run,
                title=derive_delivery_title(run),
            )
    except Exception as exc:
        log.warning("Auto-save to repository failed for pipeline %s: %s", run_id, exc)


async def _continue_from_confirmation(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    input_config: dict,
    orchestrator_plan: dict,
    user_id: str | None = None,
    memory_service=None,
    mem0=None,
):
    """Background task: resume pipeline from prompt_engineer after user confirms replication plan."""
    import logging
    import uuid
    from app.agents.base import AgentContext
    from app.services.usage_service import UsageRecorder

    log = logging.getLogger(__name__)
    try:
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=run_id,
            project_id=project_id,
            db_session_factory=async_session,
            usage_recorder=UsageRecorder(async_session),
            artifacts={"orchestrator_plan": orchestrator_plan},
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )

        # Use script from orchestrator plan if present (replication may generate script)
        effective_config = {**input_config}
        if orchestrator_plan.get("script"):
            effective_config["script"] = orchestrator_plan["script"]

        result = await executor.resume_from_confirmation(context, effective_config)

        final_video = result.get("final_video_path") if isinstance(result, dict) else None
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "completed"
                run.final_video_path = final_video
                run.completed_at = datetime.now(timezone.utc)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()
        await _auto_save_run_to_repository(run_id)

    except Exception as e:
        log.error(f"Continue from confirmation failed for pipeline {run_id}: {e}", exc_info=True)
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "failed"
                run.error_message = str(e)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()


def _apply_remix_edits(remix_plan: dict, req: ConfirmRemixRequest) -> None:
    segments = remix_plan.get("segments")
    if not isinstance(segments, list):
        return
    edits = {edit.segment_idx: edit for edit in req.edited_segments}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        edit = edits.get(int(segment.get("segment_idx", -1)))
        if edit is None:
            continue
        if edit.removed:
            segment["removed"] = True
        if edit.source_video_id:
            segment["source_video_id"] = edit.source_video_id
        if edit.start_seconds is not None:
            segment["start_seconds"] = edit.start_seconds
        if edit.end_seconds is not None:
            segment["end_seconds"] = edit.end_seconds
        if edit.transition_type is not None:
            segment["transition_to_next"] = edit.transition_type


async def _continue_from_remix_confirmation(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    input_config: dict,
    saved_artifacts: dict,
    user_id: str | None = None,
    memory_service=None,
    mem0=None,
):
    """Background task: assemble a confirmed remix plan."""
    import logging
    import uuid
    from app.agents.base import AgentContext
    from app.services.usage_service import UsageRecorder

    log = logging.getLogger(__name__)
    try:
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=run_id,
            project_id=project_id,
            db_session_factory=async_session,
            usage_recorder=UsageRecorder(async_session),
            artifacts=saved_artifacts,
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )

        result = await executor.resume_from_remix_confirmation(context, input_config)
        if isinstance(result, dict) and result.get("status") == "waiting_remix_confirmation":
            async with async_session() as session:
                run = await session.get(PipelineRun, run_id)
                if run and run.status != "cancelled":
                    run.status = "waiting_remix_confirmation"
                    run.current_agent = "remix_planner"
                    run.updated_at = datetime.now(timezone.utc)
                    if run.session_id:
                        chat_session = await session.get(AutoChatSession, run.session_id)
                        if chat_session:
                            chat_session.status_preview = "等待确认混剪方案"
                            chat_session.last_activity_at = run.updated_at
                    await session.commit()
            return
        final_video = result.get("final_video_path") if isinstance(result, dict) else None
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "completed"
                run.final_video_path = final_video
                run.completed_at = datetime.now(timezone.utc)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()
        await _auto_save_run_to_repository(run_id)

    except Exception as e:
        log.error(f"Continue from remix confirmation failed for pipeline {run_id}: {e}", exc_info=True)
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "failed"
                run.error_message = str(e)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()


async def _continue_from_prompt_review(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    input_config: dict,
    user_id: str | None = None,
    memory_service=None,
    mem0=None,
):
    """Background task: resume pipeline from av_editor after user confirms the prompt plan."""
    import logging
    import uuid
    from app.agents.base import AgentContext
    from app.services.usage_service import UsageRecorder

    log = logging.getLogger(__name__)
    try:
        # Restore artifacts from the completed prompt_engineer execution
        async with async_session() as db:
            run = await db.get(PipelineRun, run_id)
            if not run:
                return
            import json as _json
            saved_artifacts: dict = _json.loads(run.artifacts_snapshot or "{}")

        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=run_id,
            project_id=project_id,
            db_session_factory=async_session,
            usage_recorder=UsageRecorder(async_session),
            artifacts=saved_artifacts,
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )

        result = await executor.resume_from_prompt_review(context, input_config)

        final_video = result.get("final_video_path") if isinstance(result, dict) else None
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "completed"
                run.final_video_path = final_video
                run.completed_at = datetime.now(timezone.utc)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()
        await _auto_save_run_to_repository(run_id)

    except Exception as e:
        log.error(f"Continue from prompt review failed for pipeline {run_id}: {e}", exc_info=True)
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "failed"
                run.error_message = str(e)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()


async def _retry_agent(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    agent_name: str,
    agent_input: dict,
    input_config: dict,
    artifacts: dict,
    user_id: str | None = None,
    memory_service=None,
    mem0=None,
):
    """Background task: re-run a single failed agent and continue the pipeline from there."""
    import logging
    import uuid
    from app.agents.base import AgentContext
    from app.services.usage_service import UsageRecorder

    log = logging.getLogger(__name__)
    try:
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=run_id,
            project_id=project_id,
            db_session_factory=async_session,
            usage_recorder=UsageRecorder(async_session),
            artifacts=artifacts,
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )

        agent_map = executor.get_agent_map()
        agent = agent_map.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        result = await agent.run(context, agent_input)

        if not result.success:
            raise RuntimeError(f"Agent {agent_name} retry failed: {result.error}")

        # Store the new output in artifacts
        agent_to_artifact_key = executor.get_agent_to_artifact_key()
        artifact_key = agent_to_artifact_key.get(agent_name)
        if artifact_key:
            context.artifacts[artifact_key] = result.output_data

        await executor.continue_from_retry(context, agent_name, input_config)

        # Mark completed
        final_video = context.artifacts.get("final_video", {}).get("final_video_path")
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run:
                if run.status == "cancelled":
                    return
                run.status = "completed"
                run.final_video_path = final_video
                run.completed_at = datetime.now(timezone.utc)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()
        await _auto_save_run_to_repository(run_id)

    except Exception as e:
        log.error(f"Retry agent {agent_name} for pipeline {run_id} failed: {e}", exc_info=True)
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run:
                if run.status == "cancelled":
                    return
                run.status = "failed"
                run.error_message = str(e)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()


async def _retry_shots_task(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    input_config: dict,   # includes regenerate_indices
    saved_artifacts: dict,
    user_id: str | None = None,
    memory_service=None,
    mem0=None,
):
    """Background task: re-run only the specified shots then re-edit the video."""
    import logging as _logging
    import uuid as _uuid
    from app.agents.base import AgentContext
    from app.services.usage_service import UsageRecorder

    log = _logging.getLogger(__name__)
    try:
        context = AgentContext(
            trace_id=str(_uuid.uuid4()),
            pipeline_run_id=run_id,
            project_id=project_id,
            db_session_factory=async_session,
            usage_recorder=UsageRecorder(async_session),
            artifacts=saved_artifacts,
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )

        # Re-run only the failing shots (VideoGeneratorAgent reads regenerate_indices)
        video_input = executor.build_video_input(saved_artifacts, input_config)
        video_result = await executor.video_gen_agent.run(context, video_input)
        if not video_result.success:
            raise RuntimeError(f"Shot retry failed: {video_result.error}")
        context.artifacts["video_clips"] = video_result.output_data
        await context.save_checkpoint()

        # Re-assemble the final video with the updated clip list
        editor_input = executor.build_editor_input(context.artifacts, input_config)
        editor_result = await executor.video_editor.run(context, editor_input)
        if not editor_result.success:
            raise RuntimeError(f"Video editor failed after shot retry: {editor_result.error}")
        context.artifacts["final_video"] = editor_result.output_data
        await context.save_checkpoint()

        final_video_path = editor_result.output_data.get("final_video_path")
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "completed"
                run.final_video_path = final_video_path
                run.completed_at = datetime.now(timezone.utc)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()
        await _auto_save_run_to_repository(run_id)

    except Exception as exc:
        log.error(f"Shot retry failed for pipeline {run_id}: {exc}", exc_info=True)
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status != "cancelled":
                run.status = "failed"
                run.error_message = str(exc)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()


# ── Static price table (CNY, 2025 estimates) ──────────────────────────────────
_VIDEO_GEN_PRICE_PER_SECOND: dict[str, float] = {
    "seedance1.5-pro": 0.30,    # CNY per second of generated video
    "seedance2.0":     0.50,
    "kling":           0.35,
    "mock":            0.00,
}
_TTS_PRICE_PER_1K_CHARS: float = 0.10     # CNY per 1 000 Chinese characters
_LLM_PRICE_PER_1M_TOKENS: float = 2.00    # CNY per 1 M tokens (rough blended rate)
_BGM_PRICE_FLAT: float = 0.05             # CNY flat per run with BGM


def _compute_cost_estimate(req: EstimateCostRequest) -> EstimateCostResponse:
    model_key = req.model if req.model in _VIDEO_GEN_PRICE_PER_SECOND else "seedance1.5-pro"
    price_per_sec = _VIDEO_GEN_PRICE_PER_SECOND[model_key]

    total_gen_seconds: float = sum(
        float(s.get("generation_duration_seconds") or s.get("duration_seconds") or 5)
        for s in req.shot_plan
    )
    shot_count = len(req.shot_plan)

    video_cost = total_gen_seconds * price_per_sec

    # TTS: use explicit char count or estimate from script_segment lengths
    if req.tts_char_count > 0:
        tts_chars = req.tts_char_count
    else:
        tts_chars = sum(
            len(str(s.get("script_segment") or ""))
            for s in req.shot_plan
        )
    tts_cost = 0.0 if req.voiceover_no_audio else tts_chars / 1000 * _TTS_PRICE_PER_1K_CHARS

    # LLM: rough estimate — orchestrator + director + qa ≈ 60 K tokens per run
    llm_cost = 60_000 / 1_000_000 * _LLM_PRICE_PER_1M_TOKENS

    bgm_cost = _BGM_PRICE_FLAT if req.bgm_mood not in ("none", "") else 0.0

    total = round(video_cost + tts_cost + llm_cost + bgm_cost, 2)

    warning = None
    if total > 10.0:
        warning = f"预估费用 ¥{total:.2f}，超过 ¥10，请确认后继续"
    elif shot_count == 0:
        warning = "未提供镜头方案，仅显示 LLM 基础费用"

    return EstimateCostResponse(
        estimated_total_cny=total,
        breakdown={
            "video_gen": round(video_cost, 2),
            "tts": round(tts_cost, 2),
            "llm": round(llm_cost, 2),
            "bgm": round(bgm_cost, 2),
        },
        shot_count=shot_count,
        total_generation_seconds=round(total_gen_seconds, 1),
        warning=warning,
    )
