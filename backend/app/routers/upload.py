from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_auto_chat_session_for_user, get_current_user, get_project_for_user
from app.core.config import settings
from app.db.session import get_db
from app.models.auto_chat import AutoSessionReferenceVideoSelection
from app.models.project import Project
from app.models.user import User
from app.models.video_upload import VideoUpload
from app.schemas.video import VideoUploadResponse
from app.services.upload_validation import validate_upload_file

router = APIRouter(tags=["upload"])


@router.post("/api/projects/{project_id}/upload", response_model=VideoUploadResponse)
async def upload_video(
    project_id: str,
    session_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = None
    if session_id:
        session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)

    validated = await validate_upload_file(file, kind="video")
    filename = validated.filename
    duplicate = (
        await db.execute(
            select(VideoUpload.id)
            .join(Project, VideoUpload.project_id == Project.id)
            .where(
                Project.user_id == user.id,
                func.lower(VideoUpload.filename) == filename.lower(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"视频文件「{filename}」已存在于仓库中，请勿重复上传")

    upload_dir = os.path.join(settings.VIDEO_REPOSITORY_DIR, "uploads", user.id, project_id)
    os.makedirs(upload_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    safe_name = f"{file_id}_{filename}"
    file_path = os.path.join(upload_dir, safe_name)

    with open(file_path, "wb") as f:
        f.write(validated.content)

    upload = VideoUpload(
        project_id=project_id,
        session_id=session.id if session else None,
        filename=filename,
        file_path=file_path,
        file_size=validated.file_size,
        mime_type=validated.content_type,
    )
    db.add(upload)
    await db.flush()
    if session is not None:
        session.reference_video_id = upload.id
        session.last_activity_at = upload.created_at
        session.status_preview = "参考视频已上传"
        if session.title in {"新会话", "默认会话"}:
            session.title = (upload.filename or "参考视频")[:24]
        await db.execute(
            delete(AutoSessionReferenceVideoSelection)
            .where(AutoSessionReferenceVideoSelection.session_id == session.id)
        )
        db.add(
            AutoSessionReferenceVideoSelection(
                session_id=session.id,
                video_upload_id=upload.id,
                sort_order=0,
            )
        )
    await db.commit()
    await db.refresh(upload)
    return upload


@router.get("/api/projects/{project_id}/upload", response_model=Optional[VideoUploadResponse])
async def get_upload(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
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
async def stream_video(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    upload = await db.get(VideoUpload, upload_id)
    if not upload or not os.path.exists(upload.file_path):
        raise HTTPException(404, "Video not found")
    await get_project_for_user(db, user.id, upload.project_id)
    if not os.path.exists(upload.file_path):
        raise HTTPException(404, "Video not found")
    return FileResponse(upload.file_path, media_type=upload.mime_type or "video/mp4")
