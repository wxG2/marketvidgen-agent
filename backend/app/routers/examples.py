from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter

from app.config import settings


router = APIRouter(tags=["examples"])

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _asset_type(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "file"


@router.get("/api/examples")
async def list_examples():
    root = Path(settings.EXAMPLES_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    categories: list[dict] = []
    for category_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        files = []
        for file_path in sorted(p for p in category_dir.rglob("*") if p.is_file()):
            rel_path = file_path.relative_to(root).as_posix()
            files.append({
                "name": file_path.name,
                "relative_path": rel_path,
                "url": f"/examples/{quote(rel_path)}",
                "asset_type": _asset_type(file_path),
                "size": file_path.stat().st_size,
            })

        categories.append({
            "name": category_dir.name,
            "files": files,
        })

    return {"categories": categories}
