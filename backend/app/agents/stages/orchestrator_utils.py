from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.stages.requirement_utils import (
    fast_infer_duration,
    fast_infer_platform,
    fast_infer_style,
    looks_like_meta_instruction as _looks_like_meta_instruction,
)
from app.core.config import settings


def _snap_to_supported(value: float, supported: list[int]) -> int:
    """Round a duration to the nearest model-supported value."""
    return min(supported, key=lambda s: abs(s - value))


def _as_string_or_empty(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_duration(value: object, fallback: int) -> int:
    try:
        duration = int(float(str(value).strip()))
    except (TypeError, ValueError):
        duration = int(fallback)
    return max(5, min(duration, 300))


def _normalize_platform(value: object, fallback: str = "generic") -> str:
    platform = _as_string_or_empty(value).strip().lower()
    if platform in settings.PLATFORM_RESOLUTIONS:
        return platform
    return fallback if fallback in settings.PLATFORM_RESOLUTIONS else "generic"


def _summarize_image_asset(asset: dict[str, Any]) -> str:
    parts = []
    filename = _as_string_or_empty(asset.get("filename") or Path(asset.get("image_path", "")).name).strip()
    if filename:
        parts.append(f"文件 {filename}")
    if asset.get("width") and asset.get("height"):
        parts.append(f"{asset['width']}x{asset['height']}")
    tags = _as_string_or_empty(asset.get("tags")).strip()
    if tags:
        parts.append(f"标签：{tags}")
    return "，".join(parts) if parts else "用户提供的图片素材"


def _infer_platform_from_text(text: str, fallback: str = "generic") -> str:
    return fast_infer_platform(text) or fallback


def _infer_style_from_text(text: str, fallback: str = "commercial") -> str:
    return fast_infer_style(text) or fallback


def _infer_duration_from_text(text: str, fallback: int = 30) -> int:
    return fast_infer_duration(text) or fallback


def _build_brief_segments(brief: str, count: int) -> list[str]:
    if count <= 0:
        return []
    if _looks_like_meta_instruction(brief):
        return [f"素材 {idx + 1} 的核心视觉亮点" for idx in range(count)]
    clean = _as_string_or_empty(brief).strip()
    if not clean:
        return [f"镜头 {idx + 1}" for idx in range(count)]
    return [clean for _ in range(count)]
