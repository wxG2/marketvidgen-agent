from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents.stages.orchestrator import _snap_to_supported
from app.core.config import settings
from app.db.session import async_session, get_db
from app.models.pipeline import AgentExecution
from app.routers.pipeline import (
    _build_replication_narration_script,
    _continue_from_confirmation,
    _continue_from_prompt_review,
    _pipeline_tasks,
    launch_pipeline_task,
)
from app.schemas.public_api import (
    PublicVideoJobResponse,
    PublicVideoJobReviewRequest,
    PublicVideoJobReviewResponse,
    PublicVideoJobSpec,
)
from app.services.api_keys import ApiKeyContext, ensure_api_key_scope, get_api_key_context
from app.services.public_video_jobs import (
    build_public_job_response,
    create_public_video_job,
    ensure_completed_video_file,
    get_external_job_for_context,
    get_latest_agent_payload,
)

logger = logging.getLogger(__name__)


def _parse_spec(spec: str) -> PublicVideoJobSpec:
    try:
        payload = json.loads(spec)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="spec must be valid JSON") from exc
    try:
        return PublicVideoJobSpec.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def get_public_video_jobs_router(executor) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["public-video-jobs"])

    @router.post("/video-jobs", response_model=PublicVideoJobResponse, status_code=202)
    async def create_video_job(
        request: Request,
        spec: str = Form(...),
        images: list[UploadFile] = File(...),
        reference_video: Optional[UploadFile] = File(default=None),
        watermark: Optional[UploadFile] = File(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        context: ApiKeyContext = Depends(get_api_key_context),
        db: AsyncSession = Depends(get_db),
    ):
        ensure_api_key_scope(context, "video_jobs:create")
        job_spec = _parse_spec(spec)
        job, run, _created = await create_public_video_job(
            db,
            context=context,
            executor=executor,
            spec=job_spec,
            image_files=images,
            reference_video=reference_video,
            watermark=watermark,
            idempotency_key=idempotency_key,
            memory_service=getattr(request.app.state, "agent_memory", None),
            mem0=getattr(request.app.state, "mem0", None),
        )
        return build_public_job_response(job, run)

    @router.get("/video-jobs/{job_id}", response_model=PublicVideoJobResponse)
    async def get_video_job(
        job_id: str,
        context: ApiKeyContext = Depends(get_api_key_context),
        db: AsyncSession = Depends(get_db),
    ):
        ensure_api_key_scope(context, "video_jobs:read")
        job, run = await get_external_job_for_context(db, context, job_id)
        return build_public_job_response(job, run)

    @router.get("/video-jobs/{job_id}/events")
    async def stream_video_job_events(
        job_id: str,
        context: ApiKeyContext = Depends(get_api_key_context),
    ):
        ensure_api_key_scope(context, "video_jobs:read")

        async def _event_generator() -> AsyncGenerator[dict, None]:
            while True:
                try:
                    async with async_session() as db:
                        job, run = await get_external_job_for_context(db, context, job_id)
                        payload = build_public_job_response(job, run).model_dump(mode="json")
                        payload["agents"] = await get_latest_agent_payload(db, run.id)
                        data = json.dumps(payload, ensure_ascii=False)
                        yield {"event": "update", "data": data}
                        if run.status in {"completed", "failed", "cancelled"}:
                            yield {"event": "done", "data": data}
                            return
                except Exception as exc:
                    logger.warning("Public video job SSE failed for %s: %s", job_id, exc)
                    yield {"event": "error", "data": json.dumps({"detail": str(exc)}, ensure_ascii=False)}
                    return
                await asyncio.sleep(2)

        return EventSourceResponse(_event_generator())

    @router.post("/video-jobs/{job_id}/review", response_model=PublicVideoJobReviewResponse)
    async def review_video_job(
        job_id: str,
        data: PublicVideoJobReviewRequest,
        request: Request,
        context: ApiKeyContext = Depends(get_api_key_context),
        db: AsyncSession = Depends(get_db),
    ):
        ensure_api_key_scope(context, "video_jobs:review")
        job, run = await get_external_job_for_context(db, context, job_id)
        if run.status == "waiting_prompt_review":
            return await _review_prompt_plan(
                executor,
                job.project_id,
                run.id,
                data,
                memory_service=getattr(request.app.state, "agent_memory", None),
                mem0=getattr(request.app.state, "mem0", None),
                db=db,
                user_id=context.user.id,
            )
        if run.status == "waiting_confirmation":
            return await _review_replication_plan(
                executor,
                job.project_id,
                run.id,
                data,
                memory_service=getattr(request.app.state, "agent_memory", None),
                mem0=getattr(request.app.state, "mem0", None),
                db=db,
                user_id=context.user.id,
            )
        raise HTTPException(status_code=400, detail=f"Video job is not waiting for review (status: {run.status})")

    @router.get("/video-jobs/{job_id}/result")
    async def download_video_job_result(
        job_id: str,
        context: ApiKeyContext = Depends(get_api_key_context),
        db: AsyncSession = Depends(get_db),
    ):
        ensure_api_key_scope(context, "video_jobs:read")
        _job, run = await get_external_job_for_context(db, context, job_id)
        path = ensure_completed_video_file(run)
        return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")

    return router


async def _review_prompt_plan(
    executor,
    project_id: str,
    run_id: str,
    data: PublicVideoJobReviewRequest,
    *,
    memory_service=None,
    mem0=None,
    db: AsyncSession,
    user_id: str,
) -> PublicVideoJobReviewResponse:
    if not data.approved:
        raise HTTPException(status_code=400, detail="Prompt review can only be approved; edit shots before approving")

    from app.models.pipeline import PipelineRun

    run = await db.get(PipelineRun, run_id)
    if run is None or run.user_id != user_id or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Video job not found")
    input_config = json.loads(run.input_config or "{}")

    if data.edited_shots:
        snapshot: dict = json.loads(run.artifacts_snapshot or "{}")
        prompt_plan: dict = snapshot.get("prompt_plan", {})
        shots: list[dict] = prompt_plan.get("shot_prompts", [])
        edits_by_idx = {edit.shot_idx: edit for edit in data.edited_shots}
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
                    _duration_range_label,
                    _generation_duration_for,
                    _snap_to_half_second,
                )

                dur = _snap_to_half_second(edit.duration_seconds)
                shot["duration_seconds"] = dur
                shot["generation_duration_seconds"] = _generation_duration_for(dur)
                shot["duration_range_label"] = _duration_range_label(dur)
        run.artifacts_snapshot = json.dumps(snapshot, ensure_ascii=False)

    run.status = "running"
    run.current_agent = "audio_subtitle"
    run.updated_at = datetime.now(timezone.utc)
    await db.commit()

    task = asyncio.create_task(
        _continue_from_prompt_review(
            executor,
            run.id,
            project_id,
            input_config,
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )
    )
    _pipeline_tasks[run.id] = task
    task.add_done_callback(lambda _task: _pipeline_tasks.pop(run.id, None))
    return PublicVideoJobReviewResponse(status="confirmed", message="Shot plan approved; video generation resumed")


