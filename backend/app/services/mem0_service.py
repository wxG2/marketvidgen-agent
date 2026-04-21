from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Mem0Service:
    """语义记忆服务，封装 Mem0 AsyncMemory。

    提供用户级（跨 session）的语义记忆：
    - 自动从对话中提取偏好/事实并存储
    - 基于向量相似度检索相关记忆
    - 显式添加结构化记忆

    使用 Qdrant 内嵌存储（无需独立部署），LLM 用 Qwen 兼容接口。
    """

    def __init__(self, llm_config: dict[str, Any], embedder_config: dict[str, Any]):
        from mem0 import AsyncMemory
        config = {
            "llm": llm_config,
            "embedder": embedder_config,
            "version": "v1.1",
        }
        self._memory = AsyncMemory.from_config(config)

    async def add_from_conversation(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        session_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """从对话中自动提取并存储记忆。

        Mem0 会识别对话中值得记住的信息（偏好、风格倾向、事实等）并持久化。
        相同信息会被合并，冲突信息会被更新。
        """
        merged_metadata: dict[str, Any] = {**(metadata or {})}
        if session_id:
            merged_metadata["session_id"] = session_id
        try:
            result = await self._memory.add(
                messages=messages,
                user_id=user_id,
                metadata=merged_metadata or None,
            )
            return result if isinstance(result, dict) else {"results": result}
        except Exception as exc:
            logger.warning(f"[mem0] add_from_conversation failed for user {user_id}: {exc}")
            return {}

    async def search(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """语义搜索用户相关记忆，返回最相关的 limit 条。

        每条结果格式：{"memory": "...", "score": 0.9, "id": "..."}
        """
        try:
            results = await self._memory.search(
                query=query,
                user_id=user_id,
                limit=limit,
            )
            if isinstance(results, dict):
                return results.get("results", [])
            return list(results) if results else []
        except Exception as exc:
            logger.warning(f"[mem0] search failed for user {user_id}, query={query!r}: {exc}")
            return []

    async def get_all(self, user_id: str) -> list[dict[str, Any]]:
        """获取用户的所有记忆。"""
        try:
            results = await self._memory.get_all(user_id=user_id)
            if isinstance(results, dict):
                return results.get("results", [])
            return list(results) if results else []
        except Exception as exc:
            logger.warning(f"[mem0] get_all failed for user {user_id}: {exc}")
            return []

    async def add_explicit(
        self,
        content: str,
        user_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """显式添加一条记忆（非从对话自动提取）。

        适用于 pipeline 完成后存储结构化结果，如平台偏好、镜头时长模式等。
        """
        try:
            result = await self._memory.add(
                messages=[{"role": "user", "content": content}],
                user_id=user_id,
                metadata=metadata,
            )
            return result if isinstance(result, dict) else {"results": result}
        except Exception as exc:
            logger.warning(f"[mem0] add_explicit failed for user {user_id}: {exc}")
            return {}
