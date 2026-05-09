from __future__ import annotations

import os
import traceback
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.security import get_current_user
from app.core.config import settings
from app.db.session import async_session

logger = structlog.get_logger(__name__)

AUTH_EXEMPT_PATHS = {
    "/api/health",
    "/api/auth/register",
    "/api/auth/login",
    "/api/social-accounts/douyin/callback",
    "/openapi.json",
    "/docs",
    "/redoc",
}


def configure_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        traceback.print_exc()
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error("unhandled_exception", request_id=request_id, error=str(exc.__class__.__name__))
        # Return generic message to avoid leaking internal details
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def configure_middleware(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """Inject a unique request_id into every request for tracing."""
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

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
