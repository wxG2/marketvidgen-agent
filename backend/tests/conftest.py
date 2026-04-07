"""Shared fixtures for the VidGen backend test suite."""
from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
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
os.environ.setdefault("TESTING", "true")

# Ensure temp dirs exist so StaticFiles mounts don't fail
for d in ("/tmp/vidgen_test_materials", "/tmp/vidgen_test_examples", "/tmp/vidgen_test_generated"):
    os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------------------
# Now import app modules (they will pick up the env vars above).
# ---------------------------------------------------------------------------
from app.database import Base, get_db  # noqa: E402
from app.auth import SESSION_COOKIE_NAME, hash_password, hash_session_token  # noqa: E402
from app.models import *  # noqa: F401,F403,E402  -- register all models
from app.models.user import User, UserSession  # noqa: E402
from app.main import app  # noqa: E402
import app.main as main_module  # noqa: E402

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
main_module.async_session = TestSessionLocal


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
        async with TestSessionLocal() as session:
            user = User(
                username=f"user_{uuid.uuid4().hex}",
                password_hash=hash_password("password123"),
                role="admin",
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            raw_token = secrets.token_urlsafe(32)
            session.add(
                UserSession(
                    user_id=user.id,
                    session_token_hash=hash_session_token(raw_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                    last_seen_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            ac.cookies.set(SESSION_COOKIE_NAME, raw_token)
        yield ac


@pytest_asyncio.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a raw async DB session for direct DB assertions."""
    async with TestSessionLocal() as session:
        yield session
