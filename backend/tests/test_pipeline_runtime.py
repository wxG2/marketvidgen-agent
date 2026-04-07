from __future__ import annotations

import asyncio
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.auth import get_current_user
from app.agents.pipeline import PipelineExecutor
from app.agents.swarm_pipeline import SwarmPipelineExecutor
from app.database import Base, get_db
from app.models import *  # noqa: F401,F403
from app.models.pipeline import AgentExecution, PipelineRun
from app.models.user import User
from app.routers.pipeline import get_pipeline_router
from app.services.usage_service import UsageRecorder
import app.routers.pipeline as pipeline_router_module


class CountingAgent(BaseAgent):
    def __init__(self, name: str, behavior):
        self.name = name
        self.behavior = behavior
        self.calls = 0

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        self.calls += 1
        return await self.behavior(context, input_data, self.calls)


def make_executor(
    session_factory: async_sessionmaker,
    delays: dict[str, float] | None = None,
    audio_failures: int = 0,
    executor_cls=PipelineExecutor,
):
    delays = delays or {}
    remaining_audio_failures = {"count": audio_failures}

    async def orchestrator_behavior(_context, input_data, _call):
        await asyncio.sleep(delays.get("orchestrator", 0))
        return AgentResult(
            success=True,
            output_data={
                "shots": [
                    {
                        "shot_idx": 0,
                        "image_path": "/tmp/test-image.jpg",
                        "script_segment": input_data["script"],
                        "duration_seconds": 5,
                    }
                ],
                "video_type": "commercial",
                "voice_speed": 1.0,
                "style": input_data.get("style", "commercial"),
                "platform": input_data.get("platform", "generic"),
            },
        )

    async def prompt_behavior(_context, input_data, _call):
        await asyncio.sleep(delays.get("prompt_engineer", 0))
        shot = input_data["shots"][0]
        return AgentResult(
            success=True,
            output_data={
                "shot_prompts": [
                    {
                        "shot_idx": shot["shot_idx"],
                        "image_path": shot["image_path"],
                        "video_prompt": "camera push in",
                        "duration_seconds": shot["duration_seconds"],
                        "script_segment": shot["script_segment"],
                    }
                ],
                "voice_params": {"voice_id": "test", "speed": 1.0, "tone": "neutral"},
            },
        )

    async def audio_behavior(_context, _input_data, _call):
        await asyncio.sleep(delays.get("audio_subtitle", 0))
        if remaining_audio_failures["count"] > 0:
            remaining_audio_failures["count"] -= 1
            return AgentResult(success=False, output_data={}, error="audio failed")
        return AgentResult(
            success=True,
            output_data={
                "audio_path": "/tmp/audio.mp3",
                "subtitle_path": "/tmp/subtitle.srt",
                "duration_ms": 5000,
            },
        )

    async def video_behavior(_context, _input_data, call):
        await asyncio.sleep(delays.get("video_generator", 0))
        return AgentResult(
            success=True,
            output_data={
                "video_clips": [
                    {
                        "shot_idx": 0,
                        "video_path": f"/tmp/video-{call}.mp4",
                        "duration_seconds": 5,
                        "task_id": f"task-{call}",
                    }
                ]
            },
        )

    async def editor_behavior(_context, _input_data, _call):
        await asyncio.sleep(delays.get("video_editor", 0))
        return AgentResult(
            success=True,
            output_data={"final_video_path": "/tmp/final.mp4", "duration_ms": 5000},
        )

    return executor_cls(
        orchestrator=CountingAgent("orchestrator", orchestrator_behavior),
        prompt_engineer=CountingAgent("prompt_engineer", prompt_behavior),
        audio_agent=CountingAgent("audio_subtitle", audio_behavior),
        video_gen_agent=CountingAgent("video_generator", video_behavior),
        video_editor=CountingAgent("video_editor", editor_behavior),
        db_session_factory=session_factory,
    )


@pytest_asyncio.fixture()
async def pipeline_test_app(tmp_path: Path):
    db_path = tmp_path / "pipeline-runtime.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    def build_app(executor: PipelineExecutor) -> FastAPI:
        app = FastAPI()

        async def override_get_db():
            async with session_factory() as session:
                yield session

        async def override_get_current_user():
            return User(id="test-user", username="tester", password_hash="x", role="admin", is_active=True)

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_current_user
        pipeline_router_module.async_session = session_factory
        app.include_router(get_pipeline_router(executor))
        return app

    try:
        yield build_app, session_factory
    finally:
        await engine.dispose()


