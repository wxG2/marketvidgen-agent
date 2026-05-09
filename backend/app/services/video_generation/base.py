from __future__ import annotations

import asyncio
import logging
import mimetypes
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TRANSIENT_HTTP_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class GenerationTask:
    task_id: str
    status: str = "pending"


@dataclass
class GenerationStatus:
    task_id: str
    status: str
    progress: float = 0.0
    video_url: Optional[str] = None
    error: Optional[str] = None


class VideoGenerator(ABC):
    @abstractmethod
    async def generate(
        self,
        image_path: str,
        prompt: str,
        duration: int = 5,
        no_audio: bool = True,
        generation_model: str | None = None,
        platform: str = "generic",
    ) -> GenerationTask:
        ...

    @abstractmethod
    async def poll_status(self, task_id: str) -> GenerationStatus:
        ...


def _validate_reference_image(file_path: str) -> Path:
    path = Path(file_path)
    if not path.exists():
        raise RuntimeError(f"Reference frame not found: {file_path}")

    mime = mimetypes.guess_type(path.name)[0] or ""
    if not mime.startswith("image/"):
        raise RuntimeError(
            f"Reference frame must be an image file, got {path.suffix or 'unknown type'}: {file_path}"
        )
    return path


def _describe_httpx_error(exc: httpx.HTTPError) -> str:
    message = str(exc).strip()
    if message:
        return message
    args = ", ".join(repr(arg) for arg in exc.args if arg not in ("", None))
    if args:
        return f"{exc.__class__.__name__}: {args}"
    return exc.__class__.__name__


async def _request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    operation: str,
    **kwargs,
) -> httpx.Response:
    attempts = max(int(settings.VIDEO_GENERATION_HTTP_RETRIES), 0) + 1
    backoff = max(float(settings.VIDEO_GENERATION_HTTP_RETRY_BACKOFF_SECONDS), 0.1)
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code in TRANSIENT_HTTP_STATUS_CODES and attempt < attempts:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                logger.warning(
                    "%s transient response on attempt %s/%s: %s",
                    operation,
                    attempt,
                    attempts,
                    last_error,
                )
                await asyncio.sleep(backoff * attempt)
                continue
            return response
        except httpx.TransportError as exc:
            last_error = _describe_httpx_error(exc)
            if attempt >= attempts:
                raise RuntimeError(f"{operation} failed after {attempts} attempts: {last_error}") from exc
            logger.warning(
                "%s transport failure on attempt %s/%s: %s",
                operation,
                attempt,
                attempts,
                last_error,
            )
            await asyncio.sleep(backoff * attempt)

    raise RuntimeError(f"{operation} failed after {attempts} attempts: {last_error or 'unknown error'}")
