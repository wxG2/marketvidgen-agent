from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import suppress
from datetime import datetime, timezone

from fastapi import FastAPI
from sqlalchemy import select

from app.agents import (
    AudioSubtitleAgent,
    ChatAgent,
    LangGraphPipelineExecutor,
    OrchestratorAgent,
    PipelineExecutor,
    PromptEngineerAgent,
    QAReviewerAgent,
    ReplicationPlannerAgent,
    ToolRegistry,
    VideoEditorAgent,
    VideoGeneratorAgent,
)
from app.agents.skills import register_runtime_skills
from app.core.config import settings
from app.db.session import async_session, init_db
from app.models.pipeline import AgentExecution, PipelineRun
from app.services.agent_memory import AgentMemoryService
from app.services.artifact_cleanup import cleanup_old_artifacts, periodic_artifact_cleanup
from app.services.image_compositor import FluxInpaintCompositor, MockImageCompositor
from app.services.keyframe_extractor import FFmpegKeyframeExtractor, MockKeyframeExtractor
from app.services.lipsync_generator import LTX23LipSyncGenerator, MockLipSyncGenerator
from app.services.llm_service import MockLLMService, RealLLMService
from app.services.mem0_service import Mem0Service
from app.services.rag_service import RagService
from app.services.tts_service import MockTTSService, RealTTSService
from app.services.video_analyzer import MockVideoAnalyzer, Qwen3VLAnalyzer
from app.services.video_editing.composer import MockVideoEditorService, RealVideoEditorService
from app.services.video_generator import (
    Kling3Generator,
    MockVideoGenerator,
    SeedanceGenerator,
    VideoGeneratorRouter,
)
# 构建各个agent的大模型服务，并传入各个agent
import app.models  # noqa: F401

logger = logging.getLogger(__name__)


def ensure_storage_directories() -> None:
    os.makedirs(settings.MATERIALS_ROOT, exist_ok=True)
    os.makedirs(settings.EXAMPLES_ROOT, exist_ok=True)
    os.makedirs(settings.GENERATED_DIR, exist_ok=True)
    os.makedirs(settings.VIDEO_REPOSITORY_DIR, exist_ok=True)


async def recover_interrupted_pipeline_runs() -> None:
    """Mark in-flight runs and their agent executions as failed after a server restart."""
    async with async_session() as session:
        result = await session.execute(
            select(PipelineRun).where(PipelineRun.status.in_(["pending", "running"]))
        )
        runs = result.scalars().all()
        if not runs:
            return

        run_ids = [run.id for run in runs]
        for run in runs:
            run.status = "failed"
            run.error_message = "Pipeline interrupted by server restart"

        exec_result = await session.execute(
            select(AgentExecution).where(
                AgentExecution.pipeline_run_id.in_(run_ids),
                AgentExecution.status.in_(["pending", "running"]),
            )
        )
        for agent_exec in exec_result.scalars().all():
            agent_exec.status = "failed"
            agent_exec.error_message = "Pipeline interrupted by server restart"
            agent_exec.completed_at = datetime.now(timezone.utc)

        await session.commit()


def create_analyzer():
    if settings.USE_MOCK_ANALYZER:
        return MockVideoAnalyzer()
    return Qwen3VLAnalyzer(api_key=settings.QWEN_API_KEY, api_url=settings.QWEN_API_URL)

# Qwen-omini-flash 全模态模型
def create_llm():
    if settings.USE_MOCK_LLM or not settings.QWEN_API_KEY:
        return MockLLMService()
    return RealLLMService(
        api_key=settings.QWEN_API_KEY,
        api_url=settings.QWEN_API_URL,
        model=settings.QWEN_OMNI_MODEL,
    )


def create_generator():
    if settings.USE_MOCK_GENERATOR:
        return MockVideoGenerator()

    provider = settings.VIDEO_GENERATOR_PROVIDER.lower()

    if provider == "mock":
        return MockVideoGenerator()

    seedance_15 = None
    seedance_20 = None
    if settings.ARK_API_KEY:
        seedance_15 = SeedanceGenerator(
            api_key=settings.ARK_API_KEY,
            model=settings.SEEDANCE_MODEL,
            base_url=settings.ARK_BASE_URL,
        )
        seedance_20 = SeedanceGenerator(
            api_key=settings.ARK_API_KEY,
            model=settings.SEEDANCE_20_MODEL,
            base_url=settings.ARK_BASE_URL,
        )

    kling = None
    api_key = settings.WAVESPEED_API_KEY or settings.KLING_API_KEY
    api_url = settings.WAVESPEED_API_URL or settings.KLING_API_URL
    if api_key:
        kling = Kling3Generator(api_key=api_key, api_url=api_url, model=settings.KLING_MODEL)

    if seedance_15 or seedance_20 or kling:
        default_model = settings.VIDEO_GENERATION_MODEL
        if default_model == "seedance1.5-pro" and seedance_15 is None and kling is not None:
            default_model = "kling"
        return VideoGeneratorRouter(
            default_model=default_model,
            seedance_15=seedance_15,
            seedance_20=seedance_20,
            kling=kling,
            fallback=MockVideoGenerator() if provider == "mock" else None,
            kling_model_name=settings.KLING_MODEL,
            seedance_15_model_name=settings.SEEDANCE_MODEL,
            seedance_20_model_name=settings.SEEDANCE_20_MODEL,
        )

    return MockVideoGenerator()


def create_compositor():
    if settings.USE_MOCK_COMPOSITOR:
        return MockImageCompositor()
    return FluxInpaintCompositor(api_key=settings.IMAGE_COMPOSITOR_API_KEY, api_url=settings.IMAGE_COMPOSITOR_API_URL)


