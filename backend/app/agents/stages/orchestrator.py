from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agents.core.base import AgentContext, AgentResult, BaseAgent, describe_exception
from app.agents.stages.llm_diagnostics import llm_failure_label, short_error
from app.agents.stages.orchestrator_utils import (
    _as_string_or_empty,
    _build_brief_segments as _build_brief_segments,
    _coerce_duration,
    _infer_duration_from_text as _infer_duration_from_text,
    _infer_platform_from_text as _infer_platform_from_text,
    _infer_style_from_text as _infer_style_from_text,
    _normalize_platform,
    _snap_to_supported as _snap_to_supported,
    _summarize_image_asset,
)
from app.agents.stages.requirement_utils import (
    REQUIREMENT_PARSER_SYSTEM_PROMPT,
    build_requirement_prompt,
    build_requirement_schema,
    looks_like_meta_instruction as _looks_like_meta_instruction,
    merge_requirement_fields,
    needs_llm_requirement_parsing,
)
from app.core.config import settings
from app.models.material import Material
from app.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class OrchestratorState(str, Enum):
    INTAKE = "intake"
    PARSE_REQUIREMENTS = "parse_requirements"
    RESOLVE_IMAGES = "resolve_images"
    PREPROCESS_IMAGES = "preprocess_images"
    ANALYZE_IMAGES = "analyze_images"
    FINALIZE = "finalize"


