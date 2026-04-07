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

    # ── Core read/write ──────────────────────────────────────────────────────

    async def get(self, user_id: str, key: str) -> Optional[Any]:
        """Return the stored value for *key*, or ``None`` if absent."""
        async with self._db() as session:
            result = await session.execute(
                select(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.key == key,
                )
            )
            record = result.scalar_one_or_none()
            if record and record.value_json is not None:
                try:
                    return json.loads(record.value_json)
                except json.JSONDecodeError:
                    return record.value_json
        return None

    async def set(self, user_id: str, key: str, value: Any) -> None:
        """Upsert *value* (any JSON-serialisable object) for *key*."""
        value_json = json.dumps(value, ensure_ascii=False, default=str)
        async with self._db() as session:
            result = await session.execute(
                select(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.key == key,
                )
            )
            record = result.scalar_one_or_none()
            if record:
                record.value_json = value_json
                record.updated_at = datetime.now(timezone.utc)
            else:
                session.add(
                    AgentMemory(user_id=user_id, key=key, value_json=value_json)
                )
            await session.commit()

    async def get_all(self, user_id: str, prefix: str = "") -> dict[str, Any]:
        """Return all memories for *user_id*, optionally filtered by key prefix."""
        async with self._db() as session:
            stmt = select(AgentMemory).where(AgentMemory.user_id == user_id)
            if prefix:
                stmt = stmt.where(AgentMemory.key.startswith(prefix))
            result = await session.execute(stmt)
            records = result.scalars().all()

        out: dict[str, Any] = {}
        for r in records:
            try:
                out[r.key] = json.loads(r.value_json) if r.value_json is not None else None
            except json.JSONDecodeError:
                out[r.key] = r.value_json
        return out

    async def delete(self, user_id: str, key: str) -> None:
        """Delete a specific memory entry."""
        async with self._db() as session:
            result = await session.execute(
                select(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.key == key,
                )
            )
            record = result.scalar_one_or_none()
            if record:
                await session.delete(record)
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
