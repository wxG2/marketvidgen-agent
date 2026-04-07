from __future__ import annotations

import os
import shutil
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.auth import get_current_user
from app.config import settings
from app.database import init_db
from app.routers import projects, upload, materials, timeline, examples
from app.routers.auth import router as auth_router
from app.routers.auto_sessions import router as auto_sessions_router
from app.routers.background_templates import get_background_templates_router
from app.routers.social_accounts import router as social_accounts_router
from app.routers.analysis import get_analysis_router
from app.routers.prompts import get_prompt_router
from app.routers.generation import get_generation_router
from app.routers.talking_head import get_talking_head_router
from app.services.video_analyzer import MockVideoAnalyzer, Qwen3VLAnalyzer
from app.services.llm_service import MockLLMService, RealLLMService
from app.services.video_generator import MockVideoGenerator, Kling3Generator, SeedanceGenerator
from app.services.image_compositor import MockImageCompositor, FluxInpaintCompositor
from app.services.lipsync_generator import MockLipSyncGenerator, LTX23LipSyncGenerator
from app.services.tts_service import MockTTSService, RealTTSService
from app.services.video_editor_service import MockVideoEditorService, RealVideoEditorService
from app.services.keyframe_extractor import FFmpegKeyframeExtractor, MockKeyframeExtractor
from app.routers.pipeline import get_pipeline_router
from app.routers.repository import router as repository_router
from app.agents import (
    OrchestratorAgent, PromptEngineerAgent, AudioSubtitleAgent,
    VideoGeneratorAgent, VideoEditorAgent, QAReviewerAgent,
    PipelineExecutor, LangGraphPipelineExecutor, SwarmPipelineExecutor,
)
from app.services.agent_memory import AgentMemoryService
from app.database import async_session
import app.models  # noqa: F401
from app.models.pipeline import PipelineRun


async def recover_interrupted_pipeline_runs():
    """Mark in-flight runs as failed after a server restart."""
    async with async_session() as session:
        result = await session.execute(
            select(PipelineRun).where(PipelineRun.status.in_(["pending", "running"]))
        )
        runs = result.scalars().all()
        if not runs:
            return

        for run in runs:
            run.status = "failed"
            run.error_message = "Pipeline interrupted by server restart"
        await session.commit()


# Dependency injection
def create_analyzer():
    if settings.USE_MOCK_ANALYZER:
        return MockVideoAnalyzer()
    return Qwen3VLAnalyzer(api_key=settings.QWEN_API_KEY, api_url=settings.QWEN_API_URL)


def create_llm():
    if not settings.QWEN_API_KEY:
        return MockLLMService()
    return RealLLMService(
        api_key=settings.QWEN_API_KEY,
        api_url=settings.QWEN_API_URL,
        model=settings.QWEN_OMNI_MODEL,
    )


def create_generator():
    provider = settings.VIDEO_GENERATOR_PROVIDER.lower()

    if provider == "seedance" and settings.ARK_API_KEY:
        return SeedanceGenerator(
            api_key=settings.ARK_API_KEY,
            model=settings.SEEDANCE_MODEL,
            base_url=settings.ARK_BASE_URL,
        )

    if provider == "wavespeed":
        api_key = settings.WAVESPEED_API_KEY or settings.KLING_API_KEY
        api_url = settings.WAVESPEED_API_URL or settings.KLING_API_URL
        if api_key:
            return Kling3Generator(api_key=api_key, api_url=api_url, model=settings.KLING_MODEL)

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
    if not settings.QWEN_API_KEY:
        return MockTTSService(output_dir=settings.GENERATED_DIR)
    return RealTTSService(
        api_key=settings.QWEN_API_KEY,
        api_url=settings.QWEN_API_URL,
        model=settings.QWEN_TTS_MODEL,
        output_dir=settings.GENERATED_DIR,
    )


def create_video_editor_service(llm):
    if not settings.QWEN_API_KEY:
        return MockVideoEditorService()
    return RealVideoEditorService(llm_service=llm, ffmpeg_bin=settings.FFMPEG_BIN)


def create_keyframe_extractor():
    ffmpeg_exists = bool(shutil.which(settings.FFMPEG_BIN))
    if not ffmpeg_exists:
        return MockKeyframeExtractor()
    return FFmpegKeyframeExtractor()


