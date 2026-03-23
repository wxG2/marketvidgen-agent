from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompositeTask:
    task_id: str
    status: str = "pending"


@dataclass
class CompositeStatus:
    task_id: str
    status: str  # pending | processing | completed | failed
    progress: float = 0.0
    result_image_url: Optional[str] = None
    error: Optional[str] = None


class ImageCompositor(ABC):
    @abstractmethod
    async def composite(self, model_image_path: str, bg_image_path: str, prompt: str) -> CompositeTask:
        ...

    @abstractmethod
    async def poll_status(self, task_id: str) -> CompositeStatus:
        ...


class MockImageCompositor(ImageCompositor):
    """Mock compositor that simulates image compositing with a delay."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    async def composite(self, model_image_path: str, bg_image_path: str, prompt: str) -> CompositeTask:
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "status": "processing",
            "started": asyncio.get_event_loop().time(),
            "model_image_path": model_image_path,
        }
        return CompositeTask(task_id=task_id, status="processing")

    async def poll_status(self, task_id: str) -> CompositeStatus:
        task = self._tasks.get(task_id)
        if not task:
            return CompositeStatus(task_id=task_id, status="failed", error="Task not found")

        elapsed = asyncio.get_event_loop().time() - task["started"]
        if elapsed < 3:
            progress = min(elapsed / 3 * 100, 99)
            return CompositeStatus(task_id=task_id, status="processing", progress=progress)

        # Mock: return the model image as the composite result
        return CompositeStatus(task_id=task_id, status="completed", progress=100)


class FluxInpaintCompositor(ImageCompositor):
    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_url = api_url

    async def composite(self, model_image_path: str, bg_image_path: str, prompt: str) -> CompositeTask:
        raise NotImplementedError("FLUX Inpaint integration pending")

    async def poll_status(self, task_id: str) -> CompositeStatus:
        raise NotImplementedError("FLUX Inpaint integration pending")
