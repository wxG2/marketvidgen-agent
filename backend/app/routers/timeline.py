from __future__ import annotations

import os
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_project_for_user
from app.config import settings
from app.database import get_db
from app.models.timeline import TimelineClip, TimelineAsset
from app.models.generated_video import GeneratedVideo
from app.models.user import User
from app.schemas.timeline import (
    TimelineAssetResponse, TimelineClipResponse,
    TimelineSaveRequest, TimelineResponse,
)

router = APIRouter(tags=["timeline"])

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"}
SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".lrc"}


def _detect_asset_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    return "video"  # fallback


async def _clip_to_response(clip: TimelineClip, db: AsyncSession) -> dict:
    video_url = None
    thumbnail_url = None
    filename = clip.label

    if clip.generated_video_id:
        video = await db.get(GeneratedVideo, clip.generated_video_id)
        if video and video.video_path:
            video_url = f"/api/generations/{clip.generated_video_id}/video"

    if clip.asset_id:
        asset = await db.get(TimelineAsset, clip.asset_id)
        if asset:
            video_url = f"/api/timeline/assets/{asset.id}/file"
            filename = filename or asset.filename

    return {
        "id": clip.id,
        "generated_video_id": clip.generated_video_id,
        "asset_id": clip.asset_id,
        "track_type": clip.track_type,
        "track_index": clip.track_index,
        "position_ms": clip.position_ms,
        "duration_ms": clip.duration_ms,
        "sort_order": clip.sort_order,
        "label": clip.label,
        "video_url": video_url,
        "thumbnail_url": thumbnail_url,
        "filename": filename,
    }


@router.get("/api/projects/{project_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    result = await db.execute(
        select(TimelineClip)
        .where(TimelineClip.project_id == project_id)
        .order_by(TimelineClip.track_type, TimelineClip.track_index, TimelineClip.sort_order)
    )
    clips = list(result.scalars().all())
    clip_responses = []
    for clip in clips:
        clip_responses.append(await _clip_to_response(clip, db))

    # Also return project assets
    asset_result = await db.execute(
        select(TimelineAsset)
        .where(TimelineAsset.project_id == project_id)
        .order_by(TimelineAsset.created_at)
    )
    assets = []
    for a in asset_result.scalars().all():
        assets.append({
            "id": a.id,
            "project_id": a.project_id,
            "asset_type": a.asset_type,
            "filename": a.filename,
            "file_url": f"/api/timeline/assets/{a.id}/file",
            "file_size": a.file_size,
            "duration_ms": a.duration_ms,
        })

    return {"project_id": project_id, "clips": clip_responses, "assets": assets}


@router.put("/api/projects/{project_id}/timeline", response_model=TimelineResponse)
async def save_timeline(
    project_id: str,
    data: TimelineSaveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    await db.execute(delete(TimelineClip).where(TimelineClip.project_id == project_id))

    clips = []
    for cd in data.clips:
        clip = TimelineClip(
            project_id=project_id,
            generated_video_id=cd.generated_video_id,
            asset_id=cd.asset_id,
            track_type=cd.track_type,
            track_index=cd.track_index,
            position_ms=cd.position_ms,
            duration_ms=cd.duration_ms,
            sort_order=cd.sort_order,
            label=cd.label,
        )
        db.add(clip)
        clips.append(clip)

    await db.commit()
    for c in clips:
        await db.refresh(c)

    clip_responses = []
    for clip in clips:
        clip_responses.append(await _clip_to_response(clip, db))

    return {"project_id": project_id, "clips": clip_responses, "assets": []}


@router.post("/api/projects/{project_id}/timeline/assets", response_model=TimelineAssetResponse)
async def upload_timeline_asset(
    project_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a local video, audio, or subtitle file for timeline use."""
    await get_project_for_user(db, user.id, project_id)
    filename = file.filename or "unnamed"
    asset_type = _detect_asset_type(filename)

    asset_dir = os.path.join(settings.GENERATED_DIR, project_id, "assets")
    os.makedirs(asset_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    file_path = os.path.join(asset_dir, unique_name)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    asset = TimelineAsset(
        project_id=project_id,
        asset_type=asset_type,
        filename=filename,
        file_path=file_path,
        file_size=len(content),
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "asset_type": asset.asset_type,
        "filename": asset.filename,
        "file_url": f"/api/timeline/assets/{asset.id}/file",
        "file_size": asset.file_size,
        "duration_ms": asset.duration_ms,
    }


@router.get("/api/timeline/assets/{asset_id}/file")
async def serve_asset_file(
    asset_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(TimelineAsset, asset_id)
    if not asset or not os.path.exists(asset.file_path):
        raise HTTPException(404, "Asset file not found")
    await get_project_for_user(db, user.id, asset.project_id)
    return FileResponse(asset.file_path)


@router.delete("/api/timeline/assets/{asset_id}")
async def delete_asset(
    asset_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(TimelineAsset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    await get_project_for_user(db, user.id, asset.project_id)
    if os.path.exists(asset.file_path):
        os.remove(asset.file_path)
    # Remove clips referencing this asset
    await db.execute(delete(TimelineClip).where(TimelineClip.asset_id == asset_id))
    await db.delete(asset)
    await db.commit()
    return {"ok": True}
