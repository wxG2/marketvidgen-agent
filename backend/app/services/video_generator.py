from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GenerationTask:
    task_id: str
    status: str = "pending"


@dataclass
class GenerationStatus:
    task_id: str
    status: str  # pending | processing | completed | failed
    progress: float = 0.0
    video_url: Optional[str] = None
    error: Optional[str] = None


class VideoGenerator(ABC):
    @abstractmethod
    async def generate(self, image_path: str, prompt: str, duration: int = 5, no_audio: bool = True) -> GenerationTask:
        ...

    @abstractmethod
    async def poll_status(self, task_id: str) -> GenerationStatus:
        ...


class MockVideoGenerator(VideoGenerator):
    """Mock generator that simulates video generation with a delay."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    async def generate(self, image_path: str, prompt: str, duration: int = 5, no_audio: bool = True) -> GenerationTask:
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "status": "processing",
            "started": asyncio.get_event_loop().time(),
            "image_path": image_path,
        }
        return GenerationTask(task_id=task_id, status="processing")

    async def poll_status(self, task_id: str) -> GenerationStatus:
        task = self._tasks.get(task_id)
        if not task:
            return GenerationStatus(task_id=task_id, status="failed", error="Task not found")

        elapsed = asyncio.get_event_loop().time() - task["started"]
        if elapsed < 5:
            progress = min(elapsed / 5 * 100, 99)
            return GenerationStatus(task_id=task_id, status="processing", progress=progress)

        return GenerationStatus(task_id=task_id, status="completed", progress=100)


class Kling3Generator(VideoGenerator):
    MEDIA_UPLOAD_URL = "https://api.wavespeed.ai/api/v3/media/upload/binary"

    def __init__(self, api_key: str, api_url: str, model: str):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.model = model.strip("/")
        # Cache: task_id → polling URL (from the create response)
        self._poll_urls: dict[str, str] = {}

    async def _upload_image(self, file_path: str) -> str:
        """Upload a local image to WaveSpeed media API and return the public URL."""
        path = Path(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file_bytes = path.read_bytes()

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                self.MEDIA_UPLOAD_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (path.name, file_bytes, mime)},
            )
            if response.status_code != 200:
                try:
                    msg = response.json().get("message", response.text)
                except Exception:
                    msg = response.text
                raise RuntimeError(f"WaveSpeed media upload failed ({response.status_code}): {msg}")
            data = response.json()

        download_url = (data.get("data") or {}).get("download_url")
        if not download_url:
            raise RuntimeError(f"WaveSpeed media upload did not return download_url: {data}")
        return download_url

    async def generate(self, image_path: str, prompt: str, duration: int = 5, no_audio: bool = True) -> GenerationTask:
        # WaveSpeed Kling API requires a publicly accessible image URL
        image_url = await self._upload_image(image_path)

        payload = {
            "prompt": prompt,
            "image": image_url,
            "duration": duration,
            "cfg_scale": 0.5,
            "sound": not no_audio,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{self.api_url}/{self.model}",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code != 200:
                # Surface the API error message instead of a generic HTTP error
                try:
                    err_body = response.json()
                    msg = err_body.get("message") or err_body.get("error") or response.text
                except Exception:
                    msg = response.text
                raise RuntimeError(f"WaveSpeed API error ({response.status_code}): {msg}")
            data = response.json()

        inner = data.get("data", {})
        task_id = inner.get("id") or data.get("id")
        if not task_id:
            raise RuntimeError(f"WaveSpeed did not return task id: {data}")

        # Store the polling URL provided by the API (preferred over constructing one)
        poll_url = (inner.get("urls") or {}).get("get")
        if poll_url:
            self._poll_urls[task_id] = poll_url

        return GenerationTask(task_id=task_id, status="processing")

    async def poll_status(self, task_id: str) -> GenerationStatus:
        # Use the URL returned by the create endpoint; fall back to constructed URL
        poll_url = self._poll_urls.get(task_id, f"{self.api_url}/predictions/{task_id}")

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(
                poll_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            data = response.json().get("data", {})

        raw_status = data.get("status", "processing")
        outputs = data.get("outputs") or data.get("output", {}).get("video_url") and [data["output"]["video_url"]] or []
        error = data.get("error") or data.get("message")

        # Normalize status: WaveSpeed may return "succeeded" or "completed"
        if raw_status in ("completed", "succeeded"):
            status = "completed"
        elif raw_status in ("failed", "error", "canceled"):
            status = "failed"
        else:
            status = "processing"

        progress = 100.0 if status == "completed" else 50.0 if status == "processing" else 0.0
        return GenerationStatus(
            task_id=task_id,
            status=status,
            progress=progress,
            video_url=outputs[0] if outputs else None,
            error=error,
        )


class SeedanceGenerator(VideoGenerator):
    """Volcengine Ark doubao-seedance image-to-video generator.

    Matches the official SDK calling convention:
        client.content_generation.tasks.create(model=..., content=[...])
        client.content_generation.tasks.get(task_id=...)

    Duration and other flags are embedded in the text prompt:
        "prompt text  --duration 5 --camerafixed false --watermark false"
    """

    def __init__(
        self,
        api_key: str,
        model: str = "doubao-seedance-1-5-pro-251215",
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _file_to_data_url(file_path: str) -> str:
        path = Path(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    async def generate(self, image_path: str, prompt: str, duration: int = 5, no_audio: bool = True) -> GenerationTask:
        image_data_url = self._file_to_data_url(image_path)
        supported = settings.SEEDANCE_SUPPORTED_DURATIONS
        actual_duration = min(supported, key=lambda s: abs(s - max(duration, min(supported))))

        # Seedance embeds parameters as flags in the text prompt
        noaudio_flag = "true" if no_audio else "false"
        text_with_flags = f"{prompt}  --duration {actual_duration} --noaudio {noaudio_flag} --camerafixed false --watermark false"

        content = [
            {"type": "text", "text": text_with_flags},
            {
                "type": "image_url",
                "image_url": {"url": image_data_url},
            },
        ]
        payload = {
            "model": self.model,
            "content": content,
        }

        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{self.base_url}/contents/generations/tasks",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code != 200:
                try:
                    err = response.json()
                    err_obj = err.get("error", {})
                    msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                    msg = msg or err.get("message") or response.text
                except Exception:
                    msg = response.text
                raise RuntimeError(f"Seedance API error ({response.status_code}): {msg}")
            data = response.json()

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"Seedance did not return task id: {data}")

        logger.info(f"Seedance task created: {task_id}")
        return GenerationTask(task_id=task_id, status="processing")

    async def poll_status(self, task_id: str) -> GenerationStatus:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(
                f"{self.base_url}/contents/generations/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if response.status_code != 200:
                try:
                    msg = response.json().get("error", {}).get("message", response.text)
                except Exception:
                    msg = response.text
                return GenerationStatus(task_id=task_id, status="failed", error=f"Poll error ({response.status_code}): {msg}")
            data = response.json()

        raw_status = data.get("status", "processing")
        error_info = data.get("error") or {}
        error_msg = error_info.get("message") if isinstance(error_info, dict) else str(error_info) if error_info else None

        # Extract video URL — SDK returns content.video_url on success
        content = data.get("content") or {}
        video_url = None
        if isinstance(content, dict):
            video_url = content.get("video_url")
        elif isinstance(content, list):
            # Some responses return content as a list of items
            for item in content:
                if isinstance(item, dict) and item.get("type") == "video_url":
                    video_url = item.get("video_url", {}).get("url")
                    break

        # Normalize status
        if raw_status == "succeeded":
            status = "completed"
        elif raw_status in ("failed", "error", "expired"):
            status = "failed"
        else:
            status = "processing"

        progress = 100.0 if status == "completed" else 50.0 if status == "processing" else 0.0
        return GenerationStatus(
            task_id=task_id,
            status=status,
            progress=progress,
            video_url=video_url,
            error=error_msg,
        )
