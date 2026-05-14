from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.core.security import get_current_user
from app.agents.pipeline import PipelineExecutor
from app.db.session import Base, get_db
from app.models import *  # noqa: F401,F403
from app.models.auto_chat import AutoChatSession, AutoSessionMaterialSelection
from app.models.material import Material
from app.models.pipeline import AgentExecution, PipelineRun
from app.models.user import User
from app.models.video_upload import VideoUpload
from app.routers.pipeline import get_pipeline_router
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
            "review_prompts": False,
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

    final_video_response = await client.get(f"/api/projects/{project_id}/pipeline/{run['id']}/final-video")
    assert final_video_response.status_code == 200
    assert final_video_response.content == b"fake-video"

    save_response = await client.post(f"/api/projects/{project_id}/pipeline/{run['id']}/delivery/save", json={})
    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["platform"] == "repository"
    assert saved["status"] == "saved"
    assert saved["saved_video_path"]
    assert Path(saved["saved_video_path"]).exists()


async def test_pipeline_persists_intermediate_agent_artifacts(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory)
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    await _wait_for_status(session_factory, run["id"], "completed")

    response = await client.get(f"/api/projects/{project_id}/pipeline/{run['id']}/artifacts")
    assert response.status_code == 200
    artifacts = response.json()
    asset_keys = {item["asset_key"] for item in artifacts}

    assert {
        "prompt_engineer.plan",
        "prompt_engineer.shot.0",
        "prompt_engineer.voice_params",
        "audio_subtitle.audio",
        "audio_subtitle.subtitle",
        "video_generator.manifest",
        "video_generator.shot.0",
    }.issubset(asset_keys)
    prompt_asset = next(item for item in artifacts if item["asset_key"] == "prompt_engineer.shot.0")
    assert prompt_asset["source_agent"] == "prompt_engineer"
    assert prompt_asset["text_content"] == "camera push in"


async def test_standard_pipeline_does_not_create_requirement_parser_execution(
    pipeline_client, project_id, session_factory
):
    executor = make_executor(session_factory)
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    await _wait_for_status(session_factory, run["id"], "completed")

    async with session_factory() as session:
        result = await session.execute(
            select(AgentExecution.agent_name).where(AgentExecution.pipeline_run_id == run["id"])
        )
        agent_names = result.scalars().all()

    assert "requirement_parser" not in agent_names
    assert "orchestrator" in agent_names


