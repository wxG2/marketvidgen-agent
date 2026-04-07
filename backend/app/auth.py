from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.models.background_template import BackgroundTemplate
from app.models.auto_chat import AutoChatSession
from app.models.material import Material
from app.models.pipeline import PipelineRun
from app.models.project import Project
from app.models.prompt import Prompt, PromptMessage
from app.models.social_account import SocialAccount
from app.models.user import User, UserSession

SESSION_COOKIE_NAME = "vidgen_session"
SESSION_TTL_DAYS = 14
PBKDF2_ITERATIONS = 120_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _algo, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt_b64.encode()),
            int(iterations),
        )
        return hmac.compare_digest(base64.b64encode(derived).decode(), digest_b64)
    except Exception:
        return False


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    cached = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    result = await db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.session_token_hash == hash_session_token(token))
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    session, user = row
    if _ensure_utc(session.expires_at) <= _utcnow():
        await db.delete(session)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    session.last_seen_at = _utcnow()
    await db.commit()
    request.state.current_user = user
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


async def create_user_session(db: AsyncSession, user: User, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        session_token_hash=hash_session_token(token),
        expires_at=_utcnow() + timedelta(days=SESSION_TTL_DAYS),
        last_seen_at=_utcnow(),
    )
    db.add(session)
    await db.commit()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        path="/",
    )


async def get_project_for_user(db: AsyncSession, user_id: str, project_id: str) -> Project:
    project = await db.get(Project, project_id)
    if not project or project.user_id != user_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def get_pipeline_run_for_user(db: AsyncSession, user_id: str, run_id: str) -> PipelineRun:
    run = await db.get(PipelineRun, run_id)
    if not run or run.user_id != user_id:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return run


async def get_auto_chat_session_for_user(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    session_id: str,
) -> AutoChatSession:
    session = await db.get(AutoChatSession, session_id)
    if not session or session.user_id != user_id or session.project_id != project_id:
        raise HTTPException(status_code=404, detail="Auto chat session not found")
    return session


async def get_material_for_user(db: AsyncSession, user_id: str, material_id: str) -> Material:
    material = await db.get(Material, material_id)
    if not material or material.user_id != user_id:
        raise HTTPException(status_code=404, detail="Material not found")
    return material


async def get_background_template_for_user(db: AsyncSession, user_id: str, template_id: str) -> BackgroundTemplate:
    template = await db.get(BackgroundTemplate, template_id)
    if not template or template.user_id != user_id:
        raise HTTPException(status_code=404, detail="Background template not found")
    return template


async def get_social_account_for_user(db: AsyncSession, user_id: str, social_account_id: str) -> SocialAccount:
    account = await db.get(SocialAccount, social_account_id)
    if not account or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Social account not found")
    return account


async def adopt_orphaned_records(db: AsyncSession, user_id: str) -> None:
    await db.execute(text("UPDATE projects SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})
    await db.execute(text("UPDATE pipeline_runs SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})
    await db.execute(text("UPDATE prompt_messages SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})
    await db.execute(text("UPDATE prompts SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})
    await db.execute(text("UPDATE materials SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})
    await db.commit()


async def count_users(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(User.id)))
    return int(result.scalar() or 0)


def compile_background_template(template: BackgroundTemplate) -> str:
    sections: Iterable[tuple[str, str | None]] = (
        ("品牌信息", template.brand_info),
        ("用户需求", template.user_requirements),
        ("角色名称", template.character_name),
        ("角色身份", template.identity),
        ("场景背景", template.scene_context),
        ("语气风格", template.tone_style),
        ("视觉风格", template.visual_style),
        ("避免内容", template.do_not_include),
        ("长期偏好", template.learned_preferences),
        ("备注", template.notes),
    )
    lines = [f"{label}：{value.strip()}" for label, value in sections if value and value.strip()]
    return "\n".join(lines)