def create_qa_reviewer():
    """Create QA reviewer agent (uses same LLM as the rest of the pipeline)."""
    if not settings.QA_REVIEW_ENABLED:
        return None
    llm = create_llm()
    return QAReviewerAgent(llm=llm)


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
    elif engine == "swarm":
        executor_cls = SwarmPipelineExecutor
    else:
        executor_cls = PipelineExecutor

    return executor_cls(
        orchestrator=OrchestratorAgent(llm_service=llm, keyframe_extractor=keyframe_ext),
        prompt_engineer=PromptEngineerAgent(llm_service=llm),
        audio_agent=AudioSubtitleAgent(tts_service=tts),
        video_gen_agent=VideoGeneratorAgent(video_generator=generator),
        video_editor=VideoEditorAgent(editor_service=editor_svc, output_dir=settings.GENERATED_DIR),
        db_session_factory=async_session,
        qa_reviewer=qa_reviewer,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure materials directory exists
    os.makedirs(settings.MATERIALS_ROOT, exist_ok=True)
    os.makedirs(settings.EXAMPLES_ROOT, exist_ok=True)
    os.makedirs(settings.VIDEO_REPOSITORY_DIR, exist_ok=True)
    await init_db()
    await recover_interrupted_pipeline_runs()
    # Make AgentMemoryService available app-wide
    app.state.agent_memory = AgentMemoryService(async_session)

    import asyncio
    from app.services.artifact_cleanup import cleanup_old_artifacts, periodic_artifact_cleanup

    await cleanup_old_artifacts()
    cleanup_task = asyncio.create_task(periodic_artifact_cleanup())

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="capy API", version="0.1.0", lifespan=lifespan)

AUTH_EXEMPT_PATHS = {
    "/api/health",
    "/api/auth/register",
    "/api/auth/login",
    "/api/social-accounts/douyin/callback",
    "/openapi.json",
    "/docs",
    "/redoc",
}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": str(exc)})

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if os.getenv("TESTING", "").lower() == "true":
        return await call_next(request)
    path = request.url.path
    if (
        path in AUTH_EXEMPT_PATHS
        or path.startswith("/docs")
        or path.startswith("/redoc")
        or path.startswith("/generated/")
        or path.startswith("/repository/")
        or path.startswith("/examples/")
    ):
        return await call_next(request)

    if path.startswith("/api/"):
        try:
            async with async_session() as session:
                user = await get_current_user(request, session)
                request.state.current_user = user
        except Exception as exc:
            status_code = getattr(exc, "status_code", 401)
            detail = getattr(exc, "detail", "Authentication required")
            return JSONResponse(status_code=status_code, content={"detail": detail})

    return await call_next(request)

# Register routers
app.include_router(auth_router)
app.include_router(projects.router)
app.include_router(upload.router)
app.include_router(auto_sessions_router)
app.include_router(social_accounts_router)
app.include_router(get_analysis_router(create_analyzer()))
app.include_router(materials.router)
app.include_router(examples.router)
app.include_router(get_prompt_router(create_llm()))
app.include_router(get_generation_router(create_generator()))
app.include_router(get_talking_head_router(create_compositor(), create_lipsync()))
app.include_router(timeline.router)
app.include_router(get_pipeline_router(create_pipeline_executor()))
app.include_router(get_background_templates_router(create_llm()))
app.include_router(repository_router)
app.mount("/examples", StaticFiles(directory=settings.EXAMPLES_ROOT), name="examples")
os.makedirs(settings.GENERATED_DIR, exist_ok=True)
os.makedirs(settings.VIDEO_REPOSITORY_DIR, exist_ok=True)
app.mount("/generated", StaticFiles(directory=settings.GENERATED_DIR), name="generated")
app.mount("/repository", StaticFiles(directory=settings.VIDEO_REPOSITORY_DIR), name="repository")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/admin/cleanup-artifacts")
async def cleanup_artifacts(retention_days: int = 7):
    """Manually trigger artifact cleanup."""
    from app.services.artifact_cleanup import cleanup_old_artifacts
    result = await cleanup_old_artifacts(retention_days=retention_days)
    return result
