from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class LipSyncTask:
    task_id: str
    status: str = "pending"


@dataclass
class LipSyncStatus:
    task_id: str
    status: str  # pending | processing | completed | failed
    progress: float = 0.0
    video_url: Optional[str] = None
    error: Optional[str] = None


class LipSyncGenerator(ABC):
    @abstractmethod
    async def generate(self, image_path: str, audio_path: str, prompt: str) -> LipSyncTask:
        ...

    @abstractmethod
    async def poll_status(self, task_id: str) -> LipSyncStatus:
        ...


class MockLipSyncGenerator(LipSyncGenerator):
    """Mock LipSync generator that simulates generation with a delay."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    async def generate(self, image_path: str, audio_path: str, prompt: str) -> LipSyncTask:
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "status": "processing",
            "started": asyncio.get_event_loop().time(),
            "image_path": image_path,
        }
        return LipSyncTask(task_id=task_id, status="processing")

    async def poll_status(self, task_id: str) -> LipSyncStatus:
        task = self._tasks.get(task_id)
        if not task:
            return LipSyncStatus(task_id=task_id, status="failed", error="Task not found")

        elapsed = asyncio.get_event_loop().time() - task["started"]
        if elapsed < 5:
            progress = min(elapsed / 5 * 100, 99)
            return LipSyncStatus(task_id=task_id, status="processing", progress=progress)

        return LipSyncStatus(task_id=task_id, status="completed", progress=100)


class LTX23LipSyncGenerator(LipSyncGenerator):
    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_url = api_url

    async def generate(self, image_path: str, audio_path: str, prompt: str) -> LipSyncTask:
        raise NotImplementedError("LTX 2.3 LipSync integration pending")

    async def poll_status(self, task_id: str) -> LipSyncStatus:
        raise NotImplementedError("LTX 2.3 LipSync integration pending")
