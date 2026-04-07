from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.auto_chat import AutoChatSession
from app.models.user import User
from app.models.video_upload import VideoUpload
from app.models.video_delivery import VideoDelivery
from app.models.project import Project

router = APIRouter(prefix="/api/repository", tags=["repository"])


@router.get("/uploads")
async def list_user_uploads(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Return all VideoUpload records belonging to the current user (across all projects)."""
    result = await session.execute(
        select(VideoUpload, Project.name.label("project_name"))
        .join(Project, VideoUpload.project_id == Project.id)
        .where(Project.user_id == user.id)
        .order_by(VideoUpload.created_at.desc())
    )
    rows = result.all()

    items = []
    for upload, project_name in rows:
        items.append({
            "id": upload.id,
            "project_id": upload.project_id,
            "project_name": project_name,
            "filename": upload.filename,
            "file_size": upload.file_size,
            "duration_seconds": upload.duration_seconds,
            "mime_type": upload.mime_type,
            "stream_url": f"/api/uploads/{upload.id}/stream",
            "created_at": upload.created_at.isoformat(),
        })
    return items


@router.delete("/uploads/{upload_id}")
async def delete_user_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Delete a VideoUpload record belonging to the current user."""
    result = await session.execute(
        select(VideoUpload, Project)
        .join(Project, VideoUpload.project_id == Project.id)
        .where(VideoUpload.id == upload_id)
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="上传视频不存在")

    upload, project = row
    if project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权删除该上传视频")

    await session.execute(
        update(AutoChatSession)
        .where(AutoChatSession.reference_video_id == upload.id)
        .values(reference_video_id=None)
    )

    if upload.file_path and os.path.exists(upload.file_path):
        try:
            os.remove(upload.file_path)
        except OSError:
            pass

    await session.delete(upload)
    await session.commit()
    return {"ok": True}


@router.get("/deliveries")
async def list_user_deliveries(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Return all saved VideoDelivery records for the current user."""
    result = await session.execute(
        select(VideoDelivery, Project.name.label("project_name"))
        .join(Project, VideoDelivery.project_id == Project.id)
        .where(
            VideoDelivery.user_id == user.id,
            VideoDelivery.action_type == "save",
            VideoDelivery.platform == "repository",
            VideoDelivery.status == "saved",
        )
        .order_by(VideoDelivery.created_at.desc())
    )
    rows = result.all()

    items = []
    for delivery, project_name in rows:
        video_url = None
        if delivery.saved_video_path:
            path = delivery.saved_video_path.replace("\\", "/")
            marker = "/video_repository/"
            idx = path.rfind(marker)
            if idx >= 0:
                video_url = f"/repository/{path[idx + len(marker):]}"
            else:
                video_url = f"/repository/{os.path.basename(path)}"

        items.append({
            "id": delivery.id,
            "project_id": delivery.project_id,
            "project_name": project_name,
            "pipeline_run_id": delivery.pipeline_run_id,
            "title": delivery.title,
            "description": delivery.description,
            "status": delivery.status,
            "video_url": video_url,
            "created_at": delivery.created_at.isoformat(),
        })
    return items
