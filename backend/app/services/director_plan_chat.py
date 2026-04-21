"""Persist a director plan as an assistant AutoChatMessage after PromptEngineer completes."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auto_chat import AutoChatMessage, AutoChatSession
from app.models.pipeline import PipelineRun

logger = logging.getLogger(__name__)


async def persist_director_plan_message(
    db_session_factory: Callable[[], Any],
    pipeline_run_id: str,
    prompt_plan: dict,
) -> None:
    """Upsert an assistant chat message carrying the director plan for this run.

    If a message with role='assistant' and title='director_plan' already exists
    for the same run (identified via session_id + run_id stored in payload), it is
    replaced so re-runs don't create duplicates.
    """
    try:
        async with db_session_factory() as db:
            run: PipelineRun | None = await db.get(PipelineRun, pipeline_run_id)
            if run is None or not run.session_id:
                return

            session: AutoChatSession | None = await db.get(AutoChatSession, run.session_id)
            if session is None:
                return

            # Deduplicate: remove any existing director_plan message for this run
            existing_q = await db.execute(
                select(AutoChatMessage).where(
                    AutoChatMessage.session_id == run.session_id,
                    AutoChatMessage.role == "assistant",
                    AutoChatMessage.title == "director_plan",
                )
            )
            for old_msg in existing_q.scalars().all():
                try:
                    old_payload = json.loads(old_msg.payload_json or "{}")
                    if old_payload.get("directorPlan", {}).get("run_id") == pipeline_run_id:
                        await db.delete(old_msg)
                except Exception:
                    pass

            shot_prompts = prompt_plan.get("shot_prompts", [])
            voice_design = prompt_plan.get("voice_design", {})
            director_summary = prompt_plan.get("director_summary", "")

            content = _format_director_plan_text(shot_prompts, voice_design, director_summary)

            payload = {
                "directorPlan": {
                    "run_id": pipeline_run_id,
                    "shot_prompts": shot_prompts,
                    "voice_design": voice_design,
                    "director_summary": director_summary,
                    "creative_concept": prompt_plan.get("creative_concept", ""),
                    "pacing_strategy": prompt_plan.get("pacing_strategy", ""),
                    "narration_script": prompt_plan.get("narration_script", ""),
                }
            }

            msg = AutoChatMessage(
                session_id=run.session_id,
                role="assistant",
                title="director_plan",
                content=content,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
            db.add(msg)

            session.last_activity_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("[%s] Persisted director plan message for session %s", pipeline_run_id, run.session_id)
    except Exception as exc:
        logger.warning("[%s] Failed to persist director plan message: %s", pipeline_run_id, exc)


def _format_director_plan_text(shot_prompts: list[dict], voice_design: dict, director_summary: str) -> str:
    lines: list[str] = []
    if director_summary:
        lines.append(f"**导演方案**：{director_summary}\n")

    lines.append("## 镜头设计方案\n")
    for shot in shot_prompts:
        idx = shot.get("shot_idx", "?")
        dur = shot.get("duration_seconds", "?")
        range_label = shot.get("duration_range_label", "")
        script = shot.get("script_segment", "").strip()
        prompt = shot.get("video_prompt", "").strip()
        dur_display = f"{range_label}（建议 {dur}s）" if range_label else f"{dur}s"
        lines.append(f"### 镜头 {idx + 1}（{dur_display}）")
        if script:
            lines.append(f"**旁白**：{script}")
        if prompt:
            lines.append(f"**视频提示词**：{prompt}")
        lines.append("")

    if voice_design:
        lines.append("## 配音方案")
        voice_id = voice_design.get("voice_id", "")
        speed = voice_design.get("speed", 1.0)
        tone = voice_design.get("tone", "")
        if voice_id:
            lines.append(f"- 声音：{voice_id}")
        if speed:
            lines.append(f"- 语速：{speed}")
        if tone:
            lines.append(f"- 情绪：{tone}")
        lines.append("")

    lines.append("---\n确认后将继续生成视频。")
    return "\n".join(lines)
