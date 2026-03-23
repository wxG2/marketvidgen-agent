from __future__ import annotations

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import projects, upload, materials, timeline, examples
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
from app.routers.pipeline import get_pipeline_router
from app.agents import (
    OrchestratorAgent, PromptEngineerAgent, AudioSubtitleAgent,
    VideoGeneratorAgent, VideoEditorAgent, PipelineExecutor,
)
from app.database import async_session


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


def create_pipeline_executor():
    llm = create_llm()
    generator = create_generator()
    tts = create_tts()
    editor_svc = create_video_editor_service(llm)

    return PipelineExecutor(
        orchestrator=OrchestratorAgent(llm_service=llm),
        prompt_engineer=PromptEngineerAgent(llm_service=llm),
        audio_agent=AudioSubtitleAgent(tts_service=tts),
        video_gen_agent=VideoGeneratorAgent(video_generator=generator),
        video_editor=VideoEditorAgent(editor_service=editor_svc, output_dir=settings.GENERATED_DIR),
        db_session_factory=async_session,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure materials directory exists
    os.makedirs(settings.MATERIALS_ROOT, exist_ok=True)
    os.makedirs(settings.EXAMPLES_ROOT, exist_ok=True)
    await init_db()
    yield


app = FastAPI(title="VidGen API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": str(exc)})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(projects.router)
app.include_router(upload.router)
app.include_router(get_analysis_router(create_analyzer()))
app.include_router(materials.router)
app.include_router(examples.router)
app.include_router(get_prompt_router(create_llm()))
app.include_router(get_generation_router(create_generator()))
app.include_router(get_talking_head_router(create_compositor(), create_lipsync()))
app.include_router(timeline.router)
app.include_router(get_pipeline_router(create_pipeline_executor()))
app.mount("/examples", StaticFiles(directory=settings.EXAMPLES_ROOT), name="examples")
os.makedirs(settings.GENERATED_DIR, exist_ok=True)
app.mount("/generated", StaticFiles(directory=settings.GENERATED_DIR), name="generated")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
