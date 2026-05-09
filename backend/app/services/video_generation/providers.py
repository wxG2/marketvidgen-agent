from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import uuid

import httpx

from app.core.config import settings
from app.services.video_generation.base import (
    GenerationStatus,
    GenerationTask,
    VideoGenerator,
    _request_with_retries,
    _validate_reference_image,
)

logger = logging.getLogger(__name__)


class MockVideoGenerator(VideoGenerator):
    """Mock generator that simulates video generation with a delay."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    async def generate(
        self,
        image_path: str,
        prompt: str,
        duration: int = 5,
        no_audio: bool = True,
        generation_model: str | None = None,
        platform: str = "generic",
    ) -> GenerationTask:
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
        self._poll_urls: dict[str, str] = {}

    async def _upload_image(self, file_path: str) -> str:
        """Upload a local image to WaveSpeed media API and return the public URL."""
        path = _validate_reference_image(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file_bytes = path.read_bytes()

        async with httpx.AsyncClient(timeout=120) as client:
            response = await _request_with_retries(
                client,
                "POST",
                self.MEDIA_UPLOAD_URL,
                operation="WaveSpeed media upload",
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

    async def generate(
        self,
        image_path: str,
        prompt: str,
        duration: int = 5,
        no_audio: bool = True,
        generation_model: str | None = None,
        platform: str = "generic",
    ) -> GenerationTask:
        image_url = await self._upload_image(image_path)
        payload = {
            "prompt": prompt,
            "image": image_url,
            "duration": duration,
            "cfg_scale": 0.5,
            "sound": not no_audio,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await _request_with_retries(
                client,
                "POST",
                f"{self.api_url}/{self.model}",
                operation="WaveSpeed task create",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code != 200:
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

        poll_url = (inner.get("urls") or {}).get("get")
        if poll_url:
            self._poll_urls[task_id] = poll_url

        return GenerationTask(task_id=task_id, status="processing")

    async def poll_status(self, task_id: str) -> GenerationStatus:
        poll_url = self._poll_urls.get(task_id, f"{self.api_url}/predictions/{task_id}")

        async with httpx.AsyncClient(timeout=120) as client:
            response = await _request_with_retries(
                client,
                "GET",
                poll_url,
                operation="WaveSpeed task poll",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            data = response.json().get("data", {})

        raw_status = data.get("status", "processing")
        outputs = data.get("outputs") or data.get("output", {}).get("video_url") and [data["output"]["video_url"]] or []
        error = data.get("error") or data.get("message")

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
    """Volcengine Ark doubao-seedance image-to-video generator."""

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
        path = _validate_reference_image(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _is_seedance_2(self) -> bool:
        return "seedance-2-0" in self.model or "seedance2" in self.model.replace("-", "")

    @staticmethod
    def _ratio_for_platform(platform: str) -> str:
        ratios = {
            "douyin": "9:16",
            "xiaohongshu": "3:4",
            "bilibili": "16:9",
            "generic": "16:9",
        }
        return ratios.get(platform or "generic", "16:9")

    def _build_seedance_15_payload(self, *, image_data_url: str, prompt: str, duration: int, no_audio: bool) -> dict:
        supported = settings.SEEDANCE_SUPPORTED_DURATIONS
        actual_duration = min(supported, key=lambda s: abs(s - max(duration, min(supported))))
        noaudio_flag = "true" if no_audio else "false"
        text_with_flags = f"{prompt}  --duration {actual_duration} --noaudio {noaudio_flag} --camerafixed false --watermark false"
        return {
            "model": self.model,
            "content": [
                {"type": "text", "text": text_with_flags},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
            "generate_audio": not no_audio,
        }

    def _build_seedance_20_payload(
        self,
        *,
        image_data_url: str,
        prompt: str,
        duration: int,
        no_audio: bool,
        platform: str,
    ) -> dict:
        return {
            "model": self.model,
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}, "role": "reference_image"},
            ],
            "generate_audio": not no_audio,
            "ratio": self._ratio_for_platform(platform),
            "duration": max(int(duration or 1), 1),
            "watermark": False,
        }

    def _build_payload(self, *, image_data_url: str, prompt: str, duration: int, no_audio: bool, platform: str) -> dict:
        if self._is_seedance_2():
            return self._build_seedance_20_payload(
                image_data_url=image_data_url,
                prompt=prompt,
                duration=duration,
                no_audio=no_audio,
                platform=platform,
            )
        return self._build_seedance_15_payload(
            image_data_url=image_data_url,
            prompt=prompt,
            duration=duration,
            no_audio=no_audio,
        )

    async def generate(
        self,
        image_path: str,
        prompt: str,
        duration: int = 5,
        no_audio: bool = True,
        generation_model: str | None = None,
        platform: str = "generic",
    ) -> GenerationTask:
        image_data_url = self._file_to_data_url(image_path)
        payload = self._build_payload(
            image_data_url=image_data_url,
            prompt=prompt,
            duration=duration,
            no_audio=no_audio,
            platform=platform,
        )

        async with httpx.AsyncClient(timeout=180) as client:
            response = await _request_with_retries(
                client,
                "POST",
                f"{self.base_url}/contents/generations/tasks",
                operation="Seedance task create",
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
            response = await _request_with_retries(
                client,
                "GET",
                f"{self.base_url}/contents/generations/tasks/{task_id}",
                operation="Seedance task poll",
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

        content = data.get("content") or {}
        video_url = None
        if isinstance(content, dict):
            video_url = content.get("video_url")
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "video_url":
                    video_url = item.get("video_url", {}).get("url")
                    break

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
