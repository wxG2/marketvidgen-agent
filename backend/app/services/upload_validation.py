from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from fastapi import HTTPException, UploadFile

from app.config import settings

UploadKind = Literal["image", "video", "audio", "subtitle", "timeline_asset"]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".lrc"}
TIMELINE_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | SUBTITLE_EXTENSIONS | {
    ".mkv",
    ".flv",
    ".wmv",
    ".m4v",
    ".wma",
}

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._() \-\u4e00-\u9fff]+")


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    content: bytes
    content_type: str
    extension: str
    file_size: int


def sanitize_upload_filename(raw_filename: str | None, *, fallback: str = "upload") -> str:
    raw = (raw_filename or fallback).replace("\x00", "").strip()
    # Browsers may submit either POSIX or Windows style relative paths.
    name = PureWindowsPath(PurePosixPath(raw).name).name.strip()
    if not name or name in {".", ".."}:
        name = fallback
    name = _FILENAME_SAFE_RE.sub("_", name).strip(" .")
    if not name:
        name = fallback
    return name[:180]


async def validate_upload_file(file: UploadFile, *, kind: UploadKind) -> ValidatedUpload:
    filename = sanitize_upload_filename(file.filename, fallback=_fallback_name(kind))
    extension = _extension(filename)
    content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    max_bytes = _max_size_bytes(kind)

    _validate_extension(kind, extension)
    _validate_declared_content_type(kind, content_type)

    content = await _read_with_limit(file, max_bytes=max_bytes)
    if not content:
        raise HTTPException(status_code=400, detail="上传文件不能为空")

    _validate_file_signature(kind, extension, content)
    return ValidatedUpload(
        filename=filename,
        content=content,
        content_type=content_type,
        extension=extension,
        file_size=len(content),
    )


def _fallback_name(kind: UploadKind) -> str:
    return {
        "image": "image.png",
        "video": "video.mp4",
        "audio": "audio.mp3",
        "subtitle": "subtitle.srt",
        "timeline_asset": "asset",
    }[kind]


def _extension(filename: str) -> str:
    pos = filename.rfind(".")
    return filename[pos:].lower() if pos >= 0 else ""


def _max_size_bytes(kind: UploadKind) -> int:
    size_mb = {
        "image": settings.MAX_IMAGE_SIZE_MB,
        "video": settings.MAX_UPLOAD_SIZE_MB,
        "audio": settings.MAX_AUDIO_SIZE_MB,
        "subtitle": 5,
        "timeline_asset": settings.MAX_TIMELINE_ASSET_SIZE_MB,
    }[kind]
    return int(size_mb) * 1024 * 1024


async def _read_with_limit(file: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"上传文件超过大小限制（{max_bytes // 1024 // 1024}MB）")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_extension(kind: UploadKind, extension: str) -> None:
    allowed = {
        "image": IMAGE_EXTENSIONS,
        "video": VIDEO_EXTENSIONS,
        "audio": AUDIO_EXTENSIONS,
        "subtitle": SUBTITLE_EXTENSIONS,
        "timeline_asset": TIMELINE_EXTENSIONS,
    }[kind]
    if extension not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件扩展名：{extension or '无扩展名'}")


def _validate_declared_content_type(kind: UploadKind, content_type: str) -> None:
    if content_type == "application/octet-stream":
        return
    if kind == "timeline_asset":
        allowed = (
            set(settings.ALLOWED_VIDEO_TYPES)
            | set(settings.ALLOWED_AUDIO_TYPES)
            | set(settings.ALLOWED_SUBTITLE_TYPES)
        )
    else:
        allowed = {
            "image": set(settings.ALLOWED_IMAGE_TYPES),
            "video": set(settings.ALLOWED_VIDEO_TYPES),
            "audio": set(settings.ALLOWED_AUDIO_TYPES),
            "subtitle": set(settings.ALLOWED_SUBTITLE_TYPES),
        }[kind]
    if content_type not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{content_type}")


def _validate_file_signature(kind: UploadKind, extension: str, content: bytes) -> None:
    effective_kind = _kind_from_timeline_extension(extension) if kind == "timeline_asset" else kind
    if effective_kind == "image" and not _looks_like_image(extension, content):
        raise HTTPException(status_code=400, detail="图片文件内容与扩展名不匹配")
    if effective_kind == "video" and not _looks_like_video(extension, content):
        raise HTTPException(status_code=400, detail="视频文件内容与扩展名不匹配")
    if effective_kind == "audio" and not _looks_like_audio(extension, content):
        raise HTTPException(status_code=400, detail="音频文件内容与扩展名不匹配")
    if effective_kind == "subtitle" and not _looks_like_text(content):
        raise HTTPException(status_code=400, detail="字幕文件必须是可读取的文本")


def _kind_from_timeline_extension(extension: str) -> Literal["video", "audio", "subtitle"]:
    if extension in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if extension in AUDIO_EXTENSIONS or extension == ".wma":
        return "audio"
    return "video"


def _looks_like_image(extension: str, content: bytes) -> bool:
    header = content[:16]
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return header.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def _looks_like_video(extension: str, content: bytes) -> bool:
    header = content[:64]
    if extension in {".mp4", ".mov", ".m4v"}:
        return b"ftyp" in header[:32]
    if extension == ".webm" or extension == ".mkv":
        return header.startswith(b"\x1aE\xdf\xa3")
    if extension == ".avi":
        return header.startswith(b"RIFF") and b"AVI" in header[:16]
    if extension in {".flv", ".wmv"}:
        return True
    return False


def _looks_like_audio(extension: str, content: bytes) -> bool:
    header = content[:64]
    if extension == ".mp3":
        return header.startswith(b"ID3") or header[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}
    if extension == ".wav":
        return header.startswith(b"RIFF") and content[8:12] == b"WAVE"
    if extension == ".ogg":
        return header.startswith(b"OggS")
    if extension == ".flac":
        return header.startswith(b"fLaC")
    if extension in {".m4a", ".aac"}:
        return b"ftyp" in header[:32] or header.startswith(b"\xff\xf1") or header.startswith(b"\xff\xf9")
    if extension == ".webm":
        return header.startswith(b"\x1aE\xdf\xa3")
    if extension == ".wma":
        return True
    return False


def _looks_like_text(content: bytes) -> bool:
    sample = content[:4096]
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
