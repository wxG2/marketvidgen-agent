from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base, get_db
from app.models import *  # noqa: F401,F403
from app.models.api_key import ApiKey
from app.models.external_video_job import ExternalVideoJob
from app.models.material import Material
from app.models.pipeline import PipelineRun
from app.models.user import User
from app.routers.public_video_jobs import get_public_video_jobs_router
from app.services.api_keys import create_api_key
import app.routers.pipeline as pipeline_router_module
import app.routers.public_video_jobs as public_jobs_router_module


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
MP3_BYTES = b"ID3" + b"\x00" * 64


class WaitingPromptExecutor:
    engine_name = "test"

    def __init__(self, session_factory: async_sessionmaker, final_path: Path):
        self.session_factory = session_factory
        self.final_path = final_path

    async def run(self, run_id: str, _project_id: str, _input_config: dict, **_kwargs):
        async with self.session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            run.status = "waiting_prompt_review"
            run.current_agent = "prompt_engineer"
            run.artifacts_snapshot = json.dumps(
                {
                    "prompt_plan": {
                        "shot_prompts": [
                            {
                                "shot_idx": 0,
                                "image_path": "/tmp/private-image.png",
                                "script_segment": "old script",
                                "video_prompt": "old prompt",
                                "duration_seconds": 5,
                            }
                        ],
                        "voice_params": {"voice_id": "default"},
                    }
                }
            )
            await session.commit()

    async def resume_from_prompt_review(self, context, _input_config: dict):
        self.final_path.write_bytes(b"fake-mp4")
        return {"final_video_path": str(self.final_path)}