async def test_remix_pipeline_waits_for_confirmation_then_assembles(pipeline_client, project_id, session_factory):
    async def remix_planner_behavior(_context, _input_data, _call):
        return AgentResult(
            success=True,
            output_data={
                "requires_confirmation": True,
                "remix_plan": {
                    "title": "test remix",
                    "segments": [
                        {
                            "segment_idx": 0,
                            "source_video_id": "video-a",
                            "start_seconds": 0,
                            "end_seconds": 2,
                            "transition_to_next": "cut",
                        }
                    ],
                    "audio_design": {"strategy": "silent", "bgm_mood": "none", "bgm_volume": 0.0},
                },
            },
        )

    async def remix_assembler_behavior(_context, input_data, _call):
        assert input_data["remix_plan"]["title"] == "test remix"
        return AgentResult(success=True, output_data={"final_video_path": "/tmp/remix.mp4", "duration_ms": 2000})

    executor = make_executor(session_factory)
    executor.remix_planner = CountingAgent("remix_planner", remix_planner_behavior)
    executor.remix_assembler = CountingAgent("remix_assembler", remix_assembler_behavior)
    client = await pipeline_client(executor)
    async with session_factory() as session:
        session.add_all([
            VideoUpload(
                id="video-a",
                project_id=project_id,
                filename="video-a.mp4",
                file_path="/tmp/video-a.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
            VideoUpload(
                id="video-b",
                project_id=project_id,
                filename="video-b.mp4",
                file_path="/tmp/video-b.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
        ])
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/pipeline",
        json={
            "script": "remix these clips",
            "image_ids": [],
            "reference_video_ids": ["video-a", "video-b"],
            "review_prompts": False,
        },
    )
    assert response.status_code == 200
    run = response.json()
    await _wait_for_status(session_factory, run["id"], "waiting_remix_confirmation")

    confirm = await client.post(
        f"/api/projects/{project_id}/pipeline/{run['id']}/confirm-remix",
        json={"approved": True, "edited_segments": []},
    )
    assert confirm.status_code == 200
    completed = await _wait_for_status(session_factory, run["id"], "completed")

    assert completed.final_video_path == "/tmp/remix.mp4"
    assert executor.remix_planner.calls == 1
    assert executor.remix_assembler.calls == 1
    assert executor.video_gen_agent.calls == 0


async def test_remix_voiceover_uses_audio_subtitle_agent(pipeline_client, project_id, session_factory):
    async def remix_planner_behavior(_context, _input_data, _call):
        return AgentResult(
            success=True,
            output_data={
                "requires_confirmation": True,
                "remix_plan": {
                    "title": "test remix",
                    "segments": [
                        {
                            "segment_idx": 0,
                            "source_video_id": "video-a",
                            "start_seconds": 0,
                            "end_seconds": 2,
                            "description": "第一段展示产品亮点。",
                            "voiceover": "第一段展示产品亮点。",
                            "transition_to_next": "cut",
                        }
                    ],
                    "audio_design": {"strategy": "silent", "voice_id": "warm", "voice_speed": 1.2},
                },
            },
        )

    async def audio_behavior(_context, input_data, _call):
        assert input_data["script"] == "第一段展示产品亮点。"
        assert input_data["voice_params"]["voice_id"] == "warm"
        assert input_data["voice_params"]["speed"] == 1.2
        return AgentResult(
            success=True,
            output_data={
                "audio_path": "/tmp/remix-voice.mp3",
                "subtitle_path": "/tmp/remix-subtitle.srt",
                "duration_ms": 2000,
            },
        )

    async def remix_assembler_behavior(_context, input_data, _call):
        assert input_data["audio"]["audio_path"] == "/tmp/remix-voice.mp3"
        assert input_data["audio"]["subtitle_path"] == "/tmp/remix-subtitle.srt"
        return AgentResult(success=True, output_data={"final_video_path": "/tmp/remix.mp4", "duration_ms": 2000})

    executor = make_executor(session_factory)
    executor.remix_planner = CountingAgent("remix_planner", remix_planner_behavior)
    executor.audio_agent = CountingAgent("audio_subtitle", audio_behavior)
    executor.remix_assembler = CountingAgent("remix_assembler", remix_assembler_behavior)
    client = await pipeline_client(executor)
    async with session_factory() as session:
        session.add_all([
            VideoUpload(
                id="video-a",
                project_id=project_id,
                filename="video-a.mp4",
                file_path="/tmp/video-a.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
            VideoUpload(
                id="video-b",
                project_id=project_id,
                filename="video-b.mp4",
                file_path="/tmp/video-b.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
        ])
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/pipeline",
        json={
            "script": "remix these clips",
            "image_ids": [],
            "reference_video_ids": ["video-a", "video-b"],
            "remix_config": {"add_voiceover": True, "bgm_mood": "none"},
            "review_prompts": False,
        },
    )
    assert response.status_code == 200
    run = response.json()
    await _wait_for_status(session_factory, run["id"], "waiting_remix_confirmation")

    confirm = await client.post(
        f"/api/projects/{project_id}/pipeline/{run['id']}/confirm-remix",
        json={"approved": True, "edited_segments": []},
    )
    assert confirm.status_code == 200
    await _wait_for_status(session_factory, run["id"], "completed")

    assert executor.audio_agent.calls == 1
    assert executor.remix_assembler.calls == 1
    assert executor.video_gen_agent.calls == 0


async def test_remix_voiceover_replans_when_audio_exceeds_selected_segments(
    pipeline_client,
    project_id,
    session_factory,
):
    async def remix_planner_behavior(_context, input_data, call):
        if call == 2:
            # Video-priority: replan target = min(audio_duration, voiceover_cap)
            # voiceover_cap = min(4.0 * 1.2, 4.0 + 6.0) = 4.8
            assert input_data["remix_config"]["target_duration_seconds"] == 4.8
            assert "素材不足" in input_data["remix_adjustment_feedback"]
            return AgentResult(
                success=True,
                output_data={
                    "requires_confirmation": True,
                    "remix_plan": {
                        "title": "replanned remix",
                        "segments": [
                            {
                                "segment_idx": 0,
                                "source_video_id": "video-a",
                                "start_seconds": 0,
                                "end_seconds": 4,
                                "voiceover": "第一段。",
                                "transition_to_next": "cut",
                            },
                            {
                                "segment_idx": 1,
                                "source_video_id": "video-b",
                                "start_seconds": 0,
                                "end_seconds": 4,
                                "voiceover": "第二段。",
                                "transition_to_next": "cut",
                            },
                        ],
                        "audio_design": {"strategy": "silent", "bgm_mood": "none", "bgm_volume": 0.0},
                    },
                    "video_profiles": [
                        {"video_id": "video-a", "duration_seconds": 8.0},
                        {"video_id": "video-b", "duration_seconds": 8.0},
                    ],
                },
            )
        return AgentResult(
            success=True,
            output_data={
                "requires_confirmation": True,
                "remix_plan": {
                    "title": "initial remix",
                    "segments": [
                        {
                            "segment_idx": 0,
                            "source_video_id": "video-a",
                            "start_seconds": 0,
                            "end_seconds": 2,
                            "voiceover": "第一段。",
                            "transition_to_next": "cut",
                        },
                        {
                            "segment_idx": 1,
                            "source_video_id": "video-b",
                            "start_seconds": 0,
                            "end_seconds": 2,
                            "voiceover": "第二段。",
                            "transition_to_next": "cut",
                        },
                    ],
                    "audio_design": {"strategy": "silent", "bgm_mood": "none", "bgm_volume": 0.0},
                },
                "video_profiles": [
                    {"video_id": "video-a", "duration_seconds": 1.5},
                    {"video_id": "video-b", "duration_seconds": 1.5},
                ],
            },
        )

    async def audio_behavior(_context, input_data, _call):
        assert input_data["script"] == "第一段。\n第二段。"
        return AgentResult(
            success=True,
            output_data={
                "audio_path": "/tmp/remix-voice-long.mp3",
                "subtitle_path": "/tmp/remix-subtitle-long.srt",
                "duration_ms": 8000,
            },
        )

    async def remix_assembler_behavior(_context, _input_data, _call):
        raise AssertionError("assembler should wait for the replanned remix confirmation")

    executor = make_executor(session_factory)
    executor.remix_planner = CountingAgent("remix_planner", remix_planner_behavior)
    executor.audio_agent = CountingAgent("audio_subtitle", audio_behavior)
    executor.remix_assembler = CountingAgent("remix_assembler", remix_assembler_behavior)
    client = await pipeline_client(executor)
    async with session_factory() as session:
        session.add_all([
            VideoUpload(
                id="video-a",
                project_id=project_id,
                filename="video-a.mp4",
                file_path="/tmp/video-a.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
            VideoUpload(
                id="video-b",
                project_id=project_id,
                filename="video-b.mp4",
                file_path="/tmp/video-b.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
        ])
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/pipeline",
        json={
            "script": "remix these clips",
            "image_ids": [],
            "reference_video_ids": ["video-a", "video-b"],
            "remix_config": {"add_voiceover": True, "bgm_mood": "none"},
            "review_prompts": False,
        },
    )
    assert response.status_code == 200
    run = response.json()
    await _wait_for_status(session_factory, run["id"], "waiting_remix_confirmation")

    confirm = await client.post(
        f"/api/projects/{project_id}/pipeline/{run['id']}/confirm-remix",
        json={"approved": True, "edited_segments": []},
    )
    assert confirm.status_code == 200

    deadline = asyncio.get_event_loop().time() + 3.0
    replanned = None
    while asyncio.get_event_loop().time() < deadline:
        if executor.remix_planner.calls >= 2:
            async with session_factory() as session:
                replanned = await session.get(PipelineRun, run["id"])
                if replanned and replanned.status == "waiting_remix_confirmation":
                    break
        await asyncio.sleep(0.05)
    assert replanned is not None
    assert replanned.status == "waiting_remix_confirmation"
    assert executor.audio_agent.calls == 1
    assert executor.remix_assembler.calls == 0

    snapshot = json.loads(replanned.artifacts_snapshot)
    assert snapshot["remix_plan"]["title"] == "replanned remix"
    assert snapshot["audio"]["_reuse_for_audio_aligned_remix"] is True
    saved_input = json.loads(replanned.input_config)
    assert saved_input["remix_config"]["target_duration_seconds"] == 4.8


async def test_remix_launch_uses_first_session_audio_as_bgm(pipeline_client, project_id, session_factory):
    async def remix_planner_behavior(_context, input_data, _call):
        assert input_data["remix_config"]["bgm_material_id"] == "audio-1"
        return AgentResult(
            success=True,
            output_data={
                "requires_confirmation": True,
                "remix_plan": {
                    "title": "test remix",
                    "segments": [
                        {
                            "segment_idx": 0,
                            "source_video_id": "video-a",
                            "source_shot_idx": 0,
                            "start_seconds": 0,
                            "end_seconds": 2,
                            "transition_to_next": "cut",
                        }
                    ],
                    "audio_design": {"strategy": "bgm_only", "bgm_source": "uploaded", "bgm_material_id": "audio-1"},
                },
            },
        )

    executor = make_executor(session_factory)
    executor.remix_planner = CountingAgent("remix_planner", remix_planner_behavior)
    client = await pipeline_client(executor)
    async with session_factory() as session:
        session.add(AutoChatSession(id="session-1", project_id=project_id, user_id="test-user"))
        session.add_all([
            Material(
                id="audio-1",
                user_id="test-user",
                category="音乐",
                filename="bgm.mp3",
                file_path="test-user/音乐/bgm.mp3",
                media_type="audio",
            ),
            AutoSessionMaterialSelection(
                session_id="session-1",
                material_id="audio-1",
                sort_order=0,
            ),
            VideoUpload(
                id="video-a",
                project_id=project_id,
                filename="video-a.mp4",
                file_path="/tmp/video-a.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
            VideoUpload(
                id="video-b",
                project_id=project_id,
                filename="video-b.mp4",
                file_path="/tmp/video-b.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
        ])
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/pipeline",
        json={
            "script": "remix these clips",
            "session_id": "session-1",
            "image_ids": [],
            "reference_video_ids": ["video-a", "video-b"],
            "remix_config": {"bgm_mood": "cinematic"},
            "review_prompts": False,
        },
    )
    assert response.status_code == 200
    await _wait_for_status(session_factory, response.json()["id"], "waiting_remix_confirmation")


async def test_remix_launch_rejects_non_audio_bgm_material(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory)
    client = await pipeline_client(executor)
    async with session_factory() as session:
        session.add_all([
            Material(
                id="image-1",
                user_id="test-user",
                category="素材",
                filename="image.png",
                file_path="test-user/素材/image.png",
                media_type="image",
            ),
            VideoUpload(
                id="video-a",
                project_id=project_id,
                filename="video-a.mp4",
                file_path="/tmp/video-a.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
            VideoUpload(
                id="video-b",
                project_id=project_id,
                filename="video-b.mp4",
                file_path="/tmp/video-b.mp4",
                file_size=100,
                mime_type="video/mp4",
            ),
        ])
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/pipeline",
        json={
            "script": "remix these clips",
            "image_ids": [],
            "reference_video_ids": ["video-a", "video-b"],
            "remix_config": {"bgm_material_id": "image-1"},
            "review_prompts": False,
        },
    )
    assert response.status_code == 400
    assert "BGM material must be an audio file" in response.json()["detail"]


async def test_pipeline_rejects_reference_video_from_other_project(pipeline_client, project_id, session_factory):
    from app.models.project import Project

    executor = make_executor(session_factory)
    client = await pipeline_client(executor)
    async with session_factory() as session:
        other_project = Project(name="Other Project", user_id="test-user")
        session.add(other_project)
        await session.flush()
        session.add(
            VideoUpload(
                id="other-video",
                project_id=other_project.id,
                filename="other-video.mp4",
                file_path="/tmp/other-video.mp4",
                file_size=100,
                mime_type="video/mp4",
            )
        )
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/pipeline",
        json={
            "script": "use another project's video",
            "reference_video_ids": ["other-video"],
            "review_prompts": False,
        },
    )
    assert response.status_code == 400


async def test_pipeline_delivery_publish_requires_connected_douyin_account(pipeline_client, project_id, session_factory):
    executor = make_executor(session_factory)
    client = await pipeline_client(executor)

    run = await _launch_pipeline(client, project_id)
    completed = await _wait_for_status(session_factory, run["id"], "completed")
    Path(completed.final_video_path).write_bytes(b"fake-video")

    response = await client.post(f"/api/projects/{project_id}/pipeline/{run['id']}/delivery/publish-douyin", json={})
    assert response.status_code == 400
    assert "抖音账号" in response.json()["detail"]
