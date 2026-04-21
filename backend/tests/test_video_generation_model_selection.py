from __future__ import annotations

import pytest

from app.services.video_generator import (
    GenerationStatus,
    GenerationTask,
    SeedanceGenerator,
    VideoGenerator,
    VideoGeneratorRouter,
)


class RecordingGenerator(VideoGenerator):
    def __init__(self, name: str):
        self.name = name
        self.calls: list[dict] = []

    async def generate(
        self,
        image_path: str,
        prompt: str,
        duration: int = 5,
        no_audio: bool = True,
        generation_model: str | None = None,
        platform: str = "generic",
    ) -> GenerationTask:
        self.calls.append(
            {
                "image_path": image_path,
                "prompt": prompt,
                "duration": duration,
                "no_audio": no_audio,
                "generation_model": generation_model,
                "platform": platform,
            }
        )
        return GenerationTask(task_id=f"{self.name}-task", status="processing")

    async def poll_status(self, task_id: str) -> GenerationStatus:
        return GenerationStatus(
            task_id=task_id,
            status="completed",
            progress=100,
            video_url=f"https://example.test/{task_id}.mp4",
        )


@pytest.mark.asyncio
async def test_video_generator_router_routes_by_generation_model_alias():
    seedance_15 = RecordingGenerator("seedance15")
    seedance_20 = RecordingGenerator("seedance20")
    kling = RecordingGenerator("kling")
    router = VideoGeneratorRouter(
        default_model="seedance1.5-pro",
        seedance_15=seedance_15,
        seedance_20=seedance_20,
        kling=kling,
        seedance_15_model_name="doubao-seedance-1-5-pro-251215",
        seedance_20_model_name="doubao-seedance-2-0-260128",
        kling_model_name="kling-v3",
    )

    task = await router.generate(
        image_path="/tmp/frame.jpg",
        prompt="make a bright product video",
        duration=11,
        no_audio=False,
        generation_model="doubao-seedance-2-0-260128",
        platform="douyin",
    )
    status = await router.poll_status(task.task_id)

    assert seedance_20.calls == [
        {
            "image_path": "/tmp/frame.jpg",
            "prompt": "make a bright product video",
            "duration": 11,
            "no_audio": False,
            "generation_model": "seedance2.0",
            "platform": "douyin",
        }
    ]
    assert seedance_15.calls == []
    assert kling.calls == []
    assert status.status == "completed"
    assert status.video_url == "https://example.test/seedance20-task.mp4"


@pytest.mark.asyncio
async def test_video_generator_router_uses_default_model():
    seedance_15 = RecordingGenerator("seedance15")
    router = VideoGeneratorRouter(default_model="seedance1.5-pro", seedance_15=seedance_15)

    await router.generate(image_path="/tmp/frame.jpg", prompt="hello")

    assert seedance_15.calls[0]["generation_model"] == "seedance1.5-pro"


@pytest.mark.asyncio
async def test_video_generator_router_rejects_unconfigured_model():
    router = VideoGeneratorRouter(default_model="seedance1.5-pro")

    with pytest.raises(RuntimeError, match="not configured"):
        await router.generate(
            image_path="/tmp/frame.jpg",
            prompt="hello",
            generation_model="seedance2.0",
        )


def test_seedance_20_payload_uses_contents_generation_fields():
    generator = SeedanceGenerator(
        api_key="test",
        model="doubao-seedance-2-0-260128",
    )

    payload = generator._build_payload(
        image_data_url="data:image/jpeg;base64,abc",
        prompt="first-person product video",
        duration=11,
        no_audio=False,
        platform="douyin",
    )

    assert payload["model"] == "doubao-seedance-2-0-260128"
    assert payload["generate_audio"] is True
    assert payload["ratio"] == "9:16"
    assert payload["duration"] == 11
    assert payload["watermark"] is False
    assert payload["content"][0] == {"type": "text", "text": "first-person product video"}
    assert payload["content"][1]["role"] == "reference_image"
    assert payload["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,abc"


def test_seedance_20_payload_defaults_to_silent_generation():
    generator = SeedanceGenerator(
        api_key="test",
        model="doubao-seedance-2-0-260128",
    )

    payload = generator._build_payload(
        image_data_url="data:image/jpeg;base64,abc",
        prompt="silent product video",
        duration=5,
        no_audio=True,
        platform="generic",
    )

    assert payload["generate_audio"] is False
    assert payload["ratio"] == "16:9"