class OrchestratorAgent(BaseAgent):
    """Intake / Context stage: parses intent and prepares director input context."""

    name = "orchestrator"

    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def _transition(
        self,
        context: AgentContext,
        state: OrchestratorState,
        message: str,
        visited_states: list[str],
    ) -> None:
        if not visited_states or visited_states[-1] != state.value:
            visited_states.append(state.value)
        await context.report_progress(f"{state.value}: {message}", agent_name=self.name)

    async def _parse_requirements(
        self,
        context: AgentContext,
        input_data: dict,
        visited_states: list[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raw_message = _as_string_or_empty(
            input_data.get("user_request") or input_data.get("script") or ""
        ).strip()
        image_ids = input_data.get("image_ids") or []
        usage_records: list[dict[str, Any]] = []
        llm_result: dict[str, Any] = {}

        await self._transition(
            context,
            OrchestratorState.PARSE_REQUIREMENTS,
            "解析用户需求，分离真实创作意图和生成元指令。",
            visited_states,
        )

        if needs_llm_requirement_parsing(raw_message) and getattr(self.llm, "client", None) is not None:
            try:
                llm_result, usage = await self.llm.generate_structured(
                    system_prompt=REQUIREMENT_PARSER_SYSTEM_PROMPT,
                    user_prompt=build_requirement_prompt(input_data, raw_message, len(image_ids)),
                    schema=build_requirement_schema(),
                )
                usage_records.append({
                    "provider": "qwen",
                    "model_name": getattr(getattr(self.llm, "client", None), "model", "mock"),
                    "operation": "requirement_parse",
                    **usage,
                })
            except Exception as exc:
                logger.warning(
                    "Orchestrator requirement parsing LLM call failed: %s",
                    describe_exception(exc),
                    exc_info=True,
                )
                await self._transition(
                    context,
                    OrchestratorState.PARSE_REQUIREMENTS,
                    f"{llm_failure_label(exc)}，已使用本地关键词规则解析需求。错误: {short_error(exc)}",
                    visited_states,
                )

        parsed = merge_requirement_fields(input_data, llm_result)
        await self._transition(
            context,
            OrchestratorState.PARSE_REQUIREMENTS,
            (
                f"已解析需求：平台={parsed['platform']}，时长={parsed['duration_seconds']}s，"
                f"风格={parsed['style']}，BGM={parsed['bgm_mood']}。"
            ),
            visited_states,
        )
        return parsed, usage_records

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        visited_states: list[str] = []
        await self._transition(
            context,
            OrchestratorState.INTAKE,
            "读取用户消息、会话参数和已选素材，准备进入视频调度。",
            visited_states,
        )

        parsed_requirement, requirement_usage = await self._parse_requirements(
            context, input_data, visited_states
        )
        usage_records = list(requirement_usage)

        creative_brief: str = _as_string_or_empty(parsed_requirement.get("user_intent")).strip()
        script_for_voiceover: str = _as_string_or_empty(parsed_requirement.get("explicit_script")).strip()
        if _looks_like_meta_instruction(script_for_voiceover):
            script_for_voiceover = ""
        image_ids: list[str] = parsed_requirement.get("image_ids") or []
        platform: str = _normalize_platform(parsed_requirement.get("platform", "generic"))
        duration_seconds: int = _coerce_duration(parsed_requirement.get("duration_seconds", 30), 30)
        duration_mode: str = _as_string_or_empty(parsed_requirement.get("duration_mode", "fixed")).strip() or "fixed"
        style: str = _as_string_or_empty(parsed_requirement.get("style", "commercial")).strip() or "commercial"
        bgm_mood: str = _as_string_or_empty(parsed_requirement.get("bgm_mood", "none")).strip() or "none"
        voice_id: str = _as_string_or_empty(parsed_requirement.get("voice_id", "default")).strip() or "default"
        generation_model: str = _as_string_or_empty(
            parsed_requirement.get("generation_model", "seedance1.5-pro")
        ).strip() or "seedance1.5-pro"
        no_audio = bool(parsed_requirement.get("no_audio", True))
        video_model_no_audio = bool(parsed_requirement.get("video_model_no_audio", no_audio))
        voiceover_no_audio = bool(parsed_requirement.get("voiceover_no_audio", no_audio))
        background_context: str = _as_string_or_empty(parsed_requirement.get("background_context")).strip()

        await self._transition(
            context,
            OrchestratorState.RESOLVE_IMAGES,
            f"确认 {len(image_ids)} 张图片素材，并读取文件路径与基础元数据。",
            visited_states,
        )
        image_assets = await self._resolve_image_assets(context, image_ids)
        if not image_assets:
            return AgentResult(success=False, output_data={}, error="No valid images found for the given IDs")
        image_paths = [asset["image_path"] for asset in image_assets]

        await self._transition(
            context,
            OrchestratorState.PREPROCESS_IMAGES,
            f"按 {platform} 平台画幅准备图片，后续会把这些图片直接交给视频生成节点。",
            visited_states,
        )
        image_assets = self._preprocess_image_assets_for_platform(image_assets, platform)
        image_paths = [asset["image_path"] for asset in image_assets]
        preprocessed_platform = platform

        num_shots = len(image_paths)

        schema = {
            "name": "image_context_plan",
            "schema": {
                "type": "object",
                "properties": {
                    "video_type": {"type": "string"},
                    "platform": {"type": "string"},
                    "style": {"type": "string"},
                    "image_summaries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "image_idx": {"type": "integer"},
                                "summary": {"type": "string"},
                                "key_subjects": {"type": "array", "items": {"type": "string"}},
                                "visual_role": {"type": "string"},
                                "marketing_angle": {"type": "string"},
                            },
                            "required": ["image_idx", "summary", "visual_role"],
                        },
                    },
                },
                "required": ["video_type", "platform", "style"],
            },
        }

        # Retrieve user preference memories if available
        memory_hints = ""
        if context.mem0 and context.user_id:
            try:
                memories = await context.mem0.search(
                    f"video style preference for {platform}", context.user_id, limit=3,
                )
                if memories:
                    memory_hints = "\n".join(
                        f"- {m.get('memory', '')}" for m in memories if m.get("memory")
                    )
            except Exception as exc:
                logger.warning(
                    "Orchestrator memory search failed: %s",
                    describe_exception(exc),
                    exc_info=True,
                )

        # RAG: retrieve similar historical generation plans as few-shot context
        rag_context = ""
        rag_service = getattr(context, "rag_service", None)
        if rag_service is not None and creative_brief:
            try:
                similar_plans = await rag_service.retrieve_similar(
                    requirement=creative_brief,
                    platform=platform,
                    limit=3,
                )
                if similar_plans:
                    rag_context = rag_service.format_for_prompt(similar_plans)
                    logger.debug(
                        "Orchestrator RAG: retrieved %d similar plans for query '%s'",
                        len(similar_plans),
                        creative_brief[:50],
                    )
            except Exception as exc:
                logger.warning("Orchestrator RAG retrieval failed: %s", describe_exception(exc))

        image_inventory = "\n".join(
            f"Image {idx}: {_summarize_image_asset(asset)}"
            for idx, asset in enumerate(image_assets)
        )

        prompt = (
            f"User request: {creative_brief or '(none)'}\n"
            f"Initial platform: {platform} | style: {style}\n\n"
            f"Image inventory ({num_shots} images):\n{image_inventory}\n\n"
            + (f"Background context:\n{background_context}\n\n" if background_context else "")
            + (f"User's historical preferences:\n{memory_hints}\n\n" if memory_hints else "")
            + (f"{rag_context}\n" if rag_context else "")
            + "For each image: describe visible content precisely, identify key subjects, assign visual_role "
            "(product_hero / lifestyle_scene / brand_identity / testimonial / cta_moment / supporting_detail), "
            "and note the marketing_angle. Confirm or correct platform and style. "
            "Do NOT write narration or shot sequences."
        )

        try:
            await self._transition(
                context,
                OrchestratorState.ANALYZE_IMAGES,
                "多模态理解图片内容、营销角色，确认平台和风格。",
                visited_states,
            )
            llm_output, usage = await self.llm.generate_structured(
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                user_prompt=prompt,
                schema=schema,
                image_paths=image_paths,
            )
            if await context.is_cancelled():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

            platform = _normalize_platform(llm_output.get("platform") or platform, platform)
            style = _as_string_or_empty(llm_output.get("style")).strip() or style
            if platform != preprocessed_platform:
                image_assets = self._preprocess_image_assets_for_platform(image_assets, platform)
                image_paths = [asset["image_path"] for asset in image_assets]
                preprocessed_platform = platform

            image_summaries = llm_output.get("image_summaries")
            if not isinstance(image_summaries, list):
                image_summaries = []
                await self._transition(
                    context,
                    OrchestratorState.ANALYZE_IMAGES,
                    "Qwen 返回缺少 image_summaries，已使用本地图片摘要兜底。",
                    visited_states,
                )
            image_summary_map = {}
            for item in image_summaries:
                if not isinstance(item, dict):
                    continue
                try:
                    image_idx = int(item.get("image_idx", item.get("shot_idx", -1)))
                except (TypeError, ValueError):
                    continue
                if 0 <= image_idx < num_shots:
                    image_summary_map[image_idx] = item
            missing_summary_count = len([idx for idx in range(num_shots) if idx not in image_summary_map])
            if missing_summary_count and image_summary_map:
                await self._transition(
                    context,
                    OrchestratorState.ANALYZE_IMAGES,
                    f"Qwen 返回缺少 {missing_summary_count} 张图片摘要，缺失图片已使用本地摘要兜底。",
                    visited_states,
                )
            video_type = llm_output.get("video_type", self._detect_video_type(creative_brief or script_for_voiceover))
            usage_records.append({
                "provider": "qwen",
                "model_name": getattr(self.llm, "client", None).model if getattr(self.llm, "client", None) else "mock",
                "operation": "orchestrate",
                **usage,
            })
        except Exception as exc:
            logger.warning(
                "Orchestrator image context LLM call failed: %s",
                describe_exception(exc),
                exc_info=True,
            )
            video_type = self._detect_video_type(creative_brief or script_for_voiceover)
            image_summary_map = {}
            await self._transition(
                context,
                OrchestratorState.ANALYZE_IMAGES,
                f"{llm_failure_label(exc)}，已使用本地图片摘要兜底。错误: {short_error(exc)}",
                visited_states,
            )

        source_images = []
        for idx, asset in enumerate(image_assets):
            summary_item = image_summary_map.get(idx, {})
            image_content = _as_string_or_empty(summary_item.get("summary")).strip() or _summarize_image_asset(asset)
            source_images.append({
                **asset,
                "image_idx": idx,
                "shot_idx": idx,  # backward-compat alias
                "image_content": image_content,
                "visual_role": _as_string_or_empty(summary_item.get("visual_role")).strip() or "素材展示",
                "key_subjects": summary_item.get("key_subjects", []),
                "marketing_angle": _as_string_or_empty(summary_item.get("marketing_angle")).strip(),
            })

        logger.info(
            f"Orchestrator: platform={platform}, style={style}, video_type={video_type}, "
            f"images={num_shots}, target={duration_seconds}s ({duration_mode})"
        )

        if context.mem0 and context.user_id:
            try:
                await context.mem0.add_explicit(
                    f"用户在{platform}平台做{video_type}类型视频，{num_shots}张素材，风格{style}",
                    context.user_id,
                    metadata={"type": "pipeline_context", "platform": platform},
                )
            except Exception as exc:
                logger.warning(
                    "Orchestrator memory add failed: %s",
                    describe_exception(exc),
                    exc_info=True,
                )

        await self._transition(
            context,
            OrchestratorState.FINALIZE,
            "素材感知完成：已整理图片内容、营销角色和上下文，交给导演 Agent 创作分镜方案。",
            visited_states,
        )

        output = {
            "video_type": video_type,
            "platform": platform,
            "style": style,
            "duration_seconds": duration_seconds,
            "target_duration_seconds": duration_seconds,
            "duration_mode": duration_mode,
            "bgm_mood": bgm_mood,
            "voice_id": voice_id,
            "generation_model": generation_model,
            "no_audio": no_audio,
            "video_model_no_audio": video_model_no_audio,
            "voiceover_no_audio": voiceover_no_audio,
            "creative_brief": creative_brief,
            "explicit_script": script_for_voiceover,
            "user_request": creative_brief,
            "background_context": background_context,
            "source_images": source_images,
            "image_context": source_images,  # backward-compat alias
            "voice_config": {"voice_id": voice_id, "speed": 1.0},
            "intent": {
                "video_type": video_type,
                "platform": platform,
                "style": style,
                "target_duration_seconds": duration_seconds,
                "duration_mode": duration_mode,
                "bgm_mood": bgm_mood,
                "voice_id": voice_id,
                "generation_model": generation_model,
                "no_audio": no_audio,
                "video_model_no_audio": video_model_no_audio,
                "voiceover_no_audio": voiceover_no_audio,
            },
            "state_machine": {
                "completed_states": visited_states,
                "current_state": OrchestratorState.FINALIZE.value,
            },
        }

        return AgentResult(success=True, output_data=output, usage_records=usage_records)

    def _preprocess_image_assets_for_platform(
        self,
        image_assets: list[dict[str, Any]],
        platform: str,
    ) -> list[dict[str, Any]]:
        target_res = settings.PLATFORM_RESOLUTIONS.get(platform)
        if not target_res:
            return image_assets

        from app.services.media_utils import preprocess_image_for_platform

        processed_assets = []
        for asset in image_assets:
            source_path = asset.get("original_image_path") or asset["image_path"]
            try:
                processed_path = preprocess_image_for_platform(
                    source_path, target_res[0], target_res[1], settings.GENERATED_DIR
                )
            except Exception as exc:
                logger.warning(
                    "Image preprocess failed for %s: %s, using original",
                    source_path,
                    describe_exception(exc),
                    exc_info=True,
                )
                processed_path = source_path
            processed_asset = dict(asset)
            processed_asset["original_image_path"] = source_path
            processed_asset["image_path"] = processed_path
            processed_assets.append(processed_asset)
        return processed_assets

    async def _resolve_image_assets(self, context: AgentContext, image_ids: list[str]) -> list[dict[str, Any]]:
        async with context.db_session_factory() as session:
            result = await session.execute(
                select(Material).where(Material.id.in_(image_ids))
            )
            materials = result.scalars().all()
            id_to_material = {m.id: m for m in materials}
            root = Path(settings.MATERIALS_ROOT)
            assets = []
            for img_id in image_ids:
                m = id_to_material.get(img_id)
                if m and m.file_path:
                    assets.append({
                        "image_id": img_id,
                        "image_path": str((root / m.file_path).resolve()),
                        "filename": m.filename,
                        "width": m.width,
                        "height": m.height,
                        "media_type": m.media_type,
                        "tags": m.tags,
                    })
                elif m:
                    logger.warning(f"Material {img_id} has no file_path, skipped")
            return assets

    async def _resolve_images(self, context: AgentContext, image_ids: list[str]) -> list[str]:
        assets = await self._resolve_image_assets(context, image_ids)
        return [asset["image_path"] for asset in assets]

    def _detect_video_type(self, script: str) -> str:
        if any(w in script for w in ["功能", "特点", "产品", "介绍", "参数"]):
            return "product_demo"
        if any(w in script for w in ["品牌", "故事", "理念", "创始"]):
            return "brand_story"
        if any(w in script for w in ["优惠", "折扣", "促销", "限时", "抢购"]):
            return "promotion"
        return "commercial"
