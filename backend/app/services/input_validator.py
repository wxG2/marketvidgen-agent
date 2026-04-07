"""Input validation and security helpers.

Used at system boundaries (API endpoints, agent file access) to prevent
common vulnerabilities such as path traversal, oversized uploads, and
unsupported file types.  Also provides a light-weight LLM output sanitiser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

# ── Constants (defaults; real limits come from settings at call sites) ────────

ALLOWED_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)
ALLOWED_VIDEO_MIME_TYPES: frozenset[str] = frozenset(
    {"video/mp4", "video/quicktime", "video/webm", "video/avi", "video/x-msvideo"}
)
MAX_UPLOAD_SIZE_BYTES: int = 500 * 1024 * 1024   # 500 MB
MAX_IMAGE_SIZE_BYTES: int = 50 * 1024 * 1024     # 50 MB

# Patterns that indicate path traversal attempts
_PATH_TRAVERSAL_RE = re.compile(
    r"\.\.[/\\]|%2e%2e[/\\]|%252e%252e|\.\.%2f|\.\.%5c",
    re.IGNORECASE,
)
_NULL_BYTE_RE = re.compile(r"\x00")


# ── Filename / path validation ────────────────────────────────────────────────


def validate_filename(filename: str) -> str:
    """Sanitize and validate an upload filename.

    Strips directory components and rejects names that contain path-traversal
    sequences or null bytes.

    Returns:
        The sanitized base filename.

    Raises:
        HTTPException(400): On invalid or dangerous filename.
    """
    if not filename or not filename.strip():
        raise HTTPException(status_code=400, detail="Filename cannot be empty")

    if _PATH_TRAVERSAL_RE.search(filename) or _NULL_BYTE_RE.search(filename):
        raise HTTPException(
            status_code=400, detail="Invalid filename: path traversal detected"
        )

    # Keep only the final component (strip directories)
    clean = Path(filename).name
    if not clean or clean in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    return clean


# ── MIME type validation ──────────────────────────────────────────────────────


def validate_content_type(
    content_type: Optional[str],
    allowed: frozenset[str],
    label: str = "file",
) -> None:
    """Raise ``HTTPException(400)`` when *content_type* is not in *allowed*.

    Strips MIME parameters before comparison (e.g. ``image/jpeg; charset=utf-8``
    → ``image/jpeg``).
    """
    if not content_type:
        raise HTTPException(
            status_code=400, detail=f"{label} content type is required"
        )

    mime = content_type.split(";")[0].strip().lower()
    if mime not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported {label} type '{mime}'. "
                f"Allowed: {sorted(allowed)}"
            ),
        )


# ── File size validation ──────────────────────────────────────────────────────


def validate_file_size(
    size_bytes: int,
    max_bytes: int = MAX_UPLOAD_SIZE_BYTES,
    label: str = "file",
) -> None:
    """Raise ``HTTPException(413)`` when *size_bytes* exceeds *max_bytes*."""
    if size_bytes > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"{label} too large. Maximum allowed size is {max_mb} MB",
        )


# ── Agent file-access permission check ───────────────────────────────────────


def validate_agent_file_access(
    file_path: str,
    allowed_dirs: list[str],
) -> Path:
    """Verify that *file_path* resolves within one of *allowed_dirs*.

    Prevents agents from reading or writing arbitrary filesystem paths
    (i.e. enforces a minimal sandbox).

    Returns:
        The resolved ``Path`` object.

    Raises:
        ValueError: When the path is outside every allowed directory.
    """
    try:
        path = Path(file_path).resolve()
    except Exception as exc:
        raise ValueError(f"Invalid file path '{file_path}': {exc}") from exc

    for allowed_dir in allowed_dirs:
        try:
            allowed = Path(allowed_dir).resolve()
            path.relative_to(allowed)   # raises ValueError if not under allowed
            return path
        except ValueError:
            continue

    raise ValueError(
        f"File access denied: '{file_path}' is outside allowed directories: "
        f"{allowed_dirs}"
    )


# ── LLM output sanitisation ───────────────────────────────────────────────────


def sanitize_llm_output(text: str, max_length: int = 50_000) -> str:
    """Remove obviously dangerous content from LLM-generated text.

    Currently:
    - Strips null bytes (can cause issues in downstream processing).
    - Truncates excessively long outputs (guard against token-stuffing).

    This is a lightweight defence-in-depth measure, not a comprehensive
    content-safety filter.
    """
    if not text:
        return text

    text = _NULL_BYTE_RE.sub("", text)

    if len(text) > max_length:
        text = text[:max_length] + "… [output truncated]"

    return text