def create_lipsync():
    if settings.USE_MOCK_LIPSYNC:
        return MockLipSyncGenerator()
    return LTX23LipSyncGenerator(api_key=settings.LTX_API_KEY, api_url=settings.LTX_API_URL)


def create_tts():
    if settings.USE_MOCK_TTS or not settings.QWEN_API_KEY:
        return MockTTSService(output_dir=settings.GENERATED_DIR)
    return RealTTSService(
        api_key=settings.QWEN_API_KEY,
        api_url=settings.QWEN_API_URL,
        model=settings.QWEN_TTS_MODEL,
        output_dir=settings.GENERATED_DIR,
    )


def create_video_editor_service(llm):
    if settings.USE_MOCK_VIDEO_EDITOR or not settings.QWEN_API_KEY:
        return MockVideoEditorService()
    return RealVideoEditorService(llm_service=llm, ffmpeg_bin=settings.FFMPEG_BIN)


def create_keyframe_extractor():
    if not shutil.which(settings.FFMPEG_BIN):
        return MockKeyframeExtractor()
    return FFmpegKeyframeExtractor()


def create_qa_reviewer():
    if not settings.QA_REVIEW_ENABLED:
        return None
    return QAReviewerAgent(llm=create_llm())


def create_pipeline_executor():
    llm = create_llm()
    generator = create_generator()
    tts = create_tts()
    editor_svc = create_video_editor_service(llm)
    keyframe_ext = create_keyframe_extractor()
    qa_reviewer = create_qa_reviewer()
    engine = settings.PIPELINE_ENGINE.lower()
    if engine == "langgraph":
        executor_cls = LangGraphPipelineExecutor
    elif engine == "pipeline":
        executor_cls = PipelineExecutor
    else:
        logger.warning("Unknown PIPELINE_ENGINE %r; falling back to langgraph", settings.PIPELINE_ENGINE)
        executor_cls = LangGraphPipelineExecutor

    return executor_cls(
        orchestrator=OrchestratorAgent(llm_service=llm),
        replication_planner=ReplicationPlannerAgent(llm_service=llm, keyframe_extractor=keyframe_ext),
        prompt_engineer=PromptEngineerAgent(llm_service=llm),
        audio_agent=AudioSubtitleAgent(tts_service=tts),
        video_gen_agent=VideoGeneratorAgent(video_generator=generator),
        video_editor=VideoEditorAgent(editor_service=editor_svc, output_dir=settings.GENERATED_DIR),
        db_session_factory=async_session,
        qa_reviewer=qa_reviewer,
    )


def create_mem0_service() -> Mem0Service | None:
    if not settings.MEM0_ENABLED or not settings.QWEN_API_KEY:
        return None

    try:
        return Mem0Service(
            llm_config={
                "provider": "openai",
                "config": {
                    "model": settings.QWEN_OMNI_MODEL,
                    "api_key": settings.QWEN_API_KEY,
                    "openai_base_url": settings.QWEN_API_URL,
                },
            },
            embedder_config={
                "provider": "openai",
                "config": {
                    "model": settings.MEM0_EMBEDDING_MODEL,
                    "api_key": settings.QWEN_API_KEY,
                    "openai_base_url": settings.QWEN_API_URL,
                    "embedding_dims": settings.MEM0_EMBEDDING_DIMS,
                },
            },
        )
    except Exception as exc:
        logger.warning("Mem0Service init failed, running without semantic memory: %s", exc)
        return None


def create_rag_service() -> RagService | None:
    """Create a RagService instance if the Qwen API key is available."""
    if not settings.QWEN_API_KEY:
        return None
    qdrant_host = getattr(settings, "MEM0_QDRANT_HOST", "localhost")
    qdrant_port = int(getattr(settings, "MEM0_QDRANT_PORT", 6333))
    return RagService(
        qwen_api_key=settings.QWEN_API_KEY,
        qwen_api_url=settings.QWEN_API_URL,
        embedding_model=settings.MEM0_EMBEDDING_MODEL,
        qdrant_host=qdrant_host,
        qdrant_port=qdrant_port,
    )


def initialize_agent_state(app: FastAPI) -> None:
    app.state.agent_memory = AgentMemoryService(async_session)
    app.state.mem0 = create_mem0_service()
    app.state.rag = create_rag_service()

    chat_llm = create_llm()
    chat_executor = create_pipeline_executor()
    tool_registry = ToolRegistry()
    register_runtime_skills(
        tool_registry=tool_registry,
        dependency_map={
            "executor": chat_executor,
            "llm_service": chat_llm,
            "db_factory": async_session,
            "memory_service": app.state.agent_memory,
            "mem0": app.state.mem0,
        },
        agent_name="chat_agent",
    )
    app.state.tool_registry = tool_registry
    app.state.chat_agent = ChatAgent(chat_llm, tool_registry, mem0=app.state.mem0)
    app.state.chat_pipeline_executor = chat_executor


async def startup_application(app: FastAPI) -> asyncio.Task:
    ensure_storage_directories()
    await init_db()
    await recover_interrupted_pipeline_runs()
    initialize_agent_state(app)
    # Initialize RAG service (creates Qdrant collection if needed)
    if app.state.rag is not None:
        await app.state.rag.initialize()
    await cleanup_old_artifacts()
    return asyncio.create_task(periodic_artifact_cleanup())


async def shutdown_application(cleanup_task: asyncio.Task | None) -> None:
    if cleanup_task is None:
        return
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