async def _wait_for_status(session_factory, run_id: str, expected: str, timeout: float = 3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            if run and run.status == expected:
                return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach status '{expected}' within {timeout}s")


@pytest_asyncio.fixture()
async def pipeline_client(pipeline_test_app):
    clients = []

    async def _make_client(executor: PipelineExecutor):
        app_factory, _session_factory = pipeline_test_app
        app = app_factory(executor)
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        clients.append(client)
        return client

    try:
        yield _make_client
    finally:
        for client in clients:
            await client.aclose()


async def _create_project(session_factory) -> str:
    from app.models.project import Project

    async with session_factory() as session:
        project = Project(name="Test Project", user_id="test-user")
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project.id


@pytest_asyncio.fixture()
async def project_id(pipeline_test_app):
    _app_factory, session_factory = pipeline_test_app
    return await _create_project(session_factory)


async def _launch_pipeline(client: AsyncClient, project_id: str):
    response = await client.post(
        f"/api/projects/{project_id}/pipeline",
        json={
            "script": "test script",
            "image_ids": ["img-1"],
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture()
async def session_factory(pipeline_test_app):
    _app_factory, session_factory = pipeline_test_app
    return session_factory


async def test_launch_pipeline_is_deduplicated(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory, delays={"orchestrator": 0.2})
    client = await pipeline_client(executor)

    first, second = await asyncio.gather(
        _launch_pipeline(client, project_id),
        _launch_pipeline(client, project_id),
    )

    assert first["id"] == second["id"]

    async with session_factory() as session:
        result = await session.execute(select(PipelineRun).where(PipelineRun.project_id == project_id))
        runs = result.scalars().all()
        assert len(runs) == 1


async def test_cancelled_pipeline_stays_cancelled(pipeline_client, project_id, session_factory):
    executor = make_executor(
        session_factory,
        delays={"audio_subtitle": 0.3, "video_generator": 0.3},
    )
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    await _wait_for_status(session_factory, run["id"], "running")

    cancel_response = await client.post(f"/api/projects/{project_id}/pipeline/{run['id']}/cancel")
    assert cancel_response.status_code == 200

    await asyncio.sleep(0.5)

    async with session_factory() as session:
        refreshed = await session.get(PipelineRun, run["id"])
        assert refreshed.status == "cancelled"
        assert refreshed.final_video_path is None


async def test_retry_failed_audio_does_not_rerun_video(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory, audio_failures=1, delays={"audio_subtitle": 0.05, "video_generator": 0.05})
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    await _wait_for_status(session_factory, run["id"], "failed")

    retry_response = await client.post(f"/api/projects/{project_id}/pipeline/{run['id']}/retry-agent")
    assert retry_response.status_code == 200

    await _wait_for_status(session_factory, run["id"], "completed")

    async with session_factory() as session:
        refreshed = await session.get(PipelineRun, run["id"])
        assert refreshed.retry_count == 1

        result = await session.execute(
            select(AgentExecution).where(AgentExecution.pipeline_run_id == run["id"])
        )
        executions = result.scalars().all()

    audio_attempts = sorted(e.attempt_number for e in executions if e.agent_name == "audio_subtitle")
    video_attempts = sorted(e.attempt_number for e in executions if e.agent_name == "video_generator")
    editor_attempts = sorted(e.attempt_number for e in executions if e.agent_name == "video_editor")

    assert audio_attempts == [1, 2]
    assert video_attempts == [1]
    assert editor_attempts == [1]


async def test_swarm_executor_records_lead_decisions_and_completes(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory, executor_cls=SwarmPipelineExecutor)
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    assert run["engine"] == "swarm"
    await _wait_for_status(session_factory, run["id"], "completed")

    run_response = await client.get(f"/api/projects/{project_id}/pipeline/{run['id']}")
    assert run_response.status_code == 200
    refreshed_run = run_response.json()
    assert refreshed_run["swarm_state"] is not None
    assert "task_board" in refreshed_run["swarm_state"]

    async with session_factory() as session:
        result = await session.execute(
            select(AgentExecution).where(AgentExecution.pipeline_run_id == run["id"])
        )
        executions = result.scalars().all()

    agent_names = [execution.agent_name for execution in executions]
    assert "swarm_lead" in agent_names
    assert "orchestrator" in agent_names
    assert "prompt_engineer" in agent_names
    assert "audio_subtitle" in agent_names
    assert "video_generator" in agent_names
    assert "video_editor" in agent_names

    lead_execs = [execution for execution in executions if execution.agent_name == "swarm_lead"]
    assert len(lead_execs) >= 3


async def test_swarm_accepts_human_message_while_running(pipeline_client, project_id, session_factory):
    executor = make_executor(
        session_factory,
        delays={"audio_subtitle": 0.3, "video_generator": 0.3},
        executor_cls=SwarmPipelineExecutor,
    )
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    await _wait_for_status(session_factory, run["id"], "running")

    message_response = await client.post(
        f"/api/projects/{project_id}/pipeline/{run['id']}/message",
        json={"message": "请使用更柔和的转场，并保持舒缓的背景音乐"},
    )
    assert message_response.status_code == 200
    assert message_response.json()["status"] == "queued"

    await _wait_for_status(session_factory, run["id"], "completed")


async def test_pipeline_delivery_preview_and_save(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory)
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    completed = await _wait_for_status(session_factory, run["id"], "completed")
    Path(completed.final_video_path).write_bytes(b"fake-video")

    preview_response = await client.get(f"/api/projects/{project_id}/pipeline/{run['id']}/delivery")
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert {item["platform"] for item in preview_payload["previews"]} == {"douyin", "youtube"}

    save_response = await client.post(f"/api/projects/{project_id}/pipeline/{run['id']}/delivery/save", json={})
    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["platform"] == "repository"
    assert saved["status"] == "saved"
    assert saved["saved_video_path"]
    assert Path(saved["saved_video_path"]).exists()


async def test_pipeline_delivery_publish_requires_connected_douyin_account(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory)
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    completed = await _wait_for_status(session_factory, run["id"], "completed")
    Path(completed.final_video_path).write_bytes(b"fake-video")

    response = await client.post(f"/api/projects/{project_id}/pipeline/{run['id']}/delivery/publish-douyin", json={})
    assert response.status_code == 400
    assert "抖音账号" in response.json()["detail"]
