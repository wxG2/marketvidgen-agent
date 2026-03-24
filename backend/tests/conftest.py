"""Shared fixtures for the VidGen backend test suite."""
from __future__ import annotations

import os
from typing import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

# ---------------------------------------------------------------------------
# Force settings to use in-memory SQLite *before* any app module is imported.
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MATERIALS_ROOT", "/tmp/vidgen_test_materials")
os.environ.setdefault("EXAMPLES_ROOT", "/tmp/vidgen_test_examples")
os.environ.setdefault("GENERATED_DIR", "/tmp/vidgen_test_generated")
os.environ.setdefault("USE_MOCK_ANALYZER", "true")
os.environ.setdefault("USE_MOCK_LLM", "true")
os.environ.setdefault("USE_MOCK_GENERATOR", "true")
os.environ.setdefault("USE_MOCK_COMPOSITOR", "true")
os.environ.setdefault("USE_MOCK_LIPSYNC", "true")
os.environ.setdefault("USE_MOCK_TTS", "true")
os.environ.setdefault("USE_MOCK_VIDEO_EDITOR", "true")
os.environ.setdefault("VIDEO_GENERATOR_PROVIDER", "mock")

# Ensure temp dirs exist so StaticFiles mounts don't fail
for d in ("/tmp/vidgen_test_materials", "/tmp/vidgen_test_examples", "/tmp/vidgen_test_generated"):
    os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------------------
# Now import app modules (they will pick up the env vars above).
# ---------------------------------------------------------------------------
from app.database import Base, get_db  # noqa: E402
from app.models import *  # noqa: F401,F403,E402  -- register all models
from app.main import app  # noqa: E402

# ---------------------------------------------------------------------------
# Test engine / session (in-memory SQLite)
# ---------------------------------------------------------------------------
test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


# Override the production DB dependency with the test one.
app.dependency_overrides[get_db] = _override_get_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_tables():
    """Create all tables once for the test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an httpx AsyncClient wired to the FastAPI test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a raw async DB session for direct DB assertions."""
    async with TestSessionLocal() as session:
        yield session
