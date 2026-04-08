from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")

from sqlalchemy import select

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.config import settings
from app.models.auto_chat import AutoSessionMaterialSelection
from app.models.material import Material
from app.models.pipeline import PipelineRun
from app.models.video_upload import VideoUpload
from app.prompts import ORCHESTRATOR_SYSTEM_PROMPT, VIDEO_REPLICATION_SYSTEM_PROMPT
from app.services.keyframe_extractor import KeyframeExtractor
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


def _snap_to_supported(value: float, supported: list[int]) -> int:
    """Round a duration to the nearest model-supported value."""
    return min(supported, key=lambda s: abs(s - value))


def _allocate_shot_durations(target_total: int, num_shots: int, supported: list[int]) -> list[int]:
    """Allocate per-shot durations from *supported* values so the sum equals *target_total*.

    Strategy: start by assigning the supported duration closest to the even split,
    then greedily adjust individual shots to close the gap to the target.
    """
    if not supported or num_shots == 0:
        return []

    min_d, max_d = min(supported), max(supported)
    # Clamp target to feasible range
    clamped_total = max(num_shots * min_d, min(num_shots * max_d, target_total))

    even = clamped_total / num_shots
    durations = [_snap_to_supported(even, supported)] * num_shots
    current_sum = sum(durations)

    # Greedily adjust to match clamped_total
    sorted_sup = sorted(supported)
    for _ in range(num_shots * len(supported)):
        diff = clamped_total - current_sum
        if diff == 0:
            break
        if diff > 0:
            # Need more time — try to increase the first shot that can go up
            for i in range(num_shots):
                candidates = [s for s in sorted_sup if s > durations[i]]
                if candidates:
                    step = candidates[0] - durations[i]
                    if step <= diff:
                        current_sum += step
                        durations[i] = candidates[0]
                        break
            else:
                break
        else:
            # Need less time — try to decrease the first shot that can go down
            for i in range(num_shots):
                candidates = [s for s in sorted_sup if s < durations[i]]
                if candidates:
                    step = durations[i] - candidates[-1]
                    if step <= -diff:
                        current_sum -= step
                        durations[i] = candidates[-1]
                        break
            else:
                break

    return durations


