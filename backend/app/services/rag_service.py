"""RAG (Retrieval-Augmented Generation) service for vidgen.

Indexes completed pipeline runs (requirement → orchestrator plan) into Qdrant
and retrieves similar historical plans to augment new generation requests.

Architecture:
  - Collection: "vidgen_pipeline_plans"
  - Embedding: Qwen text-embedding-v3 (1024-dim)
  - Metadata stored per vector: project_id, requirement, platform, style,
    orchestrator_plan_summary, overall_score

Usage in OrchestratorAgent:

    rag = RagService(qwen_api_key, qwen_api_url)

    # Index a completed run (call after pipeline completes)
    await rag.index_pipeline_run(run_id, requirement, platform, plan_summary, score)

    # Retrieve similar plans before LLM call
    examples = await rag.retrieve_similar(requirement, platform, limit=3)
    # → inject into system prompt as few-shot context
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_COLLECTION = "vidgen_pipeline_plans"
_EMBEDDING_DIM = 1024


class RagService:
    """Vector-based retrieval of past video generation plans.

    Uses Qdrant for vector storage and Qwen text-embedding-v3 for embeddings.
    Provides two operations:
      - index_pipeline_run: add a completed run to the knowledge base
      - retrieve_similar: find the K most similar historical plans

    Falls back gracefully (returns []) if Qdrant or the embedding API is unavailable.
    """

    def __init__(
        self,
        qwen_api_key: str,
        qwen_api_url: str,
        embedding_model: str = "text-embedding-v3",
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
    ) -> None:
        self._api_key = qwen_api_key
        self._embed_url = f"{qwen_api_url.rstrip('/')}/embeddings"
        self._embed_model = embedding_model
        self._qdrant_base = f"http://{qdrant_host}:{qdrant_port}"
        self._ready = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Ensure the Qdrant collection exists. Safe to call multiple times."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Check if collection exists
                resp = await client.get(f"{self._qdrant_base}/collections/{_COLLECTION}")
                if resp.status_code == 404:
                    # Create collection
                    await client.put(
                        f"{self._qdrant_base}/collections/{_COLLECTION}",
                        json={
                            "vectors": {
                                "size": _EMBEDDING_DIM,
                                "distance": "Cosine",
                            }
                        },
                    )
                    logger.info("rag_service: created Qdrant collection '%s'", _COLLECTION)
            self._ready = True
        except Exception as exc:
            logger.warning("rag_service: Qdrant unavailable, RAG disabled: %s", exc)
            self._ready = False

    # ── Embedding ─────────────────────────────────────────────────────────────

    async def _embed(self, text: str) -> Optional[list[float]]:
        """Call Qwen embedding API and return the vector."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self._embed_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self._embed_model, "input": [text]},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as exc:
            logger.warning("rag_service: embedding failed: %s", exc)
            return None

    # ── Index ─────────────────────────────────────────────────────────────────

    async def index_pipeline_run(
        self,
        run_id: str,
        requirement: str,
        platform: str,
        style: str,
        plan_summary: str,
        overall_score: Optional[float] = None,
    ) -> bool:
        """Index a completed pipeline run into the RAG knowledge base.

        Args:
            run_id: Pipeline run UUID (used as Qdrant point ID prefix).
            requirement: Original user requirement text.
            platform: Target platform (douyin, xiaohongshu, etc.).
            style: Video style (commercial, lifestyle, etc.).
            plan_summary: Summary of the orchestrator's shot plan.
            overall_score: QA score (0-10), if available.

        Returns:
            True if indexed successfully, False otherwise.
        """
        if not self._ready:
            return False

        # Combine fields for embedding
        index_text = f"平台:{platform} 风格:{style} 需求:{requirement} 计划摘要:{plan_summary}"
        vector = await self._embed(index_text)
        if vector is None:
            return False

        import hashlib
        # Qdrant requires integer or UUID point IDs
        point_id = int(hashlib.md5(run_id.encode()).hexdigest()[:8], 16)

        payload = {
            "run_id": run_id,
            "requirement": requirement,
            "platform": platform,
            "style": style,
            "plan_summary": plan_summary,
            "overall_score": overall_score,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.put(
                    f"{self._qdrant_base}/collections/{_COLLECTION}/points",
                    json={
                        "points": [
                            {
                                "id": point_id,
                                "vector": vector,
                                "payload": payload,
                            }
                        ]
                    },
                )
                resp.raise_for_status()
            logger.info("rag_service: indexed run '%s' (score=%s)", run_id, overall_score)
            return True
        except Exception as exc:
            logger.warning("rag_service: index failed for run '%s': %s", run_id, exc)
            return False

    # ── Retrieve ──────────────────────────────────────────────────────────────

    async def retrieve_similar(
        self,
        requirement: str,
        platform: str = "",
        limit: int = 3,
        min_score: float = 0.6,
    ) -> list[dict[str, Any]]:
        """Retrieve the K most similar historical plans for a new requirement.

        Args:
            requirement: The new user requirement to match against.
            platform: Optional platform filter (applied post-retrieval).
            limit: Maximum number of results to return.
            min_score: Minimum cosine similarity threshold (0-1).

        Returns:
            List of payload dicts sorted by similarity, highest first.
            Each dict has: run_id, requirement, platform, style, plan_summary,
            overall_score, similarity_score.
        """
        if not self._ready:
            return []

        query_text = f"平台:{platform} 需求:{requirement}"
        vector = await self._embed(query_text)
        if vector is None:
            return []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._qdrant_base}/collections/{_COLLECTION}/points/search",
                    json={
                        "vector": vector,
                        "limit": limit * 2,  # over-fetch for platform filtering
                        "with_payload": True,
                        "score_threshold": min_score,
                    },
                )
                resp.raise_for_status()
                hits = resp.json().get("result", [])
        except Exception as exc:
            logger.warning("rag_service: retrieve failed: %s", exc)
            return []

        results = []
        for hit in hits:
            payload = hit.get("payload", {})
            # Optional platform filter
            if platform and payload.get("platform") != platform:
                continue
            results.append({**payload, "similarity_score": hit.get("score", 0.0)})
            if len(results) >= limit:
                break

        return results

    def format_for_prompt(self, examples: list[dict[str, Any]]) -> str:
        """Format retrieved examples as a prompt snippet for injection.

        Returns a Markdown-formatted section describing past similar projects,
        suitable for appending to an LLM system or user prompt.
        """
        if not examples:
            return ""

        lines = ["## 历史相似项目参考（RAG检索）\n"]
        for i, ex in enumerate(examples, 1):
            score = ex.get("similarity_score", 0)
            lines.append(
                f"### 参考案例 {i}（相似度 {score:.2f}）\n"
                f"- 平台: {ex.get('platform', '-')}\n"
                f"- 风格: {ex.get('style', '-')}\n"
                f"- 需求: {ex.get('requirement', '-')}\n"
                f"- 方案摘要: {ex.get('plan_summary', '-')}\n"
                f"- 历史评分: {ex.get('overall_score', '-')}\n"
            )
        lines.append("请参考以上历史方案，但结合当前需求做出适当调整。\n")
        return "\n".join(lines)
