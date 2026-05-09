from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import TypeVar

from sqlalchemy import select

from app.agents.core.base import AgentContext, AgentResult, BaseAgent
from app.core.config import settings
from app.models.auto_chat import AutoSessionMaterialSelection
from app.models.material import Material
from app.models.pipeline import PipelineRun
from app.models.video_upload import VideoUpload
from app.prompts import VIDEO_ANALYSIS_SYSTEM_PROMPT, VIDEO_REPLICATION_SYSTEM_PROMPT
from app.services.keyframe_extractor import KeyframeExtractor
from app.services.llm_service import LLMService

_T = TypeVar("_T")

logger = logging.getLogger(__name__)


# ── Module-level helpers ──────────────────────────────────────────────────────

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


def _as_replication_mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _is_existing_image_file(file_path: str | None) -> bool:
    if not file_path:
        return False
    path = Path(file_path)
    return path.exists() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


async def _run_cancellable(
    coro: "asyncio.Coroutine[None, None, _T]",
    context: "AgentContext",
    poll_interval: float = 2.0,
) -> "_T":
    """Wrap a long-running coroutine so it is interrupted if the pipeline is cancelled."""
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


# ── ReplicationPlannerAgent ───────────────────────────────────────────────────

class ReplicationPlannerAgent(BaseAgent):
    """Pipeline stage that analyses a reference video and produces a replication plan.

    Replaces the inline replication logic that was previously embedded inside
    OrchestratorAgent.  Returns an AgentResult with ``requires_confirmation=True``
    so the pipeline pauses for user approval before proceeding.
    """

    name = "replication_planner"

    def __init__(
        self,
        llm_service: LLMService,
        keyframe_extractor: KeyframeExtractor | None = None,
    ):
        self.llm = llm_service
        self.keyframe_extractor = keyframe_extractor

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        reference_video_id: str = input_data.get("reference_video_id", "")
        if not reference_video_id:
            return AgentResult(
                success=False, output_data={},
                error="replication_planner: reference_video_id is required",
            )

        return await self._execute_replication(context, input_data, reference_video_id)

    # ── Core replication planning ─────────────────────────────────────────────

    async def _execute_replication(
        self, context: AgentContext, input_data: dict, reference_video_id: str
    ) -> AgentResult:
        """Analyse a reference video and produce a replication plan for user confirmation."""

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

        video_path, cached_analysis = await self._resolve_video(context, reference_video_id)
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

        keyframe_dir = os.path.join(
            settings.GENERATED_DIR, f"{context.trace_id}_keyframes"
        )

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

        _keyframe_call_count = [0]
        extracted_frames: list[dict] = []

        async def tool_executor(tool_name: str, tool_args: dict) -> tuple[str, list[str]]:
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
                "required": [
                    "analysis_report", "video_summary", "overall_style",
                    "color_palette", "pacing", "audio_design", "music_design", "shots",
                ],
            },
        }

        # Step 1: Obtain a text analysis of the reference video.
        # generate_with_tools + video_url is not supported by qwen3-omni-flash.
        # We therefore pre-analyse the video with a plain text call (generate_text)
        # and pass the result as context to generate_with_tools (which never receives
        # the video file directly).
        if cached_analysis:
            video_analysis = cached_analysis
            await update_progress("读取已有视频解析报告...")
        else:
            await update_progress("正在解析参考视频（内容、风格、节奏）...")
            analysis_user_prompt = (
                "请对这段视频进行全面专业的多维度解析报告。"
                "使用中文输出，按【视频概述】【镜头方案】【视觉风格】【配音与口播】【音乐设计】【营销策略】【复刻建议】七个维度，"
                "每个维度标题加【】标注，内容连贯详细，每个观察都说明原因和营销作用。"
            )
            try:
                video_analysis, _ = await self.llm.generate_text(
                    system_prompt=VIDEO_ANALYSIS_SYSTEM_PROMPT,
                    user_prompt=analysis_user_prompt,
                    video_paths=[video_path],
                )
                video_analysis = video_analysis.strip()
                # Persist to cache for future replication/analysis calls
                if video_analysis:
                    async with context.db_session_factory() as session:
                        upload = await session.get(VideoUpload, reference_video_id)
                        if upload:
                            upload.analysis_report = video_analysis
                            await session.commit()
            except Exception as analysis_err:
                if "cancelled" in str(analysis_err).lower():
                    raise
                logger.warning("Video pre-analysis failed, proceeding without it: %r", analysis_err)
                video_analysis = ""

        user_prompt = self._build_replication_user_prompt(
            video_path=video_path,
            platform=platform,
            style=style,
            script=script,
            background_context=background_context,
            adjustment_feedback=adjustment_feedback,
            cached_analysis=video_analysis or None,
        )

        await update_progress("正在生成完整复刻方案（含音频、音乐、镜头设计）...")

        try:
            # generate_with_tools never receives video_paths — video analysis has already
            # been obtained above and embedded as text in user_prompt.
            analysis_mode = "text_analysis+keyframes"
            replication_plan, tool_call_log, usage = await _run_cancellable(
                self.llm.generate_with_tools(
                    system_prompt=VIDEO_REPLICATION_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    schema=replication_schema,
                    tools=[extract_keyframes_tool],
                    tool_executor=tool_executor,
                ),
                context,
            )

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

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _resolve_video(self, context: AgentContext, video_id: str) -> tuple[str | None, str | None]:
        """Resolve a VideoUpload ID to its (file_path, cached_analysis_report)."""
        async with context.db_session_factory() as session:
            upload = await session.get(VideoUpload, video_id)
            if upload and upload.file_path:
                path = Path(upload.file_path)
                if path.is_absolute() and path.exists():
                    return str(path), (upload.analysis_report or None)
                full = Path(settings.UPLOAD_DIR) / path
                if full.exists():
                    return str(full.resolve()), (upload.analysis_report or None)
                if path.exists():
                    return str(path.resolve()), (upload.analysis_report or None)
            return None, None

    async def _get_session_materials(self, context: AgentContext) -> list[dict]:
        """Fetch image materials selected for the current pipeline's auto-session."""
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

    # ── Plan sanitize / repair / normalize ───────────────────────────────────

    def _sanitize_replication_plan(self, replication_plan: object) -> dict:
        plan = _as_replication_mapping(replication_plan)
        if not plan and replication_plan is not None:
            logger.warning(
                "Replication plan is not a mapping; received %s. Falling back to empty defaults.",
                type(replication_plan).__name__,
            )

        audio_design = self._sanitize_replication_design(plan.get("audio_design"), label="audio_design")
        voice_speed = audio_design.get("voice_speed")
        if not isinstance(voice_speed, (int, float)) or voice_speed <= 0:
            audio_design["voice_speed"] = 1.0

        music_design = self._sanitize_replication_design(plan.get("music_design"), label="music_design")
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
                "required": [
                    "analysis_report", "video_summary", "overall_style",
                    "color_palette", "pacing", "audio_design", "music_design", "shots",
                ],
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

        context.events.append({"type": "replication_plan_repair", "usage": usage})
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

    def _normalize_replication_shots(
        self, shots: list[dict], extracted_frames: list[dict]
    ) -> list[dict]:
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

    # ── Prompt building ───────────────────────────────────────────────────────

    def _build_replication_analysis_report(
        self,
        *,
        replication_plan: dict,
        background_context: str,
        extracted_frames: list[dict],
    ) -> str:
        plan = replication_plan if isinstance(replication_plan, dict) else {}

        llm_report = plan.get("analysis_report") or ""
        if isinstance(llm_report, str) and llm_report.strip():
            header = "已完成上传视频解析，以下是本次参考视频的分析报告。"
            return f"{header}\n\n{llm_report.strip()}"

        valid_frames = [
            frame for frame in extracted_frames
            if isinstance(frame, dict) and frame.get("frame_path")
        ]
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

        audio_design = plan.get("audio_design")
        if isinstance(audio_design, dict):
            audio_lines = [
                f"音色方向：{audio_design.get('voice_style')}" if audio_design.get("voice_style") else None,
                f"语速建议：{audio_design.get('voice_speed')}" if audio_design.get("voice_speed") else None,
                f"语气风格：{audio_design.get('voice_tone')}" if audio_design.get("voice_tone") else None,
                f"口播备注：{audio_design.get('narration_notes')}" if audio_design.get("narration_notes") else None,
            ]
            audio_lines = [line for line in audio_lines if line]
            if audio_lines:
                report_sections.extend(["", "音频设计", *audio_lines])

        music_design = plan.get("music_design")
        if isinstance(music_design, dict):
            music_lines = [
                f"音乐情绪：{music_design.get('bgm_mood')}" if music_design.get("bgm_mood") else None,
                f"音乐风格：{music_design.get('bgm_style')}" if music_design.get("bgm_style") else None,
                f"音量建议：{music_design.get('volume_level')}" if music_design.get("volume_level") else None,
                f"音乐备注：{music_design.get('music_notes')}" if music_design.get("music_notes") else None,
            ]
            music_lines = [line for line in music_lines if line]
            if music_lines:
                report_sections.extend(["", "音乐设计", *music_lines])

        if isinstance(shots, list):
            shot_lines: list[str] = []
            for index, shot in enumerate(shots):
                if not isinstance(shot, dict):
                    continue
                raw_idx = shot.get("shot_idx", index)
                try:
                    shot_number = int(raw_idx) + 1
                except (TypeError, ValueError):
                    shot_number = index + 1
                description = _as_string_or_empty(
                    shot.get("description") or shot.get("visual_design")
                ).strip()
                shot_lines.append(f"镜头 {shot_number}：{description or '未提供描述'}")
                if shot.get("visual_design"):
                    shot_lines.append(f"画面设计：{shot.get('visual_design')}")
                if shot.get("camera_movement"):
                    shot_lines.append(f"运镜：{shot.get('camera_movement')}")
                if shot.get("color_tone"):
                    shot_lines.append(f"色调：{shot.get('color_tone')}")
                subjects = shot.get("subjects")
                if isinstance(subjects, list) and subjects:
                    shot_lines.append(f"主体：{'、'.join(_as_string_or_empty(item) for item in subjects)}")
                timestamp_range = shot.get("timestamp_range")
                if isinstance(timestamp_range, list) and len(timestamp_range) >= 2:
                    shot_lines.append(f"参考时间：{timestamp_range[0]}s - {timestamp_range[1]}s")
                if shot.get("suggested_duration_seconds") is not None:
                    shot_lines.append(f"建议时长：{shot.get('suggested_duration_seconds')}s")
            if shot_lines:
                report_sections.extend(["", "镜头拆解", *shot_lines])

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
        cached_analysis: str | None = None,
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

        if cached_analysis and cached_analysis.strip():
            sections.append(
                f"【已有视频分析报告（可直接参考，无需重复解读）】\n{cached_analysis.strip()}"
            )

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

    def _has_explicit_replication_direction(
        self, *, script: str, adjustment_feedback: str
    ) -> bool:
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

        generic_tokens = [
            "请",
            "帮我",
            "我想",
            "想",
            "做",
            "一个",
            "一条",
            "视频",
            "这个",
            "该",
            "参考",
            "复刻",
            "同款",
            "同风格",
            "照着",
            "跟",
            "一样",
            "按",
            "来",
        ]
        remainder = normalized
        for token in generic_tokens:
            remainder = remainder.replace(token, "")
        if not remainder:
            return False

        return True

    def _normalize_replication_direction(self, text: str) -> str:
        compact = re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE)
        return compact.strip()