async def _review_replication_plan(
    executor,
    project_id: str,
    run_id: str,
    data: PublicVideoJobReviewRequest,
    *,
    memory_service=None,
    mem0=None,
    db: AsyncSession,
    user_id: str,
) -> PublicVideoJobReviewResponse:
    from app.models.pipeline import PipelineRun

    run = await db.get(PipelineRun, run_id)
    if run is None or run.user_id != user_id or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Video job not found")
    input_config = json.loads(run.input_config or "{}")

    if not data.approved:
        if data.adjustments:
            adjusted_config = {**input_config, "adjustment_feedback": data.adjustments}
            run.status = "running"
            run.current_agent = "replication_planner"
            run.input_config = json.dumps(adjusted_config, ensure_ascii=False)
            run.updated_at = datetime.now(timezone.utc)
            await db.commit()
            launch_pipeline_task(
                executor,
                run.id,
                project_id,
                adjusted_config,
                user_id=user_id,
                memory_service=memory_service,
                mem0=mem0,
            )
            return PublicVideoJobReviewResponse(status="rerunning", message="Replication plan is being regenerated")
        run.status = "cancelled"
        run.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return PublicVideoJobReviewResponse(status="cancelled", message="Video job cancelled")

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

    standard_shots = []
    supported_durations = settings.SEEDANCE_SUPPORTED_DURATIONS
    for fallback_idx, shot in enumerate(replication_plan.get("shots", [])):
        raw_dur = shot.get("suggested_duration_seconds", 5)
        clamped_dur = _snap_to_supported(raw_dur, supported_durations)
        standard_shots.append(
            {
                "shot_idx": shot.get("shot_idx", fallback_idx),
                "image_path": shot.get("material_image_path") or shot.get("reference_frame_path", ""),
                "script_segment": shot.get("description", ""),
                "duration_seconds": clamped_dur,
            }
        )

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

    run.status = "running"
    run.current_agent = "prompt_engineer"
    run.updated_at = datetime.now(timezone.utc)
    await db.commit()

    task = asyncio.create_task(
        _continue_from_confirmation(
            executor,
            run.id,
            project_id,
            input_config,
            orchestrator_plan,
            user_id=user_id,
            memory_service=memory_service,
            mem0=mem0,
        )
    )
    _pipeline_tasks[run.id] = task
    task.add_done_callback(lambda _task: _pipeline_tasks.pop(run.id, None))
    return PublicVideoJobReviewResponse(status="confirmed", message="Replication plan approved; video generation resumed")
