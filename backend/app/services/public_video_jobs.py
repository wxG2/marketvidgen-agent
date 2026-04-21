from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.external_video_job import ExternalVideoJob
from app.models.pipeline import AgentExecution, PipelineRun
from app.models.project import Project
from app.models.video_upload import VideoUpload
from app.routers.pipeline import launch_pipeline_task
from app.schemas.public_api import PublicVideoJobResponse, PublicVideoJobReviewState, PublicVideoJobResultResponse, PublicVideoJobSpec
from app.services.api_keys import ApiKeyContext, hash_idempotency_key
from app.services.material_service import index_uploaded_file
from app.services.upload_validation import validate_upload_file


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _external_status(internal_status: str) -> str:
    if internal_status == "pending":
        return "queued"
    if internal_status == "running":
        return "processing"
    if internal_status in {"waiting_prompt_review", "waiting_confirmation"}:
        return "requires_review"
    if internal_status in TERMINAL_STATUSES:
        return internal_status
    return "processing"


def _scrub_review_data(value: Any) -> Any:
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            key_lower = str(key).lower()
            if "path" in key_lower or key_lower in {"access_token", "refresh_token"}:
                continue
            scrubbed[key] = _scrub_review_data(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub_review_data(item) for item in value]
    return value


def _shot_plan_review(snapshot: dict[str, Any]) -> dict[str, Any]:
    prompt_plan = snapshot.get("prompt_plan") if isinstance(snapshot.get("prompt_plan"), dict) else {}
    shot_prompts = prompt_plan.get("shot_prompts") if isinstance(prompt_plan.get("shot_prompts"), list) else []
    safe_shots = []
    for shot in shot_prompts:
        if not isinstance(shot, dict):
            continue
        safe_shots.append(
            {
                "shot_idx": shot.get("shot_idx"),
                "script_segment": shot.get("script_segment", ""),
                "video_prompt": shot.get("video_prompt", ""),
                "duration_seconds": shot.get("duration_seconds"),
                "generation_duration_seconds": shot.get("generation_duration_seconds"),
                "duration_range_label": shot.get("duration_range_label"),
                "image_content": shot.get("image_content", ""),
            }
        )
    return {
        "shot_prompts": safe_shots,
        "voice_params": _scrub_review_data(prompt_plan.get("voice_params") or {}),
    }


def _replication_plan_review(snapshot: dict[str, Any]) -> dict[str, Any]:
    orchestrator_plan = snapshot.get("orchestrator_plan") if isinstance(snapshot.get("orchestrator_plan"), dict) else {}
    replication_plan = snapshot.get("replication_plan") or orchestrator_plan.get("replication_plan") or {}
    return _scrub_review_data(replication_plan if isinstance(replication_plan, dict) else {})


def build_public_job_response(job: ExternalVideoJob, run: PipelineRun) -> PublicVideoJobResponse:
    status = _external_status(run.status)
    snapshot = _load_json(run.artifacts_snapshot)
    review = PublicVideoJobReviewState(required=False, data={})
    if run.status == "waiting_prompt_review":
        review = PublicVideoJobReviewState(type="shot_plan", required=True, data=_shot_plan_review(snapshot))
    elif run.status == "waiting_confirmation":
        review = PublicVideoJobReviewState(type="replication_plan", required=True, data=_replication_plan_review(snapshot))

    result = PublicVideoJobResultResponse(
        download_url=f"/v1/video-jobs/{job.id}/result" if run.status == "completed" and run.final_video_path else None
    )
    return PublicVideoJobResponse(
        job_id=job.id,
        status=status,
        internal_status=run.status,
        current_agent=run.current_agent,
        review=review,
        result=result,
        error=run.error_message,
        created_at=job.created_at,
        updated_at=run.updated_at or job.updated_at,
    )


async def get_external_job_for_context(
    db: AsyncSession,
    context: ApiKeyContext,
    job_id: str,
) -> tuple[ExternalVideoJob, PipelineRun]:
    job = await db.get(ExternalVideoJob, job_id)
    if not job or job.user_id != context.user.id or job.api_key_id != context.api_key.id:
        raise HTTPException(status_code=404, detail="Video job not found")
    run = await db.get(PipelineRun, job.pipeline_run_id)
    if not run or run.user_id != context.user.id:
        raise HTTPException(status_code=404, detail="Video job not found")
    return job, run


