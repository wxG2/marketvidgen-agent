from __future__ import annotations

from fastapi import APIRouter

from app.services.artifact_cleanup import cleanup_old_artifacts

router = APIRouter(tags=["system"])


@router.get("/api/health")
async def health():
    return {"status": "ok"}


@router.post("/api/admin/cleanup-artifacts")
async def cleanup_artifacts(retention_days: int = 7):
    """Manually trigger artifact cleanup."""
    return await cleanup_old_artifacts(retention_days=retention_days)
