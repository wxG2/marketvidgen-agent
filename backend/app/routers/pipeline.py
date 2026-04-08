from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from pathlib import Path

from app.auth import (
    compile_background_template,
    get_auto_chat_session_for_user,
    get_background_template_for_user,
    get_current_user,
    get_material_for_user,
    get_pipeline_run_for_user,
    get_project_for_user,
    get_social_account_for_user,
)
from app.config import settings
from app.database import get_db
from app.models.background_template import BackgroundTemplate
from app.models.auto_chat import AutoChatSession
from app.models.material import Material
from app.models.pipeline import PipelineRun, AgentExecution
from app.models.social_account import SocialAccount
from app.models.video_delivery import VideoDelivery
from app.models.user import User
from app.schemas.pipeline import (
    PipelineCreateRequest,
    PipelineRunResponse,
    AgentExecutionResponse,
    PipelineUsageResponse,
    PipelineDeliveryResponse,
    PlatformPreviewCardResponse,
    VideoDeliveryResponse,
    DeliveryActionRequest,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
    PrefightCheckRequest,
    PrefightCheckResponse,
    SwarmMessageRequest,
    ConfirmPlanRequest,
)
from app.schemas.social_account import PublishDraftResponse, SocialAccountResponse
from app.agents.pipeline import PipelineExecutor
from app.agents.swarm_runtime import get_swarm_controller
from app.agents.swarm_runtime import register_swarm_controller, unregister_swarm_controller
from app.services.video_delivery import (
    build_douyin_publish_draft,
    build_platform_preview_cards,
    derive_delivery_title,
    publish_video_to_douyin,
    save_video_to_repository,
    serialize_delivery,
)
from app.services.social_accounts import serialize_social_account
from app.services.usage_service import UsageRecorder
from app.database import async_session


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
        payload = PipelineRunResponse.model_validate(run).model_dump(mode="json")
        controller = get_swarm_controller(run.id)
        if run.swarm_state_json:
            try:
                payload["swarm_state"] = json.loads(run.swarm_state_json)
            except json.JSONDecodeError:
                payload["swarm_state"] = None
        else:
            payload["swarm_state"] = None
        if controller is not None and controller.latest_snapshot:
            payload["swarm_state"] = controller.latest_snapshot
        return payload

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
                session.reference_video_id = req.reference_video_id
                session.background_template_id = req.background_template_id
                session.draft_script = req.script
                session.video_platform = req.platform
                session.video_no_audio = req.no_audio
                session.duration_mode = req.duration_mode
                session.video_transition = req.transition
                session.bgm_mood = req.bgm_mood
                session.watermark_id = req.watermark_image_id
                session.status_preview = "准备执行"
                session.last_activity_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(run)

        # Fire and forget — pipeline runs in background
        asyncio.create_task(_run_pipeline(executor, run.id, project_id, input_config))

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
        if run.status not in ("pending", "running", "waiting_confirmation"):
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
        return {"status": "cancelled"}

    @router.post("/projects/{project_id}/pipeline/{run_id}/confirm-plan")
    async def confirm_replication_plan(
        project_id: str,
        run_id: str,
        req: ConfirmPlanRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Confirm or adjust the replication plan produced by the orchestrator."""
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status != "waiting_confirmation":
            raise HTTPException(status_code=400, detail=f"Pipeline is not waiting for confirmation (status: {run.status})")

        input_config = json.loads(run.input_config) if run.input_config else {}

        if not req.approved:
            if req.adjustments:
                # Re-run orchestrator with user feedback
                run.status = "running"
                run.current_agent = "orchestrator"
                run.updated_at = datetime.now(timezone.utc)
                # Append adjustment feedback to input config
                adjusted_config = {**input_config, "adjustment_feedback": req.adjustments}
                run.input_config = json.dumps(adjusted_config, ensure_ascii=False)
                if run.session_id:
                    session = await get_auto_chat_session_for_user(db, user.id, project_id, run.session_id)
                    session.status_preview = "重新生成方案中"
                    session.last_activity_at = datetime.now(timezone.utc)
                await db.commit()
                asyncio.create_task(
                    _run_pipeline(executor, run.id, project_id, adjusted_config)
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

        # Approved — convert replication plan to standard orchestrator_plan and resume
        # Rebuild artifacts from the completed orchestrator execution
        result = await db.execute(
            select(AgentExecution)
            .where(
                AgentExecution.pipeline_run_id == run_id,
                AgentExecution.agent_name == "orchestrator",
                AgentExecution.status == "completed",
            )
            .order_by(AgentExecution.created_at.desc())
            .limit(1)
        )
        orch_exec = result.scalars().first()
        if not orch_exec or not orch_exec.output_data:
            raise HTTPException(status_code=400, detail="No orchestrator output found")

        orch_output = json.loads(orch_exec.output_data)
        replication_plan = orch_output.get("replication_plan", {})
        narration_script = _build_replication_narration_script(
            replication_plan,
            orch_output.get("script", input_config.get("script", "")),
        )

        # Convert replication plan shots to standard orchestrator_plan format
        supported_durations = settings.SEEDANCE_SUPPORTED_DURATIONS
        from app.agents.orchestrator import _snap_to_supported

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
        asyncio.create_task(
            _continue_from_confirmation(
                executor, run.id, project_id, input_config, orchestrator_plan,
            )
        )
        return {"status": "confirmed", "message": "复刻方案已确认，继续生成中"}

    @router.post("/projects/{project_id}/pipeline/{run_id}/message")
    async def send_swarm_message(
        project_id: str,
        run_id: str,
        req: SwarmMessageRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        run = await get_pipeline_run_for_user(db, user.id, run_id)
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status not in ("pending", "running"):
            raise HTTPException(status_code=400, detail="Only pending or running pipelines accept messages")
        if run.engine != "swarm":
            raise HTTPException(status_code=400, detail="Human-in-the-loop messaging is only enabled for swarm runs")

        controller = get_swarm_controller(run_id)
        if controller is None:
            raise HTTPException(status_code=409, detail="Swarm controller is not available for this run")

        await controller.send_human_message(req.message)
        return {"status": "queued"}

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
        from app.config import settings as _settings

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
            shortfall = estimated_audio_s - effective_video_s
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


async def _run_pipeline(executor: PipelineExecutor, run_id: str, project_id: str, input_config: dict):
    """Background task wrapper for pipeline execution."""
    try:
        await executor.run(run_id, project_id, input_config)
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


async def _retry_agent(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    agent_name: str,
    agent_input: dict,
    input_config: dict,
    artifacts: dict,
):
    """Background task: re-run a single failed agent and continue the pipeline from there."""
    import logging
    import uuid
    from app.agents.base import AgentContext
    from app.services.usage_service import UsageRecorder

    log = logging.getLogger(__name__)
    controller_registered = False
    try:
        if getattr(executor, "engine_name", "pipeline") == "swarm":
            register_swarm_controller(run_id)
            controller_registered = True

        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=run_id,
            project_id=project_id,
            db_session_factory=async_session,
            usage_recorder=UsageRecorder(async_session),
            artifacts=artifacts,
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
    finally:
        if controller_registered:
            unregister_swarm_controller(run_id)


def _build_agent_input(agent_name: str, artifacts: dict, input_config: dict) -> dict:
    """Build the input dict for a given agent from accumulated artifacts."""
    if agent_name == "orchestrator":
        return input_config
    if agent_name == "prompt_engineer":
        return artifacts.get("orchestrator_plan", {})
    if agent_name == "audio_subtitle":
        prompt_plan = artifacts.get("prompt_plan", {})
        orchestrator_plan = artifacts.get("orchestrator_plan", {})
        shot_prompts = prompt_plan.get("shot_prompts", [])
        shot_script = "\n".join(
            str(item.get("script_segment") or "").strip()
            for item in shot_prompts
            if isinstance(item, dict) and str(item.get("script_segment") or "").strip()
        )
        return {
            "script": shot_script or orchestrator_plan.get("script") or input_config.get("script", ""),
            "voice_params": prompt_plan.get("voice_params", {}),
        }
    if agent_name == "video_generator":
        prompt_plan = artifacts.get("prompt_plan", {})
        return {
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "no_audio": input_config.get("no_audio", True),
        }
    if agent_name == "video_editor":
        video_clips = artifacts.get("video_clips", {})
        audio = artifacts.get("audio", {})
        prompt_plan = artifacts.get("prompt_plan", {})
        orch_plan = artifacts.get("orchestrator_plan", {})
        return {
            "video_clips": video_clips.get("video_clips", []),
            "audio_path": audio.get("audio_path", ""),
            "subtitle_path": audio.get("subtitle_path", ""),
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "duration_mode": input_config.get("duration_mode", "fixed"),
            "shot_durations": [s["duration_seconds"] for s in orch_plan.get("shots", [])],
            "transition": input_config.get("transition", "none"),
            "transition_duration": input_config.get("transition_duration", 0.5),
            "bgm_mood": input_config.get("bgm_mood", "none"),
            "bgm_volume": input_config.get("bgm_volume", 0.15),
            "watermark_path": input_config.get("watermark_path"),
        }
    return {}


async def _run_agent(executor: PipelineExecutor, context, agent_name: str, input_config: dict) -> dict:
    return await executor.run_named_agent(context, agent_name, input_config)


async def _continue_pipeline_from_retry(
    executor: PipelineExecutor,
    context,
    agent_name: str,
    input_config: dict,
):
    await executor.continue_from_retry(context, agent_name, input_config)