async def create_public_video_job(
    db: AsyncSession,
    *,
    context: ApiKeyContext,
    executor,
    spec: PublicVideoJobSpec,
    image_files: list[UploadFile],
    reference_video: UploadFile | None,
    watermark: UploadFile | None,
    idempotency_key: str | None,
    memory_service=None,
    mem0=None,
) -> tuple[ExternalVideoJob, PipelineRun, bool]:
    idempotency_hash = hash_idempotency_key(idempotency_key)
    if idempotency_hash:
        existing_result = await db.execute(
            select(ExternalVideoJob)
            .where(
                ExternalVideoJob.api_key_id == context.api_key.id,
                ExternalVideoJob.idempotency_key_hash == idempotency_hash,
            )
            .limit(1)
        )
        existing_job = existing_result.scalars().first()
        if existing_job is not None:
            existing_run = await db.get(PipelineRun, existing_job.pipeline_run_id)
            if existing_run is None:
                raise HTTPException(status_code=404, detail="Existing video job is missing its pipeline run")
            return existing_job, existing_run, False

    if not image_files:
        raise HTTPException(status_code=400, detail="At least one image is required")
    if len(image_files) > 100:
        raise HTTPException(status_code=400, detail="At most 100 images are allowed")

    project_label = spec.client_reference_id or _utcnow().strftime("%Y%m%d-%H%M%S")
    project = Project(name=f"API Job {project_label}", user_id=context.user.id)
    db.add(project)
    await db.flush()

    image_ids: list[str] = []
    for index, file in enumerate(image_files):
        validated = await validate_upload_file(file, kind="image")
        material = await index_uploaded_file(
            db,
            settings.MATERIALS_ROOT,
            context.user.id,
            f"api-job-{project.id}",
            f"{index + 1:03d}_{validated.filename}",
            validated.content,
        )
        if material is None:
            raise HTTPException(status_code=400, detail=f"Unsupported image file: {validated.filename}")
        await db.flush()
        image_ids.append(material.id)

    watermark_image_id = None
    watermark_path = None
    if watermark is not None:
        validated = await validate_upload_file(watermark, kind="image")
        material = await index_uploaded_file(
            db,
            settings.MATERIALS_ROOT,
            context.user.id,
            f"api-job-{project.id}-watermarks",
            f"watermark_{validated.filename}",
            validated.content,
        )
        if material is not None:
            await db.flush()
            watermark_image_id = material.id
            watermark_path = str((Path(settings.MATERIALS_ROOT) / material.file_path).resolve())

    reference_video_id = None
    if reference_video is not None:
        validated = await validate_upload_file(reference_video, kind="video")
        upload_dir = Path(settings.VIDEO_REPOSITORY_DIR) / "uploads" / context.user.id / project.id
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{uuid.uuid4()}_{validated.filename}"
        file_path = upload_dir / safe_name
        file_path.write_bytes(validated.content)
        upload = VideoUpload(
            project_id=project.id,
            filename=validated.filename,
            file_path=str(file_path),
            file_size=validated.file_size,
            mime_type=validated.content_type,
        )
        db.add(upload)
        await db.flush()
        reference_video_id = upload.id

    input_config: dict[str, Any] = {
        "script": spec.script.strip(),
        "user_request": spec.prompt.strip(),
        "image_ids": image_ids,
        "platform": spec.platform,
        "duration_seconds": spec.duration_seconds,
        "duration_mode": spec.duration_mode,
        "no_audio": spec.video_model_no_audio,
        "video_model_no_audio": spec.video_model_no_audio,
        "voiceover_no_audio": spec.voiceover_no_audio,
        "generation_model": spec.generation_model,
        "style": spec.style,
        "voice_id": spec.voice_id,
        "transition": spec.transition,
        "transition_duration": spec.transition_duration,
        "bgm_mood": spec.bgm_mood,
        "bgm_volume": spec.bgm_volume,
        "watermark_image_id": watermark_image_id,
        "review_prompts": True,
    }
    if watermark_path:
        input_config["watermark_path"] = watermark_path
    if reference_video_id:
        input_config["reference_video_id"] = reference_video_id

    run = PipelineRun(
        user_id=context.user.id,
        project_id=project.id,
        engine=getattr(executor, "engine_name", "pipeline"),
        status="pending",
        input_config=json.dumps(input_config, ensure_ascii=False),
    )
    db.add(run)
    await db.flush()

    job = ExternalVideoJob(
        user_id=context.user.id,
        api_key_id=context.api_key.id,
        project_id=project.id,
        pipeline_run_id=run.id,
        client_reference_id=spec.client_reference_id,
        request_json=spec.model_dump_json(),
        idempotency_key_hash=idempotency_hash,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await db.refresh(run)

    launch_pipeline_task(
        executor,
        run.id,
        project.id,
        input_config,
        user_id=context.user.id,
        memory_service=memory_service,
        mem0=mem0,
    )
    return job, run, True


async def get_latest_agent_payload(db: AsyncSession, run_id: str) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AgentExecution)
        .where(AgentExecution.pipeline_run_id == run_id)
        .order_by(AgentExecution.created_at.asc())
    )
    items = []
    for execution in result.scalars().all():
        items.append(
            {
                "agent_name": execution.agent_name,
                "status": execution.status,
                "attempt_number": execution.attempt_number,
                "progress_text": execution.progress_text,
                "error_message": execution.error_message,
                "duration_ms": execution.duration_ms,
                "created_at": execution.created_at.isoformat() if execution.created_at else None,
                "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            }
        )
    return items


def ensure_completed_video_file(run: PipelineRun) -> str:
    if run.status != "completed":
        raise HTTPException(status_code=409, detail="Video job is not completed")
    if not run.final_video_path:
        raise HTTPException(status_code=404, detail="Final video not found")
    if not os.path.exists(run.final_video_path):
        raise HTTPException(status_code=404, detail="Final video file not found")
    return run.final_video_path