@pytest_asyncio.fixture()
async def public_api_app(tmp_path: Path):
    db_path = tmp_path / "public-api.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    pipeline_router_module.async_session = session_factory
    public_jobs_router_module.async_session = session_factory
    executor = WaitingPromptExecutor(session_factory, tmp_path / "final.mp4")
    app.include_router(get_public_video_jobs_router(executor))

    async with session_factory() as session:
        user = User(username="api-user", password_hash="x", role="user", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        key_record, raw_key = await create_api_key(session, user_id=user.id, name="test key")

    try:
        yield app, session_factory, raw_key, key_record.id, user.id
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
async def public_client(public_api_app):
    app, _session_factory, _raw_key, _key_id, _user_id = public_api_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


def _job_files():
    spec = {
        "prompt": "用这些素材生成一条抖音大健康营销视频",
        "platform": "douyin",
        "duration_seconds": 30,
        "client_reference_id": "customer-order-123",
    }
    return {
        "spec": (None, json.dumps(spec), "application/json"),
        "images": ("image.png", PNG_BYTES, "image/png"),
    }


async def _wait_for_status(session_factory, run_id: str, expected: str, timeout: float = 3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status == expected:
                return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach status {expected!r}")


@pytest.mark.asyncio
async def test_public_api_key_auth_errors(public_client: AsyncClient, public_api_app):
    _app, session_factory, raw_key, key_id, _user_id = public_api_app

    response = await public_client.get("/v1/video-jobs/missing")
    assert response.status_code == 401

    response = await public_client.get("/v1/video-jobs/missing", headers={"Authorization": "Bearer vg_invalid"})
    assert response.status_code == 401

    async with session_factory() as session:
        record = await session.get(ApiKey, key_id)
        record.status = "disabled"
        await session.commit()

    response = await public_client.get("/v1/video-jobs/missing", headers={"Authorization": f"Bearer {raw_key}"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_api_key_scope_is_enforced(public_client: AsyncClient, public_api_app):
    _app, session_factory, _raw_key, _key_id, user_id = public_api_app
    async with session_factory() as session:
        _record, read_only_key = await create_api_key(
            session,
            user_id=user_id,
            name="read only",
            scopes=["video_jobs:read"],
        )

    response = await public_client.post(
        "/v1/video-jobs",
        files=_job_files(),
        headers={"Authorization": f"Bearer {read_only_key}"},
    )
    assert response.status_code == 403
    assert "video_jobs:create" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_public_video_job_uploads_materials_and_requires_review(
    public_client: AsyncClient,
    public_api_app,
):
    _app, session_factory, raw_key, key_id, user_id = public_api_app
    response = await public_client.post(
        "/v1/video-jobs",
        files=_job_files(),
        headers={"Authorization": f"Bearer {raw_key}", "Idempotency-Key": "order-123"},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] in {"queued", "processing", "requires_review"}

    async with session_factory() as session:
        job = await session.get(ExternalVideoJob, payload["job_id"])
        run = await session.get(PipelineRun, job.pipeline_run_id)
        config = json.loads(run.input_config)
        assert job.api_key_id == key_id
        assert job.user_id == user_id
        assert config["review_prompts"] is True
        assert len(config["image_ids"]) == 1
        material = await session.get(Material, config["image_ids"][0])
        assert material.user_id == user_id
        key_record = await session.get(ApiKey, key_id)
        assert key_record.last_used_at is not None

    await _wait_for_status(session_factory, run.id, "waiting_prompt_review")
    status_response = await public_client.get(
        f"/v1/video-jobs/{payload['job_id']}",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    status_payload = status_response.json()
    assert status_payload["status"] == "requires_review"
    assert status_payload["review"]["type"] == "shot_plan"
    assert "image_path" not in json.dumps(status_payload["review"]["data"])

    retry_response = await public_client.post(
        "/v1/video-jobs",
        files=_job_files(),
        headers={"Authorization": f"Bearer {raw_key}", "Idempotency-Key": "order-123"},
    )
    assert retry_response.status_code == 202
    assert retry_response.json()["job_id"] == payload["job_id"]


@pytest.mark.asyncio
async def test_create_public_remix_job_uploads_reference_videos_and_bgm(
    public_client: AsyncClient,
    public_api_app,
):
    _app, session_factory, raw_key, _key_id, user_id = public_api_app
    spec = {
        "prompt": "把这些参考视频混剪成一条节奏感短片",
        "platform": "douyin",
        "duration_seconds": 18,
        "client_reference_id": "remix-order-123",
        "remix_config": {
            "target_duration_seconds": 18,
            "bgm_mood": "cinematic",
            "bgm_volume": 0.2,
            "include_source_audio": False,
            "add_voiceover": True,
        },
    }
    response = await public_client.post(
        "/v1/video-jobs",
        files=[
            ("spec", (None, json.dumps(spec), "application/json")),
            ("reference_videos", ("video-1.mp4", MP4_BYTES, "video/mp4")),
            ("reference_videos", ("video-2.mp4", MP4_BYTES, "video/mp4")),
            ("bgm", ("background.mp3", MP3_BYTES, "audio/mpeg")),
        ],
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert response.status_code == 202

    async with session_factory() as session:
        job = await session.get(ExternalVideoJob, response.json()["job_id"])
        run = await session.get(PipelineRun, job.pipeline_run_id)
        config = json.loads(run.input_config)

        assert config["image_ids"] == []
        assert config["reference_video_id"] == config["reference_video_ids"][0]
        assert len(config["reference_video_ids"]) == 2
        assert config["remix_config"]["target_duration_seconds"] == 18
        assert config["remix_config"]["bgm_mood"] == "cinematic"
        assert config["remix_config"]["bgm_volume"] == 0.2
        assert config["remix_config"]["include_source_audio"] is False
        assert config["remix_config"]["add_voiceover"] is True

        bgm_material = await session.get(Material, config["remix_config"]["bgm_material_id"])
        assert bgm_material.user_id == user_id
        assert bgm_material.media_type == "audio"


@pytest.mark.asyncio
async def test_public_video_job_review_resumes_and_result_downloads(
    public_client: AsyncClient,
    public_api_app,
):
    _app, session_factory, raw_key, _key_id, _user_id = public_api_app
    response = await public_client.post(
        "/v1/video-jobs",
        files=_job_files(),
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    job_id = response.json()["job_id"]

    async with session_factory() as session:
        job = await session.get(ExternalVideoJob, job_id)
        run_id = job.pipeline_run_id

    await _wait_for_status(session_factory, run_id, "waiting_prompt_review")

    not_ready = await public_client.get(f"/v1/video-jobs/{job_id}/result", headers={"Authorization": f"Bearer {raw_key}"})
    assert not_ready.status_code == 409

    review_response = await public_client.post(
        f"/v1/video-jobs/{job_id}/review",
        json={
            "approved": True,
            "edited_shots": [
                {
                    "shot_idx": 0,
                    "script_segment": "new script",
                    "video_prompt": "new prompt",
                    "duration_seconds": 6,
                }
            ],
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert review_response.status_code == 200

    completed = await _wait_for_status(session_factory, run_id, "completed")
    assert completed.final_video_path

    result_response = await public_client.get(f"/v1/video-jobs/{job_id}/result", headers={"Authorization": f"Bearer {raw_key}"})
    assert result_response.status_code == 200
    assert result_response.content == b"fake-mp4"
