from __future__ import annotations

import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.video_upload import VideoUpload
from app.models.video_analysis import VideoAnalysis
from app.models.project import Project
from app.schemas.video import VideoAnalysisResponse
from app.services.video_analyzer import VideoAnalyzer

router = APIRouter(tags=["analysis"])


async def _run_analysis(analysis_id: str, video_path: str, analyzer: VideoAnalyzer, categories: list[str]):
    async with async_session() as db:
        analysis = await db.get(VideoAnalysis, analysis_id)
        if not analysis:
            return
        try:
            analysis.status = "processing"
            await db.commit()

            result = await analyzer.analyze(video_path, categories)

            analysis.status = "completed"
            analysis.summary = result.summary
            analysis.scene_tags = json.dumps(result.scene_tags, ensure_ascii=False)
            analysis.recommended_categories = json.dumps(result.recommended_categories, ensure_ascii=False)
            analysis.raw_response = result.raw_response
            analysis.completed_at = datetime.now(timezone.utc)
        except Exception as e:
            analysis.status = "failed"
            analysis.error_message = str(e)
        await db.commit()


def get_analysis_router(analyzer: VideoAnalyzer) -> APIRouter:
    @router.post("/api/projects/{project_id}/analyze", response_model=VideoAnalysisResponse)
    async def trigger_analysis(
        project_id: str,
        background_tasks: BackgroundTasks,
        db: AsyncSession = Depends(get_db),
    ):
        project = await db.get(Project, project_id)
        if not project:
            raise HTTPException(404, "Project not found")

        upload_result = await db.execute(
            select(VideoUpload).where(VideoUpload.project_id == project_id).order_by(VideoUpload.created_at.desc()).limit(1)
        )
        upload = upload_result.scalar_one_or_none()
        if not upload:
            raise HTTPException(400, "No video uploaded for this project")

        # Get available categories
        from app.services.material_service import get_categories
        cats = await get_categories(db)
        category_names = [c["name"] for c in cats]

        analysis = VideoAnalysis(project_id=project_id, video_upload_id=upload.id)
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)

        background_tasks.add_task(_run_analysis, analysis.id, upload.file_path, analyzer, category_names)
        return _to_response(analysis)

    @router.get("/api/projects/{project_id}/analysis", response_model=VideoAnalysisResponse)
    async def get_analysis(project_id: str, db: AsyncSession = Depends(get_db)):
        result = await db.execute(
            select(VideoAnalysis)
            .where(VideoAnalysis.project_id == project_id)
            .order_by(VideoAnalysis.created_at.desc())
            .limit(1)
        )
        analysis = result.scalar_one_or_none()
        if not analysis:
            raise HTTPException(404, "No analysis found")
        return _to_response(analysis)

    return router


def _to_response(analysis: VideoAnalysis) -> dict:
    return {
        "id": analysis.id,
        "project_id": analysis.project_id,
        "status": analysis.status,
        "summary": analysis.summary,
        "scene_tags": json.loads(analysis.scene_tags) if analysis.scene_tags else None,
        "recommended_categories": json.loads(analysis.recommended_categories) if analysis.recommended_categories else None,
        "error_message": analysis.error_message,
        "created_at": analysis.created_at,
        "completed_at": analysis.completed_at,
    }
