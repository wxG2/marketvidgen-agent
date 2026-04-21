from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent_memory import AgentMemory

logger = logging.getLogger(__name__)


class AgentMemoryService:
    """Persistent cross-run memory for agent preferences and learned patterns.

    Stores user-scoped key-value pairs that agents can read and write across
    pipeline runs. Typical use-cases:

    - ``voice.preferred_params``   – last successful TTS voice settings
    - ``platform_style.<platform>`` – learned prompt style hints per platform
    - ``shot.preferred_duration``  – preferred shot duration pattern
    """

    def __init__(self, db_session_factory: async_sessionmaker):
        self._db = db_session_factory

    def _default_namespace_key(self, user_id: str) -> str:
        return f"user:{user_id}:vidgen"

    # ── Core read/write ──────────────────────────────────────────────────────

    async def get(
        self,
        user_id: str,
        key: str,
        *,
        scope: str = "user",
        namespace_key: Optional[str] = None,
    ) -> Optional[Any]:
        """Return the stored value for *key*, or ``None`` if absent."""
        namespace_key = namespace_key or self._default_namespace_key(user_id)
        async with self._db() as session:
            result = await session.execute(
                select(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.scope == scope,
                    AgentMemory.namespace_key == namespace_key,
                    AgentMemory.memory_key == key,
                )
            )
            record = result.scalar_one_or_none()
            if record and record.content_json is not None:
                try:
                    return json.loads(record.content_json)
                except json.JSONDecodeError:
                    return record.content_json
        return None

    async def set(
        self,
        user_id: str,
        key: str,
        value: Any,
        *,
        scope: str = "user",
        namespace_key: Optional[str] = None,
        summary: Optional[str] = None,
        source_type: Optional[str] = None,
        source_thread_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
        importance: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Upsert *value* (any JSON-serialisable object) for *key*."""
        namespace_key = namespace_key or self._default_namespace_key(user_id)
        content_json = json.dumps(value, ensure_ascii=False, default=str)
        metadata_json = (
            json.dumps(metadata, ensure_ascii=False, default=str)
            if metadata is not None
            else None
        )
        async with self._db() as session:
            result = await session.execute(
                select(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.scope == scope,
                    AgentMemory.namespace_key == namespace_key,
                    AgentMemory.memory_key == key,
                )
            )
            record = result.scalar_one_or_none()
            if record:
                record.content_json = content_json
                record.summary = summary
                record.source_type = source_type
                record.source_thread_id = source_thread_id
                record.source_run_id = source_run_id
                record.importance = importance
                record.metadata_json = metadata_json
                record.updated_at = datetime.now(timezone.utc)
            else:
                session.add(
                    AgentMemory(
                        user_id=user_id,
                        scope=scope,
                        namespace_key=namespace_key,
                        memory_key=key,
                        content_json=content_json,
                        summary=summary,
                        source_type=source_type,
                        source_thread_id=source_thread_id,
                        source_run_id=source_run_id,
                        importance=importance,
                        metadata_json=metadata_json,
                    )
                )
            await session.commit()

    async def get_all(
        self,
        user_id: str,
        prefix: str = "",
        *,
        scope: str = "user",
        namespace_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return all memories for *user_id*, optionally filtered by key prefix."""
        namespace_key = namespace_key or self._default_namespace_key(user_id)
        async with self._db() as session:
            stmt = select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.scope == scope,
                AgentMemory.namespace_key == namespace_key,
                AgentMemory.archived_at.is_(None),
            )
            if prefix:
                stmt = stmt.where(AgentMemory.memory_key.startswith(prefix))
            result = await session.execute(stmt)
            records = result.scalars().all()

        out: dict[str, Any] = {}
        for r in records:
            try:
                out[r.memory_key] = (
                    json.loads(r.content_json) if r.content_json is not None else None
                )
            except json.JSONDecodeError:
                out[r.memory_key] = r.content_json
        return out

    async def delete(
        self,
        user_id: str,
        key: str,
        *,
        scope: str = "user",
        namespace_key: Optional[str] = None,
    ) -> None:
        """Archive a specific memory entry instead of hard deleting it."""
        namespace_key = namespace_key or self._default_namespace_key(user_id)
        async with self._db() as session:
            result = await session.execute(
                select(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.scope == scope,
                    AgentMemory.namespace_key == namespace_key,
                    AgentMemory.memory_key == key,
                )
            )
            record = result.scalar_one_or_none()
            if record:
                now = datetime.now(timezone.utc)
                record.archived_at = now
                record.updated_at = now
                await session.commit()

    # ── Typed convenience helpers ────────────────────────────────────────────

    async def remember_voice_params(self, user_id: str, voice_params: dict) -> None:
        """Persist the most-recently successful TTS voice parameters."""
        await self.set(user_id, "voice.preferred_params", voice_params)
        logger.debug(f"[memory] Saved voice params for user {user_id}")

    async def recall_voice_params(self, user_id: str) -> Optional[dict]:
        """Retrieve previously saved voice params, or ``None``."""
        return await self.get(user_id, "voice.preferred_params")

    async def remember_platform_style(
        self, user_id: str, platform: str, style_hints: str
    ) -> None:
        """Persist a platform-specific prompt style description."""
        await self.set(user_id, f"platform_style.{platform}", style_hints)

    async def recall_platform_style(
        self, user_id: str, platform: str
    ) -> Optional[str]:
        """Retrieve saved platform style hints, or ``None``."""
        return await self.get(user_id, f"platform_style.{platform}")

    async def remember_shot_duration_pattern(
        self, user_id: str, platform: str, durations: list[int]
    ) -> None:
        """Save a successful shot-duration allocation for future reference."""
        await self.set(user_id, f"shot_durations.{platform}", durations)

    async def recall_shot_duration_pattern(
        self, user_id: str, platform: str
    ) -> Optional[list[int]]:
        return await self.get(user_id, f"shot_durations.{platform}")
