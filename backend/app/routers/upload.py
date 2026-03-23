from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.models.video_upload import VideoUpload
from app.schemas.video import VideoUploadResponse

router = APIRouter(tags=["upload"])


@router.post("/api/projects/{project_id}/upload", response_model=VideoUploadResponse)
async def upload_video(
    project_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    upload_dir = os.path.join(settings.UPLOAD_DIR, project_id)
    os.makedirs(upload_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    safe_name = f"{file_id}_{file.filename}"
    file_path = os.path.join(upload_dir, safe_name)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    upload = VideoUpload(
        project_id=project_id,
        filename=file.filename or "video",
        file_path=file_path,
        file_size=len(content),
        mime_type=file.content_type,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)
    return upload


@router.get("/api/projects/{project_id}/upload", response_model=Optional[VideoUploadResponse])
async def get_upload(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(VideoUpload)
        .where(VideoUpload.project_id == project_id)
        .order_by(VideoUpload.created_at.desc())
        .limit(1)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise HTTPException(404, "No upload found")
    return upload


@router.get("/api/uploads/{upload_id}/stream")
async def stream_video(upload_id: str, db: AsyncSession = Depends(get_db)):
    upload = await db.get(VideoUpload, upload_id)
    if not upload or not os.path.exists(upload.file_path):
        raise HTTPException(404, "Video not found")
    return FileResponse(upload.file_path, media_type=upload.mime_type or "video/mp4")
