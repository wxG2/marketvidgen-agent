from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_project_for_user
from app.config import settings
from app.database import get_db, async_session
from app.models.prompt import Prompt
from app.models.material import Material
from app.models.material_selection import MaterialSelection
from app.models.generated_video import GeneratedVideo
from app.models.user import User
from app.schemas.generation import GeneratedVideoResponse
from app.services.video_generator import VideoGenerator

router = APIRouter(tags=["generation"])


def get_generation_router(generator: VideoGenerator) -> APIRouter:

    @router.post("/api/projects/{project_id}/generate", response_model=List[GeneratedVideoResponse])
    async def start_generation(
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        prompt_result = await db.execute(
            select(Prompt).where(Prompt.project_id == project_id).order_by(Prompt.created_at)
        )
        prompts = list(prompt_result.scalars().all())
        if not prompts:
            raise HTTPException(400, "No prompts found")

        gen_dir = os.path.join(settings.GENERATED_DIR, project_id)
        os.makedirs(gen_dir, exist_ok=True)

        videos = []
        for prompt in prompts:
            # Find associated material
            material_id = None
            image_path = ""
            if prompt.material_selection_id:
                sel = await db.get(MaterialSelection, prompt.material_selection_id)
                if sel:
                    material_id = sel.material_id
                    mat = await db.get(Material, sel.material_id)
                    if mat:
                        image_path = os.path.join(settings.MATERIALS_ROOT, mat.file_path)

            task = await generator.generate(image_path, prompt.prompt_text)

            gen_video = GeneratedVideo(
                project_id=project_id,
                prompt_id=prompt.id,
                material_id=material_id,
                status="processing",
                kling_task_id=task.task_id,
            )
            db.add(gen_video)
            videos.append(gen_video)

        await db.commit()
        for v in videos:
            await db.refresh(v)

        # Start background polling
        asyncio.create_task(_poll_generations(project_id, generator))

        responses = []
        for v in videos:
            responses.append(await _to_response_with_binding(v, db))
        return responses

    @router.get("/api/projects/{project_id}/generations", response_model=List[GeneratedVideoResponse])
    async def get_generations(
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        result = await db.execute(
            select(GeneratedVideo)
            .where(GeneratedVideo.project_id == project_id)
            .order_by(GeneratedVideo.created_at)
        )
        responses = []
        for v in result.scalars().all():
            responses.append(await _to_response_with_binding(v, db))
        return responses

    @router.post("/api/projects/{project_id}/generations/{gen_id}/select")
    async def select_video(
        project_id: str,
        gen_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        video = await db.get(GeneratedVideo, gen_id)
        if not video or video.project_id != project_id:
            raise HTTPException(404, "Video not found")
        video.is_selected = True
        await db.commit()
        return {"ok": True}

    @router.post("/api/projects/{project_id}/generations/{gen_id}/deselect")
    async def deselect_video(
        project_id: str,
        gen_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        video = await db.get(GeneratedVideo, gen_id)
        if not video or video.project_id != project_id:
            raise HTTPException(404, "Video not found")
        video.is_selected = False
        await db.commit()
        return {"ok": True}

    @router.get("/api/projects/{project_id}/selected-videos", response_model=List[GeneratedVideoResponse])
    async def get_selected_videos(
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_for_user(db, user.id, project_id)
        result = await db.execute(
            select(GeneratedVideo)
            .where(GeneratedVideo.project_id == project_id, GeneratedVideo.is_selected == True)
            .order_by(GeneratedVideo.created_at)
        )
        responses = []
        for v in result.scalars().all():
            responses.append(await _to_response_with_binding(v, db))
        return responses

    @router.post("/api/projects/{project_id}/generate-single/{prompt_id}", response_model=GeneratedVideoResponse)
    async def generate_single(
        project_id: str,
        prompt_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Generate video for a single prompt binding."""
        await get_project_for_user(db, user.id, project_id)
        prompt = await db.get(Prompt, prompt_id)
        if not prompt or prompt.project_id != project_id:
            raise HTTPException(404, "Prompt not found")

        gen_dir = os.path.join(settings.GENERATED_DIR, project_id)
        os.makedirs(gen_dir, exist_ok=True)

        material_id = None
        image_path = ""
        if prompt.material_selection_id:
            sel = await db.get(MaterialSelection, prompt.material_selection_id)
            if sel:
                material_id = sel.material_id
                mat = await db.get(Material, sel.material_id)
                if mat:
                    image_path = os.path.join(settings.MATERIALS_ROOT, mat.file_path)

        task = await generator.generate(image_path, prompt.prompt_text)

        gen_video = GeneratedVideo(
            project_id=project_id,
            prompt_id=prompt.id,
            material_id=material_id,
            status="processing",
            kling_task_id=task.task_id,
        )
        db.add(gen_video)
        await db.commit()
        await db.refresh(gen_video)

        asyncio.create_task(_poll_generations(project_id, generator))
        return await _to_response_with_binding(gen_video, db)

    @router.get("/api/generations/{gen_id}/video")
    async def stream_generated_video(
        gen_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        video = await db.get(GeneratedVideo, gen_id)
        if not video or not video.video_path or not os.path.exists(video.video_path):
            raise HTTPException(404, "Video file not found")
        await get_project_for_user(db, user.id, video.project_id)
        return FileResponse(video.video_path, media_type="video/mp4")

    return router


async def _poll_generations(project_id: str, generator: VideoGenerator):
    """Background task to poll generation status."""
    await asyncio.sleep(1)
    while True:
        async with async_session() as db:
            result = await db.execute(
                select(GeneratedVideo).where(
                    GeneratedVideo.project_id == project_id,
                    GeneratedVideo.status.in_(["pending", "processing"]),
                )
            )
            pending = list(result.scalars().all())
            if not pending:
                break

            for video in pending:
                if video.kling_task_id:
                    status = await generator.poll_status(video.kling_task_id)
                    if status.status == "completed":
                        video.status = "completed"
                        video.completed_at = datetime.now(timezone.utc)
                        video.duration_seconds = 5.0  # mock duration
                    elif status.status == "failed":
                        video.status = "failed"
                        video.error_message = status.error

            await db.commit()
        await asyncio.sleep(3)


async def _to_response_with_binding(v: GeneratedVideo, db: AsyncSession) -> dict:
    prompt_text = None
    mat_filename = None
    mat_category = None
    mat_thumb = None

    prompt = await db.get(Prompt, v.prompt_id)
    if prompt:
        prompt_text = prompt.prompt_text

    if v.material_id:
        mat = await db.get(Material, v.material_id)
        if mat:
            mat_filename = mat.filename
            mat_category = mat.category
            mat_thumb = f"/api/materials/{mat.id}/thumbnail"

    return {
        "id": v.id,
        "project_id": v.project_id,
        "prompt_id": v.prompt_id,
        "material_id": v.material_id,
        "status": v.status,
        "video_url": f"/api/generations/{v.id}/video" if v.video_path else None,
        "thumbnail_url": None,
        "duration_seconds": v.duration_seconds,
        "is_selected": v.is_selected,
        "error_message": v.error_message,
        "created_at": v.created_at,
        "completed_at": v.completed_at,
        "prompt_text": prompt_text,
        "material_filename": mat_filename,
        "material_category": mat_category,
        "material_thumbnail_url": mat_thumb,
    }