def _is_existing_image_file(file_path: str | None) -> bool:
    if not file_path:
        return False
    path = Path(file_path)
    return path.exists() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _as_replication_mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_string_or_empty(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_numeric_list(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    normalized: list[float] = []
    for item in value:
        try:
            normalized.append(float(item))
        except (TypeError, ValueError):
            continue
    return normalized


def _build_narration_script_from_shots(shots: list[dict], fallback_script: str = "") -> str:
    """Compose a narration script from orchestrated shot segments."""
    if not isinstance(shots, list):
        return fallback_script

    segments: list[str] = []
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        text = _as_string_or_empty(shot.get("script_segment")).strip()
        if text:
            segments.append(text)

    if segments:
        return "\n".join(segments)
    return fallback_script


async def _run_cancellable(
    coro: "asyncio.Coroutine[None, None, _T]",
    context: "AgentContext",
    poll_interval: float = 2.0,
) -> "_T":
    """Wrap a long-running coroutine so it is interrupted if the pipeline is cancelled.

    Periodically checks context.is_cancelled() while waiting.  If cancellation
    is detected the underlying task is cancelled and RuntimeError is raised so
    the caller's except-block can clean up normally.
    """
    task: asyncio.Task = asyncio.create_task(coro)
    try:
        while not task.done():
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=poll_interval)
            except asyncio.TimeoutError:
                if await context.is_cancelled():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise RuntimeError("Pipeline cancelled")
        return task.result()
    except asyncio.CancelledError:
        task.cancel()
        raise


class OrchestratorAgent(BaseAgent):
    """System entry point: parses user intent, resolves images, decomposes script into shots."""

    name = "orchestrator"
    video_replication_skill_name = "video_replication"

    def __init__(self, llm_service: LLMService, keyframe_extractor: KeyframeExtractor | None = None):
        self.llm = llm_service
        self.keyframe_extractor = keyframe_extractor

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        reference_video_id = input_data.get("reference_video_id")
        if reference_video_id:
            intent = await self._classify_video_intent(input_data)
            if intent == "replication":
                return await self._execute_video_replication_skill(context, input_data, reference_video_id)
            return await self._execute_video_analysis(context, input_data, reference_video_id)

        script: str = input_data["script"]
        image_ids: list[str] = input_data["image_ids"]
        platform: str = input_data.get("platform", "generic")
        duration_seconds: int = input_data.get("duration_seconds", 30)
        duration_mode: str = input_data.get("duration_mode", "fixed")
        style: str = input_data.get("style", "commercial")
        voice_id: str = input_data.get("voice_id", "default")
        background_context: str = input_data.get("background_context", "")
        selected_skill: str | None = None

        supported_durations: list[int] = settings.SEEDANCE_SUPPORTED_DURATIONS

        # Resolve image paths from DB
        image_paths = await self._resolve_images(context, image_ids)
        if not image_paths:
            return AgentResult(success=False, output_data={}, error="No valid images found for the given IDs")

        # Preprocess images for platform resolution
        target_res = settings.PLATFORM_RESOLUTIONS.get(platform)
        if target_res:
            from app.services.media_utils import preprocess_image_for_platform
            processed = []
            for p in image_paths:
                try:
                    processed.append(
                        preprocess_image_for_platform(p, target_res[0], target_res[1], settings.GENERATED_DIR)
                    )
                except Exception as exc:
                    logger.warning(f"Image preprocess failed for {p}: {exc}, using original")
                    processed.append(p)
            image_paths = processed

        num_shots = len(image_paths)
        min_d, max_d = min(supported_durations), max(supported_durations)

        # ── Feasibility check (fixed mode only) ──
        if duration_mode == "fixed":
            min_total = num_shots * min_d
            max_total = num_shots * max_d
            if duration_seconds < min_total:
                return AgentResult(
                    success=False, output_data={},
                    error=(
                        f"目标时长 {duration_seconds}s 不可行：{num_shots} 张素材至少需要 {min_total}s"
                        f"（每个镜头最短 {min_d}s）。"
                        f"建议：减少素材数量到 {duration_seconds // min_d} 张以内，"
                        f"或将目标时长增加到 {min_total}s 以上。"
                    ),
                )
            if duration_seconds > max_total:
                return AgentResult(
                    success=False, output_data={},
                    error=(
                        f"目标时长 {duration_seconds}s 不可行：{num_shots} 张素材最多支持 {max_total}s"
                        f"（每个镜头最长 {max_d}s）。"
                        f"建议：增加素材数量到至少 {math.ceil(duration_seconds / max_d)} 张，"
                        f"或将目标时长减少到 {max_total}s 以内。"
                    ),
                )

        # ── Script length vs duration check (fixed mode) ──
        if duration_mode == "fixed":
            # Estimate TTS duration: ~4 Chinese characters per second
            cn_chars = sum(1 for c in script if '\u4e00' <= c <= '\u9fff')
            other_chars = len(script) - cn_chars
            estimated_audio_s = cn_chars / 4.0 + other_chars / 8.0
            if estimated_audio_s > duration_seconds * 1.8:
                max_chars = int(duration_seconds * 4)
                return AgentResult(
                    success=False, output_data={},
                    error=(
                        f"脚本约 {len(script)} 字，预计口播需要约 {int(estimated_audio_s)}s，"
                        f"但目标视频仅 {duration_seconds}s，差距过大无法对齐。\n"
                        f"建议：缩短脚本到约 {max_chars} 字以内，"
                        f"或将视频时长增加到 {int(estimated_audio_s) + 1}s 以上。"
                    ),
                )

        # ── Determine per-shot durations ──
        # Decision order: user target duration → model supported durations → shot decomposition
        if duration_mode == "fixed":
            # Allocate supported durations that sum to user's target
            per_shot_durations = _allocate_shot_durations(duration_seconds, num_shots, supported_durations)
        else:
            # Auto mode: use default supported duration, LLM may adjust later
            per_shot_durations = [settings.SEEDANCE_DURATION] * num_shots

        schema = {
            "name": "orchestrator_plan",
            "schema": {
                "type": "object",
                "properties": {
                    "video_type": {"type": "string"},
                    "voice_speed": {"type": "number"},
                    "narration_script": {"type": "string"},
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_idx": {"type": "integer"},
                                "script_segment": {"type": "string"},
                                "duration_seconds": {"type": "number"},
                            },
                            "required": ["shot_idx", "script_segment", "duration_seconds"],
                        },
                    },
                },
                "required": ["video_type", "voice_speed", "narration_script", "shots"],
            },
        }

        supported_str = "/".join(f"{d}s" for d in sorted(supported_durations))
        if duration_mode == "fixed":
            duration_instruction = (
                f"Total video duration MUST be exactly {duration_seconds}s. "
                f"Each shot duration MUST be one of: {supported_str}. "
                f"Pre-allocated durations: {per_shot_durations}. "
                f"You may rearrange which shots get which duration but the total must remain {duration_seconds}s."
            )
        else:
            duration_instruction = (
                f"You may freely decide each shot's duration. "
                f"Each shot duration MUST be one of: {supported_str}. "
                f"Choose durations that best fit the narrative rhythm."
            )

        prompt = (
            f"Script:\n{script}\n\n"
            f"Platform: {platform}\nStyle: {style}\n"
            + (f"Background context:\n{background_context}\n\n" if background_context else "")
            +
            f"Duration constraint: {duration_instruction}\n"
            f"Please first rewrite the user's request into a natural, polished narration script suitable for TTS voiceover. "
            f"The narration must keep the user's real goal, remove meta-instructions, and sound like final spoken copy. "
            f"Then split that narration into exactly {num_shots} shots."
        )

        try:
            llm_output, usage = await self.llm.generate_structured(
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                user_prompt=prompt,
                schema=schema, # 要求返回的json结构
                image_paths=image_paths,
            )
            if await context.is_cancelled():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")
            planned_shots = llm_output.get("shots", [])
            if len(planned_shots) != num_shots:
                raise ValueError("Model returned unexpected shot count")
            shots = []
            for i, (image_path, shot) in enumerate(zip(image_paths, planned_shots)):
                raw_dur = float(shot.get("duration_seconds", per_shot_durations[i]))
                if duration_mode == "fixed":
                    # Always clamp to pre-allocated durations to guarantee total
                    clamped_dur = per_shot_durations[i]
                else:
                    # Auto mode: snap LLM's choice to nearest supported value
                    clamped_dur = _snap_to_supported(raw_dur, supported_durations)
                shots.append({
                    "shot_idx": i,
                    "image_path": image_path,
                    "script_segment": shot["script_segment"],
                    "duration_seconds": clamped_dur,
                })
            video_type = llm_output.get("video_type", self._detect_video_type(script))
            voice_speed = float(llm_output.get("voice_speed", 1.0))
            narration_script = _as_string_or_empty(llm_output.get("narration_script")).strip()
            usage_records = [{
                "provider": "qwen",
                "model_name": getattr(self.llm, "client", None).model if getattr(self.llm, "client", None) else "mock",
                "operation": "orchestrate",
                **usage,
            }]
        except Exception:
            video_type = self._detect_video_type(script)
            sentences = self._split_script(script, num_shots)
            shots = []
            for i, (image_path, sentence) in enumerate(zip(image_paths, sentences)):
                shots.append({
                    "shot_idx": i,
                    "image_path": image_path,
                    "script_segment": sentence,
                    "duration_seconds": per_shot_durations[i],
                })
            voice_speed = 1.0
            usage_records = []
            narration_script = ""

        actual_total = sum(s["duration_seconds"] for s in shots)
        narration_script = narration_script or _build_narration_script_from_shots(shots, script)
        logger.info(
            f"Orchestrator: mode={duration_mode}, target={duration_seconds}s, "
            f"actual_total={actual_total}s, shots={[s['duration_seconds'] for s in shots]}"
        )

        output = {
            "shots": shots,
            "video_type": video_type,
            "platform": platform,
            "duration_seconds": int(actual_total),
            "style": style,
            "voice_config": {
                "voice_id": voice_id,
                "speed": voice_speed,
            },
            "script": narration_script,
            "user_script": script,
            "background_context": background_context,
            "selected_skill": selected_skill,
        }

        return AgentResult(success=True, output_data=output, usage_records=usage_records)

    async def _classify_video_intent(self, input_data: dict) -> str:
        """Classify user intent as 'replication' or 'analysis'.

        Returns 'replication' if the user wants to create a new video based on the reference,
        or 'analysis' if they want to understand/describe the video content.
        """
        script = _as_string_or_empty(input_data.get("script"))
        adjustment_feedback = _as_string_or_empty(input_data.get("adjustment_feedback"))

        # Adjustment feedback always means the user is refining a replication plan
        if adjustment_feedback.strip():
            return "replication"

        # No text → user just uploaded a video, default to analysis
        if not script.strip():
            return "analysis"

        normalized = script.lower().replace(" ", "")

        # Fast heuristic (highest priority): explicit analysis keywords → skip LLM call
        analysis_keywords = [
            "解析", "分析", "报告", "描述", "总结", "概述", "拆解",
            "理解", "讲解", "介绍", "是什么", "有哪些", "多少个",
            "什么风格", "什么内容", "讲的什么", "说的什么",
            "analyze", "describe", "summarize", "explain", "report",
        ]
        for kw in analysis_keywords:
            if kw in normalized:
                return "analysis"

        # Fast heuristic: explicit replication keywords
        replication_keywords = ["复刻", "翻拍", "模仿", "仿照", "还原", "同款", "replicat", "remake", "recreate"]
        for kw in replication_keywords:
            if kw in normalized:
                return "replication"

        # LLM classification for ambiguous requests (e.g. "按这个视频的节奏做一个")
        try:
            schema = {
                "name": "video_intent",
                "schema": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string", "enum": ["replication", "analysis"]},
                    },
                    "required": ["intent"],
                },
            }
            result, _ = await self.llm.generate_structured(
                system_prompt=(
                    "You are classifying user intent for a video production tool.\n"
                    "Classify as:\n"
                    "- 'replication': user explicitly wants to CREATE or MAKE a new video based on or inspired by the reference\n"
                    "- 'analysis': user wants to understand, describe, summarize, or get information from the video\n"
                    "When uncertain, prefer 'analysis' — the user can always follow up with a replication request."
                ),
                user_prompt=f"User request: {script}",
                schema=schema,
            )
            return result.get("intent", "analysis")
        except Exception:
            logger.warning("Video intent classification failed, defaulting to 'analysis'")
            return "analysis"

    async def _execute_video_analysis(
        self, context: AgentContext, input_data: dict, reference_video_id: str
    ) -> AgentResult:
        """Analyze the reference video and return a text report without triggering video generation."""
        video_path = await self._resolve_video(context, reference_video_id)
        if not video_path:
            return AgentResult(
                success=False, output_data={},
                error=f"无法找到参考视频（ID: {reference_video_id}）",
            )

        script = _as_string_or_empty(input_data.get("script"))

        schema = {
            "name": "video_analysis",
            "schema": {
                "type": "object",
                "properties": {
                    "overview": {
                        "type": "string",
                        "description": "视频整体概述：主题、核心信息、目标受众、叙事结构（hook型/故事型/展示型/种草型）、情感基调。2-3段连贯叙述。",
                    },
                    "shot_plan": {
                        "type": "string",
                        "description": "镜头方案分析：镜头数量与时长节奏、景别运用（全景/中景/特写比例）、运镜方式（推/拉/摇/跟/固定）、转场技巧、剪辑节奏特征。",
                    },
                    "visual_style": {
                        "type": "string",
                        "description": "视觉风格：色调与色彩心理、光影风格、构图法则运用、主体呈现方式、整体视觉调性。",
                    },
                    "audio_voiceover": {
                        "type": "string",
                        "description": "配音与口播：是否有人声、声音风格（性别/年龄感/情绪）、语速节奏、台词结构（hook/痛点/解决方案/CTA）、关键口播文案摘录。",
                    },
                    "music_design": {
                        "type": "string",
                        "description": "音乐设计：BGM风格与情绪（upbeat/calm/cinematic/emotional）、音乐与画面的配合节奏点、音量层次（配音vs背景音乐关系）、整体听觉体验。",
                    },
                    "marketing_strategy": {
                        "type": "string",
                        "description": "营销策略解读：开头hook方式、卖点传达逻辑、受众触达策略（社会认同/稀缺感/利益前置）、结尾行动号召形式。",
                    },
                    "replication_suggestions": {
                        "type": "string",
                        "description": "复刻建议：如需复刻此视频，需重点保留哪些核心元素、哪些可以根据品牌调整、预估制作难点。",
                    },
                },
                "required": ["overview", "shot_plan", "visual_style", "audio_voiceover", "music_design", "marketing_strategy", "replication_suggestions"],
            },
        }
        from app.prompts import VIDEO_ANALYSIS_SYSTEM_PROMPT
        system_prompt = VIDEO_ANALYSIS_SYSTEM_PROMPT

        has_explicit_question = bool(script.strip())
        user_prompt = (
            f"请对这段视频进行全面专业的多维度解析报告。"
            + (f"\n\n用户的具体问题/需求：{script}\n\n请在回答用户问题的同时，也完整输出各维度分析。" if has_explicit_question else "")
        )

        try:
            result, usage = await _run_cancellable(
                self.llm.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    video_paths=[video_path],
                ),
                context,
            )

            # Assemble the structured report into a readable markdown-style text
            sections = [
                ("视频概述", result.get("overview")),
                ("镜头方案", result.get("shot_plan")),
                ("视觉风格", result.get("visual_style")),
                ("配音与口播", result.get("audio_voiceover")),
                ("音乐设计", result.get("music_design")),
                ("营销策略", result.get("marketing_strategy")),
                ("复刻建议", result.get("replication_suggestions")),
            ]
            parts = []
            for title, content in sections:
                if content and str(content).strip():
                    parts.append(f"【{title}】\n{str(content).strip()}")
            analysis_text = "\n\n".join(parts) if parts else (result.get("raw_response") or "视频分析完成。")

            usage_records = [{
                "provider": "qwen",
                "model_name": getattr(self.llm, "client", None).model if getattr(self.llm, "client", None) else "mock",
                "operation": "video_analysis",
                **usage,
            }]

            return AgentResult(
                success=True,
                output_data={
                    "analysis_only": True,
                    "analysis_report": analysis_text,
                },
                usage_records=usage_records,
            )
        except Exception as e:
            if "cancelled" in str(e).lower():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")
            logger.error(f"Video analysis failed: {e}", exc_info=True)
            return AgentResult(success=False, output_data={}, error=f"视频分析失败: {e!r}")

    def _select_requested_skill(self, input_data: dict) -> str | None:
        reference_video_id = input_data.get("reference_video_id")
        if not reference_video_id:
            return None

        script = _as_string_or_empty(input_data.get("script"))
        adjustment_feedback = _as_string_or_empty(input_data.get("adjustment_feedback"))
        if self._should_invoke_video_replication_skill(script=script, adjustment_feedback=adjustment_feedback):
            return self.video_replication_skill_name

        return None

    def _should_invoke_video_replication_skill(self, *, script: str, adjustment_feedback: str = "") -> bool:
        if adjustment_feedback.strip():
            return True

        normalized = self._normalize_replication_direction(script)
        if not normalized:
            return False

        replication_patterns = [
            r"复刻",
            r"翻拍",
            r"模仿",
            r"仿照",
            r"照着.*做",
            r"按着?.*做",
            r"还原",
            r"同款",
            r"类似.*视频",
            r"像.*视频.*一样",
            r"参考.*视频.*做",
            r"参考.*风格.*做",
            r"replicat",
            r"remake",
            r"recreate",
        ]
        return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in replication_patterns)

    async def _execute_video_replication_skill(
        self,
        context: AgentContext,
        input_data: dict,
        reference_video_id: str,
    ) -> AgentResult:
        return await self._execute_replication(context, input_data, reference_video_id)

    async def _resolve_images(self, context: AgentContext, image_ids: list[str]) -> list[str]:
        async with context.db_session_factory() as session:
            result = await session.execute(
                select(Material).where(Material.id.in_(image_ids))
            )
            materials = result.scalars().all()
            # Preserve the order of image_ids (SQL IN doesn't guarantee order)
            id_to_material = {m.id: m for m in materials}
            root = Path(settings.MATERIALS_ROOT)
            paths = []
            for img_id in image_ids:
                m = id_to_material.get(img_id)
                if m and m.file_path:
                    paths.append(str((root / m.file_path).resolve()))
                elif m:
                    logger.warning(f"Material {img_id} has no file_path, skipped")
            return paths

    def _detect_video_type(self, script: str) -> str:
        if any(w in script for w in ["功能", "特点", "产品", "介绍", "参数"]):
            return "product_demo"
        if any(w in script for w in ["品牌", "故事", "理念", "创始"]):
            return "brand_story"
        if any(w in script for w in ["优惠", "折扣", "促销", "限时", "抢购"]):
            return "promotion"
        return "commercial"

    def _split_script(self, script: str, num_parts: int) -> list[str]:
        """Split script into roughly equal segments by sentence boundaries."""
        delimiters = ["。", "！", "？", "；", "\n"]
        sentences = []
        current = ""
        for char in script:
            current += char
            if char in delimiters and current.strip():
                sentences.append(current.strip())
                current = ""
        if current.strip():
            sentences.append(current.strip())

        if not sentences:
            sentences = [script]

        # Distribute sentences across parts
        if len(sentences) <= num_parts:
            # Pad with empty or duplicate last
            result = sentences[:]
            while len(result) < num_parts:
                result.append(sentences[-1])
            return result

        # Merge sentences into num_parts groups
        per_part = math.ceil(len(sentences) / num_parts)
        result = []
        for i in range(0, len(sentences), per_part):
            group = "".join(sentences[i:i + per_part])
            result.append(group)

        # Trim to num_parts
        while len(result) > num_parts:
            result[-2] += result[-1]
            result.pop()

        return result

    # ── Video Replication Mode ──

    async def _execute_replication(
        self, context: AgentContext, input_data: dict, reference_video_id: str
    ) -> AgentResult:
        """Analyze a reference video and produce a replication plan for user confirmation."""

        # Find the running AgentExecution for progress reporting
        exec_id_for_progress: str | None = None
        try:
            from sqlalchemy import select as sa_select
            async with context.db_session_factory() as session:
                from app.models.pipeline import AgentExecution as AE
                result = await session.execute(
                    sa_select(AE).where(
                        AE.pipeline_run_id == context.pipeline_run_id,
                        AE.agent_name == self.name,
                        AE.status == "running",
                    ).order_by(AE.created_at.desc()).limit(1)
                )
                row = result.scalar_one_or_none()
                if row:
                    exec_id_for_progress = row.id
        except Exception:
            pass

        async def update_progress(msg: str):
            if exec_id_for_progress:
                await context.report_progress(exec_id_for_progress, msg)

        video_path = await self._resolve_video(context, reference_video_id)
        if not video_path:
            return AgentResult(
                success=False, output_data={},
                error=f"无法找到参考视频（ID: {reference_video_id}）",
            )

        if self.keyframe_extractor is None:
            return AgentResult(
                success=False, output_data={},
                error="关键帧提取服务未配置",
            )

        platform = input_data.get("platform", "generic")
        style = input_data.get("style", "commercial")
        script = input_data.get("script", "")
        adjustment_feedback = input_data.get("adjustment_feedback", "")
        voice_id = input_data.get("voice_id", "default")
        background_context = input_data.get("background_context", "")

        await update_progress("正在准备分析参考视频...")

        # Output directory for extracted keyframes
        keyframe_dir = os.path.join(
            settings.GENERATED_DIR, f"{context.trace_id}_keyframes"
        )

        # Define the extract_keyframes tool for the LLM
        extract_keyframes_tool = {
            "type": "function",
            "function": {
                "name": "extract_keyframes",
                "description": (
                    "从参考视频中提取关键帧图片，用于分析画面构图、色调和主体。"
                    "可多次调用以使用不同策略获取更全面的视觉信息。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategy": {
                            "type": "string",
                            "enum": ["scene_change", "uniform", "interval"],
                            "description": (
                                "scene_change: 检测镜头切换边界；"
                                "uniform: 均匀抽取帧；"
                                "interval: 每N秒抽取一帧"
                            ),
                        },
                        "max_frames": {
                            "type": "integer",
                            "description": "最多提取的帧数（1-20）",
                        },
                    },
                    "required": ["strategy", "max_frames"],
                },
            },
        }

        # Tool executor callback
        _keyframe_call_count = [0]
        extracted_frames: list[dict] = []

        async def tool_executor(
            tool_name: str, tool_args: dict
        ) -> tuple[str, list[str]]:
            if await context.is_cancelled():
                raise RuntimeError("Pipeline cancelled")
            if tool_name != "extract_keyframes":
                return f"Unknown tool: {tool_name}", []

            _keyframe_call_count[0] += 1
            strategy = tool_args.get("strategy", "scene_change")
            max_frames = min(tool_args.get("max_frames", 10), settings.KEYFRAME_MAX_EXTRACT)

            await update_progress(f"正在提取视频关键帧（策略：{strategy}）...")

            frames = await self.keyframe_extractor.extract(
                video_path,
                strategy=strategy,
                max_frames=max_frames,
                output_dir=keyframe_dir,
            )

            if not frames:
                return "未能提取到关键帧，请尝试其他策略。", []

            await update_progress(f"已提取 {len(frames)} 个关键帧，正在分析画面构图与镜头结构...")
            existing_paths = {frame["frame_path"] for frame in extracted_frames}
            for frame in frames:
                if frame.get("frame_path") not in existing_paths:
                    extracted_frames.append(frame)
                    existing_paths.add(frame.get("frame_path"))

            # Build text summary and collect image paths
            text_parts = [f"成功提取 {len(frames)} 个关键帧："]
            image_paths = []
            for f in frames:
                text_parts.append(
                    f"  帧 {f['frame_index']}: 时间点 {f['timestamp_seconds']}s -> {f['frame_path']}"
                )
                if os.path.exists(f["frame_path"]):
                    image_paths.append(f["frame_path"])

            return "\n".join(text_parts), image_paths

        await update_progress("正在识别镜头结构与转场方式，生成执行方案...")

        # Response schema for the replication plan
        replication_schema = {
            "name": "replication_plan",
            "schema": {
                "type": "object",
                "properties": {
                    "analysis_report": {
                        "type": "string",
                        "description": "对视频内容的自然语言分析报告，介绍视频讲了什么、核心叙事逻辑、情感基调、观看体验等。不要重复罗列风格/音频/镜头字段，而是以连贯的段落描述视频整体。"
                    },
                    "video_summary": {"type": "string"},
                    "overall_style": {"type": "string"},
                    "color_palette": {"type": "string"},
                    "pacing": {"type": "string"},
                    "audio_design": {
                        "type": "object",
                        "properties": {
                            "voice_style": {"type": "string"},
                            "voice_speed": {"type": "number"},
                            "voice_tone": {"type": "string"},
                            "narration_notes": {"type": "string"},
                        },
                        "required": ["voice_style", "voice_speed", "voice_tone"],
                    },
                    "music_design": {
                        "type": "object",
                        "properties": {
                            "bgm_mood": {"type": "string"},
                            "bgm_style": {"type": "string"},
                            "volume_level": {"type": "string"},
                            "music_notes": {"type": "string"},
                        },
                        "required": ["bgm_mood", "bgm_style"],
                    },
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_idx": {"type": "integer"},
                                "description": {"type": "string"},
                                "visual_design": {"type": "string"},
                                "reference_frame_path": {"type": "string"},
                                "timestamp_range": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                },
                                "camera_movement": {"type": "string"},
                                "color_tone": {"type": "string"},
                                "subjects": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "suggested_duration_seconds": {"type": "integer"},
                            },
                            "required": [
                                "shot_idx", "description", "reference_frame_path",
                                "camera_movement", "color_tone", "subjects",
                                "suggested_duration_seconds",
                            ],
                        },
                    },
                },
                "required": ["analysis_report", "video_summary", "overall_style", "color_palette", "pacing", "audio_design", "music_design", "shots"],
            },
        }

        user_prompt = self._build_replication_user_prompt(
            video_path=video_path,
            platform=platform,
            style=style,
            script=script,
            background_context=background_context,
            adjustment_feedback=adjustment_feedback,
        )

        await update_progress("正在生成完整复刻方案（含音频、音乐、镜头设计）...")

        try:
            async def _call_replication_llm(include_video: bool):
                kwargs = {}
                if include_video:
                    kwargs["video_paths"] = [video_path]
                return await self.llm.generate_with_tools(
                    system_prompt=VIDEO_REPLICATION_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    schema=replication_schema,
                    tools=[extract_keyframes_tool],
                    tool_executor=tool_executor,
                    **kwargs,
                )

            analysis_mode = "video+keyframes"
            try:
                replication_plan, tool_call_log, usage = await _run_cancellable(
                    _call_replication_llm(include_video=True), context
                )
            except RuntimeError as exc:
                if "cancelled" in str(exc).lower():
                    raise
                raise
            except Exception as direct_video_error:
                logger.warning(
                    "Direct video input failed for replication analysis, falling back to keyframes-only mode: %r",
                    direct_video_error,
                    exc_info=True,
                )
                await update_progress("整段视频直连模型失败，正在回退到关键帧增强解析...")
                replication_plan, tool_call_log, usage = await _run_cancellable(
                    _call_replication_llm(include_video=False), context
                )
                analysis_mode = "keyframes_only_fallback"

            replication_plan = self._sanitize_replication_plan(replication_plan)

            if not self._has_substantive_replication_plan(replication_plan):
                await update_progress("初次结构化结果不完整，正在根据关键帧重新整理复刻方案...")
                replication_plan = await self._repair_replication_plan(
                    context=context,
                    user_prompt=user_prompt,
                    extracted_frames=extracted_frames,
                    analysis_mode=analysis_mode,
                    current_plan=replication_plan,
                    tool_call_log=tool_call_log,
                )

            if not extracted_frames:
                await update_progress("模型未主动调用关键帧工具，正在补充提取参考帧...")
                extracted_frames = await self.keyframe_extractor.extract(
                    video_path,
                    strategy="uniform",
                    max_frames=min(8, settings.KEYFRAME_MAX_EXTRACT),
                    output_dir=keyframe_dir,
                )

            replication_plan["shots"] = self._normalize_replication_shots(
                replication_plan.get("shots", []),
                extracted_frames,
            )

            session_materials = await self._get_session_materials(context)
            if session_materials:
                await update_progress("正在从素材仓库中为每个镜头分配参考图片...")
                replication_plan["shots"] = self._assign_materials_to_shots(
                    replication_plan.get("shots", []), session_materials
                )

            if await context.is_cancelled():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

            usage_records = [{
                "provider": "qwen",
                "model_name": getattr(self.llm, "client", None).model if getattr(self.llm, "client", None) else "mock",
                "operation": "video_replication_analysis",
                **usage,
            }]

            output = {
                "requires_confirmation": True,
                "selected_skill": self.video_replication_skill_name,
                "replication_plan": replication_plan,
                "extracted_frames": self._serialize_extracted_frames(extracted_frames),
                "analysis_report": self._build_replication_analysis_report(
                    replication_plan=replication_plan,
                    background_context=background_context,
                    extracted_frames=extracted_frames,
                ),
                "tool_call_log": tool_call_log,
                "analysis_mode": analysis_mode,
                "platform": platform,
                "style": style,
                "voice_config": {
                    "voice_id": voice_id,
                    "speed": 1.0,
                },
                "script": script,
                "background_context": background_context,
            }

            return AgentResult(success=True, output_data=output, usage_records=usage_records)

        except Exception as e:
            if "cancelled" in str(e).lower():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")
            logger.error(f"Video replication analysis failed: {e}", exc_info=True)
            return AgentResult(
                success=False, output_data={},
                error=f"视频复刻分析失败: {e!r}",
            )

    async def _get_session_materials(self, context: AgentContext) -> list[dict]:
        """Fetch image materials selected for the current pipeline's auto-session, ordered by sort_order."""
        async with context.db_session_factory() as session:
            run = await session.get(PipelineRun, context.pipeline_run_id)
            if not run or not run.session_id:
                return []
            result = await session.execute(
                select(AutoSessionMaterialSelection, Material)
                .join(Material, AutoSessionMaterialSelection.material_id == Material.id)
                .where(
                    AutoSessionMaterialSelection.session_id == run.session_id,
                    Material.media_type == "image",
                )
                .order_by(AutoSessionMaterialSelection.sort_order)
            )
            rows = result.all()
            materials = []
            for sel, mat in rows:
                materials.append({
                    "material_id": mat.id,
                    "file_path": mat.file_path,
                    "filename": mat.filename,
                    "category": mat.category,
                    "thumbnail_url": f"/api/materials/{mat.id}/thumbnail",
                })
            return materials

    def _assign_materials_to_shots(self, shots: list[dict], materials: list[dict]) -> list[dict]:
        """Assign session materials to shots in order, cycling if fewer materials than shots."""
        if not materials:
            return shots
        root = Path(settings.MATERIALS_ROOT)
        result = []
        for i, shot in enumerate(shots):
            mat = materials[i % len(materials)]
            full_path = str((root / mat["file_path"]).resolve())
            result.append({
                **shot,
                "material_id": mat["material_id"],
                "material_image_path": full_path,
                "material_filename": mat["filename"],
                "material_thumbnail_url": mat["thumbnail_url"],
            })
        return result

    def _sanitize_replication_plan(self, replication_plan: object) -> dict:
        plan = _as_replication_mapping(replication_plan)
        if not plan and replication_plan is not None:
            logger.warning(
                "Replication plan is not a mapping; received %s. Falling back to empty defaults.",
                type(replication_plan).__name__,
            )

        audio_design = self._sanitize_replication_design(
            plan.get("audio_design"),
            label="audio_design",
        )
        # Fix voice_speed: if 0 or missing, default to 1.0
        voice_speed = audio_design.get("voice_speed")
        if not isinstance(voice_speed, (int, float)) or voice_speed <= 0:
            audio_design["voice_speed"] = 1.0

        music_design = self._sanitize_replication_design(
            plan.get("music_design"),
            label="music_design",
        )
        shots = self._sanitize_replication_shot_items(plan.get("shots"))

        return {
            "analysis_report": _as_string_or_empty(plan.get("analysis_report")),
            "video_summary": _as_string_or_empty(plan.get("video_summary")),
            "overall_style": _as_string_or_empty(plan.get("overall_style")),
            "color_palette": _as_string_or_empty(plan.get("color_palette")),
            "pacing": _as_string_or_empty(plan.get("pacing")),
            "audio_design": audio_design,
            "music_design": music_design,
            "shots": shots,
        }

    def _has_substantive_replication_plan(self, plan: dict) -> bool:
        if not isinstance(plan, dict):
            return False

        shots = plan.get("shots")
        audio_design = plan.get("audio_design")
        music_design = plan.get("music_design")

        has_summary = bool(_as_string_or_empty(plan.get("video_summary")).strip())
        has_style = any(
            bool(_as_string_or_empty(plan.get(key)).strip())
            for key in ("overall_style", "color_palette", "pacing")
        )
        has_report = bool(_as_string_or_empty(plan.get("analysis_report")).strip())
        has_shots = isinstance(shots, list) and any(
            isinstance(shot, dict) and (
                _as_string_or_empty(shot.get("description")).strip()
                or _as_string_or_empty(shot.get("visual_design")).strip()
            )
            for shot in shots
        )
        has_audio = isinstance(audio_design, dict) and any(
            key != "voice_speed" and bool(_as_string_or_empty(audio_design.get(key)).strip())
            for key in audio_design.keys()
        )
        has_music = isinstance(music_design, dict) and any(
            bool(_as_string_or_empty(music_design.get(key)).strip())
            for key in music_design.keys()
        )

        return has_shots and (has_summary or has_style or has_report or has_audio or has_music)

    async def _repair_replication_plan(
        self,
        *,
        context: AgentContext,
        user_prompt: str,
        extracted_frames: list[dict],
        analysis_mode: str,
        current_plan: dict,
        tool_call_log: list[dict],
    ) -> dict:
        frame_paths = [
            str(frame.get("frame_path"))
            for frame in extracted_frames
            if isinstance(frame, dict) and _is_existing_image_file(frame.get("frame_path"))
        ]

        schema = {
            "name": "replication_plan_repair",
            "schema": {
                "type": "object",
                "properties": {
                    "analysis_report": {"type": "string"},
                    "video_summary": {"type": "string"},
                    "overall_style": {"type": "string"},
                    "color_palette": {"type": "string"},
                    "pacing": {"type": "string"},
                    "audio_design": {
                        "type": "object",
                        "properties": {
                            "voice_style": {"type": "string"},
                            "voice_speed": {"type": "number"},
                            "voice_tone": {"type": "string"},
                            "narration_notes": {"type": "string"},
                        },
                        "required": ["voice_style", "voice_speed", "voice_tone"],
                    },
                    "music_design": {
                        "type": "object",
                        "properties": {
                            "bgm_mood": {"type": "string"},
                            "bgm_style": {"type": "string"},
                            "volume_level": {"type": "string"},
                            "music_notes": {"type": "string"},
                        },
                        "required": ["bgm_mood", "bgm_style"],
                    },
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_idx": {"type": "integer"},
                                "description": {"type": "string"},
                                "visual_design": {"type": "string"},
                                "reference_frame_path": {"type": "string"},
                                "timestamp_range": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                },
                                "camera_movement": {"type": "string"},
                                "color_tone": {"type": "string"},
                                "subjects": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "suggested_duration_seconds": {"type": "integer"},
                            },
                            "required": [
                                "shot_idx", "description", "reference_frame_path",
                                "camera_movement", "color_tone", "subjects",
                                "suggested_duration_seconds",
                            ],
                        },
                    },
                },
                "required": ["analysis_report", "video_summary", "overall_style", "color_palette", "pacing", "audio_design", "music_design", "shots"],
            },
        }

        repair_prompt = (
            "你上一次返回的复刻方案结构不完整。请基于以下上下文重新输出完整 JSON。\n\n"
            "要求：\n"
            "1. 必须补全 analysis_report、video_summary、overall_style、color_palette、pacing、audio_design、music_design、shots。\n"
            "2. shots 不能为空；每个镜头都要有 description、visual_design、reference_frame_path、camera_movement、color_tone、subjects、suggested_duration_seconds。\n"
            "3. 只输出 JSON，不要附加解释。\n\n"
            f"原始需求：\n{user_prompt}\n\n"
            f"分析模式：{analysis_mode}\n\n"
            f"工具调用记录：\n{json.dumps(tool_call_log, ensure_ascii=False)}\n\n"
            f"当前不完整结果：\n{json.dumps(current_plan, ensure_ascii=False)}\n"
        )

        repaired, usage = await _run_cancellable(
            self.llm.generate_structured(
                system_prompt=VIDEO_REPLICATION_SYSTEM_PROMPT,
                user_prompt=repair_prompt,
                schema=schema,
                image_paths=frame_paths or None,
            ),
            context,
        )
        repaired_plan = self._sanitize_replication_plan(repaired)
        if not self._has_substantive_replication_plan(repaired_plan):
            raise RuntimeError("模型返回的复刻方案结构不完整，缺少镜头或关键设计字段")

        context.events.append(
            {
                "type": "replication_plan_repair",
                "usage": usage,
            }
        )
        return repaired_plan

    def _sanitize_replication_design(self, value: object, *, label: str) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            logger.warning(
                "Replication plan field '%s' should be an object, got %s. Using empty object instead.",
                label,
                type(value).__name__,
            )
            return {}

        result = {}
        for key, item in value.items():
            if item is None or isinstance(item, (dict, list)):
                continue
            if isinstance(item, (int, float)):
                result[key] = item
            else:
                result[key] = _as_string_or_empty(item)
        return result

    def _sanitize_replication_shot_items(self, value: object) -> list[dict]:
        if value is None:
            return []
        if not isinstance(value, list):
            logger.warning(
                "Replication plan field 'shots' should be a list, got %s. Using empty list instead.",
                type(value).__name__,
            )
            return []

        normalized: list[dict] = []
        for index, shot in enumerate(value):
            if not isinstance(shot, dict):
                logger.warning(
                    "Replication plan shot at index %s is %s, expected object. Skipping malformed shot.",
                    index,
                    type(shot).__name__,
                )
                continue

            subjects_raw = shot.get("subjects")
            if isinstance(subjects_raw, list):
                subjects = [_as_string_or_empty(item) for item in subjects_raw if item is not None]
            elif subjects_raw is None:
                subjects = []
            else:
                subjects = [_as_string_or_empty(subjects_raw)]

            duration_raw = shot.get("suggested_duration_seconds")
            try:
                suggested_duration_seconds = int(duration_raw) if duration_raw is not None else None
            except (TypeError, ValueError):
                suggested_duration_seconds = None

            shot_idx_raw = shot.get("shot_idx", index)
            try:
                shot_idx = int(shot_idx_raw)
            except (TypeError, ValueError):
                shot_idx = index

            normalized.append({
                "shot_idx": shot_idx,
                "description": _as_string_or_empty(shot.get("description")),
                "visual_design": _as_string_or_empty(shot.get("visual_design")),
                "reference_frame_path": _as_string_or_empty(shot.get("reference_frame_path")),
                "timestamp_range": _as_numeric_list(shot.get("timestamp_range")),
                "camera_movement": _as_string_or_empty(shot.get("camera_movement")),
                "color_tone": _as_string_or_empty(shot.get("color_tone")),
                "subjects": subjects,
                "suggested_duration_seconds": suggested_duration_seconds,
            })

        return normalized

    def _normalize_replication_shots(self, shots: list[dict], extracted_frames: list[dict]) -> list[dict]:
        sanitized_shots = self._sanitize_replication_shot_items(shots)
        valid_frames = [
            frame for frame in extracted_frames
            if isinstance(frame, dict) and _is_existing_image_file(frame.get("frame_path"))
        ]
        if not valid_frames:
            return sanitized_shots

        def choose_frame(shot: dict, shot_index: int) -> str:
            candidate_path = shot.get("reference_frame_path")
            if _is_existing_image_file(candidate_path):
                return str(candidate_path)

            timestamp_range = shot.get("timestamp_range") or []
            target_ts = None
            if len(timestamp_range) >= 2:
                target_ts = (float(timestamp_range[0]) + float(timestamp_range[1])) / 2
            elif len(timestamp_range) == 1:
                target_ts = float(timestamp_range[0])

            if target_ts is not None:
                frame = min(
                    valid_frames,
                    key=lambda item: abs(float(item.get("timestamp_seconds") or 0.0) - target_ts),
                )
                return str(frame["frame_path"])

            frame = valid_frames[min(shot_index, len(valid_frames) - 1)]
            return str(frame["frame_path"])

        normalized = []
        for idx, shot in enumerate(sanitized_shots):
            updated = dict(shot)
            updated["reference_frame_path"] = choose_frame(shot, idx)
            normalized.append(updated)
        return normalized

    def _serialize_extracted_frames(self, extracted_frames: list[dict]) -> list[dict]:
        serialized = []
        for frame in extracted_frames:
            frame_path = frame.get("frame_path")
            if not frame_path:
                continue
            serialized.append({
                "frame_path": str(frame_path),
                "timestamp_seconds": float(frame.get("timestamp_seconds") or 0.0),
                "frame_index": int(frame.get("frame_index") or 0),
            })
        return serialized

    def _build_replication_analysis_report(
        self,
        *,
        replication_plan: dict,
        background_context: str,
        extracted_frames: list[dict],
    ) -> str:
        # replication_plan is already sanitized by the caller.
        plan = replication_plan if isinstance(replication_plan, dict) else {}

        # Prefer the LLM-generated natural language analysis_report if present.
        llm_report = plan.get("analysis_report") or ""
        if isinstance(llm_report, str) and llm_report.strip():
            header = "已完成上传视频解析，以下是本次参考视频的分析报告。"
            return f"{header}\n\n{llm_report.strip()}"

        # Fallback: build a brief summary from the structured plan fields.
        valid_frames = [frame for frame in extracted_frames if isinstance(frame, dict) and frame.get("frame_path")]
        shots = plan.get("shots") or []

        report_sections: list[str] = ["已完成上传视频解析，以下是本次参考视频的拆解报告。"]

        if plan.get("video_summary"):
            report_sections.extend(["", "内容概述", str(plan["video_summary"]).strip()])

        style_lines = [
            line for line in [
                f"整体风格：{plan.get('overall_style')}" if plan.get("overall_style") else None,
                f"色彩基调：{plan.get('color_palette')}" if plan.get("color_palette") else None,
                f"节奏特征：{plan.get('pacing')}" if plan.get("pacing") else None,
                f"关键帧数量：{len(valid_frames)}" if valid_frames else None,
                f"镜头数量：{len(shots)}" if shots else None,
            ] if line
        ]
        if style_lines:
            report_sections.extend(["", "风格与节奏", *style_lines])

        if background_context.strip():
            report_sections.extend(["", "背景信息约束", background_context.strip()])

        return "\n".join(section for section in report_sections if section is not None).strip()

    def _build_replication_user_prompt(
        self,
        *,
        video_path: str,
        platform: str,
        style: str,
        script: str,
        background_context: str,
        adjustment_feedback: str,
    ) -> str:
        has_explicit_direction = self._has_explicit_replication_direction(
            script=script,
            adjustment_feedback=adjustment_feedback,
        )

        sections = [
            "请分析以下参考视频并生成复刻方案。",
            f"视频路径: {video_path}",
            f"目标平台: {platform}",
            f"风格: {style}",
        ]

        if adjustment_feedback.strip():
            sections.append(f"用户调整反馈:\n{adjustment_feedback.strip()}")

        if script.strip():
            sections.append(f"用户需求描述（可含脚本）:\n{script.strip()}")

        if background_context.strip():
            sections.append(f"背景信息参考:\n{background_context.strip()}")

        if has_explicit_direction:
            sections.append(
                "需求优先级:\n"
                "1. 优先满足用户明确提出的需求描述（含脚本要求）或调整反馈。\n"
                "2. 参考视频主要用于复用镜头结构、节奏、摄影语法和视觉组织方式。\n"
                "3. 背景信息在不冲突时用于补充品牌、角色和场景细节。"
            )
        elif background_context.strip():
            sections.append(
                "需求优先级:\n"
                "1. 当前用户没有给出明确的额外需求，必须将背景信息视为本次复刻执行方案的主要约束。\n"
                "2. 参考视频主要用于借鉴镜头结构、节奏、景别、运镜和剪辑思路，而不是照搬其中与背景信息无关的主体内容。\n"
                "3. 如果参考视频内容与背景信息冲突，输出方案时以背景信息为准，确保镜头描述、主体、场景和表达口径与背景信息相关。"
            )
        else:
            sections.append(
                "需求优先级:\n"
                "1. 当前没有额外创作约束，请忠实分析参考视频并输出可执行的复刻方案。"
            )

        return "\n".join(sections)

    def _has_explicit_replication_direction(self, *, script: str, adjustment_feedback: str) -> bool:
        if adjustment_feedback.strip():
            return True

        normalized = self._normalize_replication_direction(script)
        if not normalized:
            return False

        generic_phrases = {
            "参考这个视频",
            "参考该视频",
            "按这个视频复刻",
            "按这个复刻",
            "复刻这个视频",
            "复刻该视频",
            "照着这个视频做",
            "照着做",
            "按这个来",
            "跟这个一样",
            "同款",
            "同风格",
        }
        if normalized in generic_phrases:
            return False

        return True

    def _normalize_replication_direction(self, text: str) -> str:
        compact = re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE)
        return compact.strip()

    async def _resolve_video(self, context: AgentContext, video_id: str) -> str | None:
        """Resolve a VideoUpload ID to its file path."""
        async with context.db_session_factory() as session:
            upload = await session.get(VideoUpload, video_id)
            if upload and upload.file_path:
                path = Path(upload.file_path)
                if path.is_absolute() and path.exists():
                    return str(path)
                # Try relative to UPLOAD_DIR
                full = Path(settings.UPLOAD_DIR) / path
                if full.exists():
                    return str(full.resolve())
                # file_path itself may already be absolute
                if path.exists():
                    return str(path.resolve())
            return None
