from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth import compile_background_template
from app.models.background_template import BackgroundTemplate, BackgroundTemplateLearningLog
from app.services.llm_service import LLMService

LEARNABLE_FIELDS = [
    "tone_style",
    "visual_style",
    "do_not_include",
    "notes",
    "learned_preferences",
    "last_learned_summary",
]


def _template_snapshot(template: BackgroundTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "brand_info": template.brand_info,
        "user_requirements": template.user_requirements,
        "character_name": template.character_name,
        "identity": template.identity,
        "scene_context": template.scene_context,
        "tone_style": template.tone_style,
        "visual_style": template.visual_style,
        "do_not_include": template.do_not_include,
        "notes": template.notes,
        "learned_preferences": template.learned_preferences,
        "last_learned_summary": template.last_learned_summary,
        "learning_count": template.learning_count,
        "updated_by": template.updated_by,
        "compiled_background_context": compile_background_template(template),
    }


def _merge_text(current: str | None, incoming: str | None) -> str | None:
    current = (current or "").strip()
    incoming = (incoming or "").strip()
    if not incoming:
        return current or None
    if not current:
        return incoming
    if incoming in current:
        return current
    if current in incoming:
        return incoming
    return f"{current}\n{incoming}"


async def learn_background_template_from_run(
    *,
    db_session_factory: async_sessionmaker,
    llm: LLMService,
    pipeline_run_id: str,
    input_config: dict,
    artifacts: dict,
) -> None:
    template_id = input_config.get("background_template_id")
    if not template_id:
        return
    if not artifacts.get("final_video", {}).get("final_video_path"):
        return

    async with db_session_factory() as session:
        template = await session.get(BackgroundTemplate, template_id)
        if not template:
            return

        before = _template_snapshot(template)
        prompt_plan = artifacts.get("prompt_plan", {})
        orch_plan = artifacts.get("orchestrator_plan", {})
        final_video = artifacts.get("final_video", {})
        schema = {
            "name": "background_template_learning_patch",
            "schema": {
                "type": "object",
                "properties": {
                    "tone_style": {"type": "string"},
                    "visual_style": {"type": "string"},
                    "do_not_include": {"type": "string"},
                    "notes": {"type": "string"},
                    "learned_preferences": {"type": "string"},
                    "last_learned_summary": {"type": "string"},
                },
            },
        }
        user_prompt = json.dumps(
            {
                "current_template": before,
                "input_script": input_config.get("script", ""),
                "background_context_used": input_config.get("background_context", ""),
                "orchestrator_plan": orch_plan,
                "prompt_plan": prompt_plan,
                "final_video_result": final_video,
                "rules": [
                    "Only update stable long-term preferences and constraints.",
                    "Do not modify brand_info, user_requirements, character_name, identity, or scene_context.",
                    "Skip temporary one-off plot details.",
                    "Prefer brief additive updates.",
                ],
            },
            ensure_ascii=False,
        )
        try:
            patch, _usage = await llm.generate_structured(
                system_prompt=(
                    "You update a user's reusable background template after a completed video task. "
                    "Return only safe incremental updates for long-term style, visual preferences, "
                    "constraints, and notes. Never rewrite the user's core brand or identity fields."
                ),
                user_prompt=user_prompt,
                schema=schema,
            )
        except Exception:
            patch = {}

        changed = False
        for field in LEARNABLE_FIELDS:
            if field not in patch:
                continue
            merged = _merge_text(getattr(template, field), patch.get(field))
            if merged != getattr(template, field):
                setattr(template, field, merged)
                changed = True

        if changed:
            template.learning_count = int(template.learning_count or 0) + 1
            template.updated_by = "agent"

        after = _template_snapshot(template)
        log = BackgroundTemplateLearningLog(
            template_id=template.id,
            pipeline_run_id=pipeline_run_id,
            before_snapshot=json.dumps(before, ensure_ascii=False),
            applied_patch=json.dumps(patch or {}, ensure_ascii=False),
            after_snapshot=json.dumps(after, ensure_ascii=False),
            summary=(patch or {}).get("last_learned_summary"),
        )
        session.add(log)
        await session.commit()
