from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_project_for_user
from app.config import settings
from app.database import get_db, async_session
from app.models.model_image import ModelImage
from app.models.talking_head import TalkingHeadTask
from app.models.generated_video import GeneratedVideo
from app.models.material import Material
from app.models.user import User
from app.schemas.talking_head import (
    ModelImageResponse,
    TalkingHeadCreate,
    TalkingHeadPromptUpdate,
    TalkingHeadResponse,
)
from app.services.image_compositor import ImageCompositor
from app.services.lipsync_generator import LipSyncGenerator

router = APIRouter(tags=["talking-head"])

# In-memory audio file registry (for serving uploaded audio files)
_audio_files: dict[str, dict] = {}


def get_talking_head_router(compositor: ImageCompositor, lipsync: LipSyncGenerator) -> APIRouter:

    # ── Model Image endpoints ──────────────────────────────────────────

    @router.post("/api/projects/{project_id}/model-images", response_model=ModelImageResponse)
    async def upload_model_image(
        project_id: str,
        file: UploadFile = File(...),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        upload_dir = os.path.join(settings.UPLOAD_DIR, "model_images", project_id)
        os.makedirs(upload_dir, exist_ok=True)

        file_id = str(uuid.uuid4())
        ext = os.path.splitext(file.filename or "image.png")[1]
        saved_name = f"{file_id}{ext}"
        saved_path = os.path.join(upload_dir, saved_name)

        content = await file.read()
        with open(saved_path, "wb") as f:
            f.write(content)

        model_img = ModelImage(
            id=file_id,
            project_id=project_id,
            filename=file.filename or saved_name,
            file_path=saved_path,
            file_size=len(content),
        )
        db.add(model_img)
        await db.commit()
        await db.refresh(model_img)

        return _model_image_response(model_img)

    @router.get("/api/projects/{project_id}/model-images", response_model=List[ModelImageResponse])
    async def list_model_images(
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        result = await db.execute(
            select(ModelImage)
            .where(ModelImage.project_id == project_id)
            .order_by(ModelImage.created_at.desc())
        )
        return [_model_image_response(img) for img in result.scalars().all()]

    @router.delete("/api/model-images/{image_id}")
    async def delete_model_image(
        image_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        img = await db.get(ModelImage, image_id)
        if not img:
            raise HTTPException(404, "Model image not found")
        await get_project_for_user(db, user.id, img.project_id)
        if img.file_path and os.path.exists(img.file_path):
            os.remove(img.file_path)
        await db.delete(img)
        await db.commit()
        return {"ok": True}

    @router.get("/api/model-images/{image_id}/file")
    async def get_model_image_file(
        image_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        img = await db.get(ModelImage, image_id)
        if not img or not img.file_path or not os.path.exists(img.file_path):
            raise HTTPException(404, "Image file not found")
        await get_project_for_user(db, user.id, img.project_id)
        return FileResponse(img.file_path)

    # ── Audio upload endpoints ─────────────────────────────────────────

    @router.post("/api/projects/{project_id}/talking-head-audio")
    async def upload_audio(
        project_id: str,
        file: UploadFile = File(...),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        upload_dir = os.path.join(settings.UPLOAD_DIR, "talking_head_audio", user.id, project_id)
        os.makedirs(upload_dir, exist_ok=True)

        file_id = str(uuid.uuid4())
        ext = os.path.splitext(file.filename or "audio.mp3")[1]
        saved_name = f"{file_id}{ext}"
        saved_path = os.path.join(upload_dir, saved_name)

        content = await file.read()
        with open(saved_path, "wb") as f:
            f.write(content)

        serve_url = f"/api/talking-head-audio/{file_id}/file"
        # Store mapping for serving
        _audio_files[file_id] = {
            "path": saved_path,
            "filename": file.filename or saved_name,
            "user_id": user.id,
        }

        return {
            "id": file_id,
            "filename": file.filename or saved_name,
            "file_url": serve_url,
            "file_size": len(content),
        }

    @router.get("/api/talking-head-audio/{audio_id}/file")
    async def get_audio_file(
        audio_id: str,
        user: User = Depends(get_current_user),
    ):
        info = _audio_files.get(audio_id)
        if not info or info.get("user_id") != user.id or not os.path.exists(info["path"]):
            raise HTTPException(404, "Audio file not found")
        return FileResponse(info["path"])

    # ── Talking Head Task endpoints ────────────────────────────────────

    @router.post("/api/projects/{project_id}/talking-head", response_model=TalkingHeadResponse)
    async def create_talking_head_task(
        project_id: str,
        body: TalkingHeadCreate,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        # Validate model image exists
        model_img = await db.get(ModelImage, body.model_image_id)
        if not model_img or model_img.project_id != project_id:
            raise HTTPException(400, "Model image not found in this project")

        task = TalkingHeadTask(
            project_id=project_id,
            model_image_id=body.model_image_id,
            bg_material_id=body.bg_material_id,
            shot_index=body.shot_index,
            motion_prompt=body.motion_prompt,
            audio_segment_url=body.audio_segment_url,
            audio_start_ms=body.audio_start_ms,
            audio_end_ms=body.audio_end_ms,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return await _task_response(task, db)

    @router.get("/api/projects/{project_id}/talking-head", response_model=List[TalkingHeadResponse])
    async def list_talking_head_tasks(
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        result = await db.execute(
            select(TalkingHeadTask)
            .where(TalkingHeadTask.project_id == project_id)
            .order_by(TalkingHeadTask.created_at)
        )
        responses = []
        for task in result.scalars().all():
            responses.append(await _task_response(task, db))
        return responses

    @router.get("/api/talking-head/{task_id}", response_model=TalkingHeadResponse)
    async def get_talking_head_task(
        task_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        task = await db.get(TalkingHeadTask, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        await get_project_for_user(db, user.id, task.project_id)
        return await _task_response(task, db)

    # ── Step B: Trigger composite ──────────────────────────────────────

    @router.post("/api/talking-head/{task_id}/composite", response_model=TalkingHeadResponse)
    async def trigger_composite(
        task_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        task = await db.get(TalkingHeadTask, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        await get_project_for_user(db, user.id, task.project_id)

        model_img = await db.get(ModelImage, task.model_image_id)
        if not model_img:
            raise HTTPException(400, "Model image not found")

        bg_image_path = ""
        if task.bg_material_id:
            mat = await db.get(Material, task.bg_material_id)
            if mat:
                bg_image_path = os.path.join(settings.MATERIALS_ROOT, mat.file_path)

        comp_task = await compositor.composite(
            model_image_path=model_img.file_path,
            bg_image_path=bg_image_path,
            prompt=task.motion_prompt or "",
        )

        task.composite_status = "processing"
        task.compositor_task_id = comp_task.task_id
        await db.commit()
        await db.refresh(task)

        asyncio.create_task(_poll_composite(task.id, compositor))

        return await _task_response(task, db)

    @router.get("/api/talking-head/{task_id}/composite-preview")
    async def get_composite_preview(
        task_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        task = await db.get(TalkingHeadTask, task_id)
        if not task or not task.composite_image_path or not os.path.exists(task.composite_image_path):
            raise HTTPException(404, "Composite image not found")
        await get_project_for_user(db, user.id, task.project_id)
        return FileResponse(task.composite_image_path)

    # ── Step C: Update prompt & audio ──────────────────────────────────

    @router.patch("/api/talking-head/{task_id}/prompt", response_model=TalkingHeadResponse)
    async def update_prompt_and_audio(
        task_id: str,
        body: TalkingHeadPromptUpdate,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        task = await db.get(TalkingHeadTask, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        await get_project_for_user(db, user.id, task.project_id)

        if body.motion_prompt is not None:
            task.motion_prompt = body.motion_prompt
        if body.audio_segment_url is not None:
            task.audio_segment_url = body.audio_segment_url
        if body.audio_start_ms is not None:
            task.audio_start_ms = body.audio_start_ms
        if body.audio_end_ms is not None:
            task.audio_end_ms = body.audio_end_ms

        await db.commit()
        await db.refresh(task)
        return await _task_response(task, db)

    # ── Step D: Trigger LipSync generation ─────────────────────────────

    @router.post("/api/talking-head/{task_id}/generate", response_model=TalkingHeadResponse)
    async def trigger_lipsync(
        task_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        task = await db.get(TalkingHeadTask, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        await get_project_for_user(db, user.id, task.project_id)

        # Use composite image if available, otherwise use original model image
        image_path = task.composite_image_path
        if not image_path:
            model_img = await db.get(ModelImage, task.model_image_id)
            if not model_img:
                raise HTTPException(400, "Model image not found")
            image_path = model_img.file_path

        audio_path = task.audio_segment_url or ""

        ls_task = await lipsync.generate(
            image_path=image_path,
            audio_path=audio_path,
            prompt=task.motion_prompt or "",
        )

        task.lipsync_status = "processing"
        task.lipsync_task_id = ls_task.task_id
        await db.commit()
        await db.refresh(task)

        asyncio.create_task(_poll_lipsync(task.id, task.project_id, lipsync))

        return await _task_response(task, db)

    @router.get("/api/talking-head/{task_id}/video")
    async def get_talking_head_video(
        task_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        task = await db.get(TalkingHeadTask, task_id)
        if not task or not task.video_path or not os.path.exists(task.video_path):
            raise HTTPException(404, "Video file not found")
        await get_project_for_user(db, user.id, task.project_id)
        return FileResponse(task.video_path, media_type="video/mp4")

    return router


# ── Background polling ─────────────────────────────────────────────────

async def _poll_composite(task_id: str, compositor: ImageCompositor):
    """Background task to poll composite status."""
    await asyncio.sleep(1)
    while True:
        async with async_session() as db:
            task = await db.get(TalkingHeadTask, task_id)
            if not task or task.composite_status not in ("pending", "processing"):
                break

            if task.compositor_task_id:
                status = await compositor.poll_status(task.compositor_task_id)
                if status.status == "completed":
                    task.composite_status = "completed"
                    # In mock mode, use model image as composite result
                    model_img = await db.get(ModelImage, task.model_image_id)
                    if model_img:
                        task.composite_image_path = model_img.file_path
                elif status.status == "failed":
                    task.composite_status = "failed"
                    task.error_message = status.error

            await db.commit()

            if task.composite_status in ("completed", "failed"):
                break

        await asyncio.sleep(2)


async def _poll_lipsync(task_id: str, project_id: str, lipsync: LipSyncGenerator):
    """Background task to poll lipsync status."""
    await asyncio.sleep(1)
    while True:
        async with async_session() as db:
            task = await db.get(TalkingHeadTask, task_id)
            if not task or task.lipsync_status not in ("pending", "processing"):
                break

            if task.lipsync_task_id:
                status = await lipsync.poll_status(task.lipsync_task_id)
                if status.status == "completed":
                    task.lipsync_status = "completed"
                    task.completed_at = datetime.now(timezone.utc)
                    task.duration_seconds = 5.0  # mock duration

                    # Create a GeneratedVideo record so it appears in timeline
                    gen_video = GeneratedVideo(
                        project_id=project_id,
                        prompt_id=None,  # No prompt association for talking head
                        material_id=task.bg_material_id,
                        status="completed",
                        video_path=task.video_path,
                        thumbnail_path=task.thumbnail_path,
                        duration_seconds=task.duration_seconds,
                        generation_type="talking_head",
                        talking_head_task_id=task.id,
                        completed_at=datetime.now(timezone.utc),
                    )
                    db.add(gen_video)
                elif status.status == "failed":
                    task.lipsync_status = "failed"
                    task.error_message = status.error

            await db.commit()

            if task.lipsync_status in ("completed", "failed"):
                break

        await asyncio.sleep(3)


# ── Response helpers ───────────────────────────────────────────────────

def _model_image_response(img: ModelImage) -> dict:
    return {
        "id": img.id,
        "project_id": img.project_id,
        "filename": img.filename,
        "file_url": f"/api/model-images/{img.id}/file",
        "width": img.width,
        "height": img.height,
        "created_at": img.created_at,
    }


async def _task_response(task: TalkingHeadTask, db: AsyncSession) -> dict:
    model_image_url = f"/api/model-images/{task.model_image_id}/file"

    bg_thumb = None
    if task.bg_material_id:
        mat = await db.get(Material, task.bg_material_id)
        if mat:
            bg_thumb = f"/api/materials/{mat.id}/thumbnail"

    return {
        "id": task.id,
        "project_id": task.project_id,
        "shot_index": task.shot_index,
        "model_image_id": task.model_image_id,
        "model_image_url": model_image_url,
        "bg_material_id": task.bg_material_id,
        "bg_thumbnail_url": bg_thumb,
        "composite_status": task.composite_status,
        "composite_preview_url": (
            f"/api/talking-head/{task.id}/composite-preview"
            if task.composite_image_path
            else None
        ),
        "motion_prompt": task.motion_prompt,
        "audio_segment_url": task.audio_segment_url,
        "audio_start_ms": task.audio_start_ms,
        "audio_end_ms": task.audio_end_ms,
        "lipsync_status": task.lipsync_status,
        "video_url": (
            f"/api/talking-head/{task.id}/video"
            if task.video_path
            else None
        ),
        "thumbnail_url": None,
        "duration_seconds": task.duration_seconds,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }
