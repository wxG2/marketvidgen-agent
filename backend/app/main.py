from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.bootstrap import (
    create_analyzer,
    create_compositor,
    create_generator,
    create_lipsync,
    create_llm,
    create_pipeline_executor,
    ensure_storage_directories,
    shutdown_application,
    startup_application,
)
from app.core.config import settings
from app.core.http import configure_exception_handlers, configure_middleware
from app.core.logging import configure_logging
from app.mcp.router import mcp_router
from app.routers.analytics import router as analytics_router
from app.routers import api_keys, examples, materials, projects, timeline, upload
from app.routers.analysis import get_analysis_router
from app.routers.auth import router as auth_router
from app.routers.auto_sessions import router as auto_sessions_router
from app.routers.background_templates import get_background_templates_router
from app.routers.generation import get_generation_router
from app.routers.pipeline import get_pipeline_router
from app.routers.prompts import get_prompt_router
from app.routers.public_video_jobs import get_public_video_jobs_router
from app.routers.repository import router as repository_router
from app.routers.social_accounts import router as social_accounts_router
from app.routers.system import router as system_router
from app.routers.talking_head import get_talking_head_router

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = await startup_application(app)
    try:
        yield
    finally:
        await shutdown_application(cleanup_task)


app = FastAPI(title="capy API", version="0.1.0", lifespan=lifespan)

configure_exception_handlers(app)
configure_middleware(app)


def register_routers(app: FastAPI) -> None:
    app.include_router(auth_router)
    app.include_router(api_keys.router)
    app.include_router(api_keys.admin_router)
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
    app.include_router(get_public_video_jobs_router(create_pipeline_executor()))
    app.include_router(get_background_templates_router(create_llm()))
    app.include_router(repository_router)
    app.include_router(system_router)
    app.include_router(mcp_router)
    app.include_router(analytics_router)


def mount_static_files(app: FastAPI) -> None:
    ensure_storage_directories()
    app.mount("/examples", StaticFiles(directory=settings.EXAMPLES_ROOT), name="examples")
    app.mount("/generated", StaticFiles(directory=settings.GENERATED_DIR), name="generated")
    app.mount("/repository", StaticFiles(directory=settings.VIDEO_REPOSITORY_DIR), name="repository")


register_routers(app)
mount_static_files(app)
