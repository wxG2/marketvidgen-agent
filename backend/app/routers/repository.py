from __future__ import annotations

import os
import shutil
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_auto_chat_session_for_user, get_current_user, get_project_for_user
from app.config import settings
from app.database import get_db
from app.models.auto_chat import AutoChatSession
from app.models.user import User
from app.models.video_upload import VideoUpload
from app.models.video_delivery import VideoDelivery
from app.models.project import Project
from app.models.repository_asset import RepositoryAsset
from app.schemas.video import VideoUploadResponse
from app.services.pipeline_artifact_repository import serialize_repository_asset

router = APIRouter(prefix="/api/repository", tags=["repository"])


def _build_unique_upload_filename(existing_filenames: set[str], base_name: str) -> str:
    candidate = (base_name or "reference_video.mp4").strip() or "reference_video.mp4"
    if candidate.lower() not in existing_filenames:
        return candidate

    stem, ext = os.path.splitext(candidate)
    suffix = ext or ".mp4"
    counter = 1
    while True:
        next_name = f"{stem}_{counter}{suffix}"
        if next_name.lower() not in existing_filenames:
            return next_name
        counter += 1


async def _copy_upload_into_project(
    *,
    db: AsyncSession,
    user_id: str,
    project_id: str,
    source_upload: VideoUpload,
    session_id: Optional[str] = None,
) -> VideoUpload:
    existing_rows = await db.execute(
        select(VideoUpload.filename)
        .join(Project, VideoUpload.project_id == Project.id)
        .where(Project.user_id == user_id)
    )
    existing_filenames = {str(name).lower() for name in existing_rows.scalars().all() if name}
    upload_filename = _build_unique_upload_filename(existing_filenames, source_upload.filename)

    upload_dir = os.path.join(settings.VIDEO_REPOSITORY_DIR, "uploads", user_id, project_id)
    os.makedirs(upload_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    target_path = os.path.join(upload_dir, f"{file_id}_{upload_filename}")
    shutil.copy2(source_upload.file_path, target_path)

    upload = VideoUpload(
        project_id=project_id,
        session_id=session_id,
        filename=upload_filename,
        file_path=target_path,
        file_size=os.path.getsize(target_path),
        duration_seconds=source_upload.duration_seconds,
        mime_type=source_upload.mime_type or "video/mp4",
    )
    db.add(upload)
    await db.flush()
    return upload


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


@router.post("/uploads/{upload_id}/import", response_model=VideoUploadResponse)
async def import_repository_upload(
    upload_id: str,
    project_id: str = Query(...),
    session_id: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    await get_project_for_user(session, user.id, project_id)
    chat_session = None
    if session_id:
        chat_session = await get_auto_chat_session_for_user(session, user.id, project_id, session_id)

    result = await session.execute(
        select(VideoUpload, Project)
        .join(Project, VideoUpload.project_id == Project.id)
        .where(VideoUpload.id == upload_id)
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="上传视频不存在")

    source_upload, source_project = row
    if source_project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权导入该上传视频")
    if not source_upload.file_path or not os.path.exists(source_upload.file_path):
        raise HTTPException(status_code=404, detail="上传视频文件不存在")

    target_session_id = chat_session.id if chat_session else None
    if source_upload.project_id == project_id and source_upload.session_id == target_session_id:
        upload = source_upload
    else:
        upload = await _copy_upload_into_project(
            db=session,
            user_id=user.id,
            project_id=project_id,
            source_upload=source_upload,
            session_id=target_session_id,
        )

    if chat_session is not None:
        chat_session.reference_video_id = upload.id
        chat_session.last_activity_at = upload.created_at
        chat_session.status_preview = "参考视频已从仓库选中"
        if chat_session.title in {"新会话", "默认会话"}:
            chat_session.title = (upload.filename or source_upload.filename or "参考视频")[:24]
        await session.commit()
    else:
        await session.commit()

    await session.refresh(upload)
    return upload


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

    from pathlib import Path as _Path
    repo_root = _Path(settings.VIDEO_REPOSITORY_DIR).resolve()

    items = []
    for delivery, project_name in rows:
        video_url = None
        if delivery.saved_video_path:
            try:
                abs_saved = _Path(delivery.saved_video_path).resolve()
                if abs_saved.exists():
                    suffix = str(abs_saved.relative_to(repo_root)).replace("\\", "/")
                    video_url = f"/repository/{suffix}"
                # else: file missing → video_url stays None (prevent black video box)
            except (ValueError, OSError):
                pass

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


@router.get("/assets")
async def list_user_repository_assets(
    project_id: Optional[str] = Query(None),
    pipeline_run_id: Optional[str] = Query(None),
    source_agent: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Return intermediate agent artifacts persisted for the current user."""
    conditions = [RepositoryAsset.user_id == user.id]
    if project_id:
        await get_project_for_user(session, user.id, project_id)
        conditions.append(RepositoryAsset.project_id == project_id)
    if pipeline_run_id:
        conditions.append(RepositoryAsset.pipeline_run_id == pipeline_run_id)

    result = await session.execute(
        select(RepositoryAsset, Project.name.label("project_name"))
        .join(Project, RepositoryAsset.project_id == Project.id)
        .where(*conditions)
        .order_by(RepositoryAsset.created_at.desc())
    )

    items = []
    for asset, project_name in result.all():
        item = serialize_repository_asset(asset, project_name=project_name)
        if source_agent and item["source_agent"] != source_agent:
            continue
        items.append(item)
    return items


@router.post("/deliveries/{delivery_id}/import", response_model=VideoUploadResponse)
async def import_repository_delivery(
    delivery_id: str,
    project_id: str = Query(...),
    session_id: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    await get_project_for_user(session, user.id, project_id)
    chat_session = None
    if session_id:
        chat_session = await get_auto_chat_session_for_user(session, user.id, project_id, session_id)

    result = await session.execute(
        select(VideoDelivery, Project)
        .join(Project, VideoDelivery.project_id == Project.id)
        .where(VideoDelivery.id == delivery_id)
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="仓库视频不存在")

    delivery, source_project = row
    if source_project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权导入该仓库视频")
    if not delivery.saved_video_path or not os.path.exists(delivery.saved_video_path):
        raise HTTPException(status_code=404, detail="仓库视频文件不存在")

    existing_rows = await session.execute(
        select(VideoUpload.filename)
        .join(Project, VideoUpload.project_id == Project.id)
        .where(Project.user_id == user.id)
    )
    existing_filenames = {str(name).lower() for name in existing_rows.scalars().all() if name}

    base_name = os.path.basename(delivery.saved_video_path)
    upload_filename = _build_unique_upload_filename(existing_filenames, base_name)
    upload_dir = os.path.join(settings.VIDEO_REPOSITORY_DIR, "uploads", user.id, project_id)
    os.makedirs(upload_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    target_path = os.path.join(upload_dir, f"{file_id}_{upload_filename}")
    shutil.copy2(delivery.saved_video_path, target_path)

    mime_type = "video/mp4"
    upload = VideoUpload(
        project_id=project_id,
        session_id=chat_session.id if chat_session else None,
        filename=upload_filename,
        file_path=target_path,
        file_size=os.path.getsize(target_path),
        mime_type=mime_type,
    )
    session.add(upload)
    await session.flush()

    if chat_session is not None:
        chat_session.reference_video_id = upload.id
        chat_session.last_activity_at = upload.created_at
        chat_session.status_preview = "参考视频已从仓库选中"
        if chat_session.title in {"新会话", "默认会话"}:
            chat_session.title = (delivery.title or upload.filename or "参考视频")[:24]

    await session.commit()
    await session.refresh(upload)
    return upload
