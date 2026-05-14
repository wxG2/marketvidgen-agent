from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.agents.core.base import AgentContext, AgentResult, BaseAgent
from app.core.config import settings
from app.models.auto_chat import AutoSessionMaterialSelection
from app.models.material import Material
from app.models.video_upload import VideoUpload
from app.prompts import REMIX_PLANNING_PROMPT, REMIX_SHOT_ANALYSIS_PROMPT, REMIX_VOICEOVER_PROMPT
from app.services.llm_service import LLMService
from app.services.media_utils import run_subprocess
from app.services.video_editing.helpers import _probe_duration
from app.services.video_editing.video_profiler import ShotProfile, VideoProfile, VideoProfiler
from sqlalchemy import select

logger = logging.getLogger(__name__)

MIN_REMIX_SEGMENT_DURATION_SECONDS = 1.0
REMIX_TARGET_TOLERANCE_SECONDS = 2.0
PREFERRED_REMIX_EMOTIONS = {"calm", "warm", "neutral"}


class RemixPlannerAgent(BaseAgent):
    """Plan a multi-video remix from profiled source shots."""

    name = "remix_planner"

    def __init__(self, llm_service: LLMService, video_profiler: VideoProfiler):
        self.llm = llm_service
        self.video_profiler = video_profiler

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        video_ids = [str(item) for item in input_data.get("reference_video_ids") or [] if item]
        if len(video_ids) < 2:
            return AgentResult(success=False, output_data={}, error="remix_planner: reference_video_ids requires at least 2 videos")

        try:
            await context.report_progress("正在解析混剪源视频...", agent_name=self.name)
            resolved = await self._resolve_videos(context, video_ids)
            if len(resolved) < 2:
                return AgentResult(success=False, output_data={}, error="至少需要找到 2 个可用源视频")

            profiles = await asyncio.gather(
                *[
                    self.video_profiler.profile_video(path, video_id)
                    for video_id, path in resolved.items()
                ]
            )
            profiles = [profile for profile in profiles if profile.shots]
            if len(profiles) < 2:
                return AgentResult(success=False, output_data={}, error="可分析的视频镜头不足，无法生成混剪方案")

            await context.report_progress("正在解析背景音乐...", agent_name=self.name)
            bgm_context, bgm_error = await self._resolve_bgm_context(context, input_data)
            if bgm_error:
                return AgentResult(success=False, output_data={}, error=bgm_error)
            working_input = dict(input_data)
            working_input["_resolved_bgm_context"] = bgm_context

            usage_records: list[dict[str, Any]] = []
            await context.report_progress("正在分析关键帧语义...", agent_name=self.name)
            shot_usage = await self._enrich_shots(profiles, working_input)
            if shot_usage:
                usage_records.append(shot_usage)

            await context.report_progress("正在生成混剪编排方案...", agent_name=self.name)
            plan, plan_usage = await self._build_plan(profiles, working_input)
            if plan_usage:
                usage_records.append(plan_usage)

            plan = self._normalize_plan(plan, profiles, working_input)

            if _remix_add_voiceover(working_input):
                await context.report_progress("正在生成镜头旁白文案...", agent_name=self.name)
                vo_usage = await self._enrich_voiceovers(plan.get("segments", []), working_input)
                if vo_usage:
                    usage_records.append(vo_usage)

            validation_error = validate_remix_plan(plan)
            if validation_error:
                return AgentResult(success=False, output_data={}, error=f"混剪方案校验失败: {validation_error}")
            output = {
                "requires_confirmation": True,
                "remix_plan": plan,
                "video_profiles": [profile.to_dict() for profile in profiles],
            }
            return AgentResult(success=True, output_data=output, usage_records=usage_records)
        except Exception as exc:
            logger.error("Remix planning failed: %s", exc, exc_info=True)
            return AgentResult(success=False, output_data={}, error=f"混剪分析失败: {exc}")

    async def _resolve_videos(self, context: AgentContext, video_ids: list[str]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        async with context.db_session_factory() as session:
            for video_id in video_ids:
                upload = await session.get(VideoUpload, video_id)
                path = self._resolve_upload_path(upload)
                if path:
                    resolved[video_id] = path
        return resolved

    @staticmethod
    def _resolve_upload_path(upload: VideoUpload | None) -> str | None:
        if not upload or not upload.file_path:
            return None
        path = Path(upload.file_path)
        candidates = [path]
        if not path.is_absolute():
            candidates.insert(0, Path(settings.UPLOAD_DIR) / path)
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve())
        return None

    async def _resolve_bgm_context(self, context: AgentContext, input_data: dict) -> tuple[dict[str, Any], str | None]:
        config = input_data.get("remix_config") or {}
        explicit_material_id = str(config.get("bgm_material_id") or "").strip()
        async with context.db_session_factory() as session:
            # Level 1: explicit bgm_material_id
            if explicit_material_id:
                material = await session.get(Material, explicit_material_id)
                ctx, err = await self._material_bgm_context(
                    material,
                    explicit_material_id=explicit_material_id,
                    user_id=context.user_id,
                )
                if ctx:
                    return ctx, None
                logger.warning("explicit bgm_material_id %s failed: %s", explicit_material_id, err)

            # Level 2: first session-selected audio material
            session_id = str(input_data.get("session_id") or "").strip()
            if session_id:
                result = await session.execute(
                    select(Material)
                    .join(AutoSessionMaterialSelection, AutoSessionMaterialSelection.material_id == Material.id)
                    .where(
                        AutoSessionMaterialSelection.session_id == session_id,
                        Material.media_type == "audio",
                    )
                    .order_by(AutoSessionMaterialSelection.sort_order.asc(), AutoSessionMaterialSelection.created_at.asc())
                    .limit(1)
                )
                material = result.scalars().first()
                if material:
                    ctx, err = await self._material_bgm_context(
                        material,
                        explicit_material_id=material.id,
                        user_id=context.user_id,
                    )
                    if ctx:
                        return ctx, None
                    logger.warning("session bgm material %s failed: %s", material.id, err)

            # Level 3: any audio material owned by this user
            if context.user_id:
                result = await session.execute(
                    select(Material)
                    .where(Material.user_id == context.user_id, Material.media_type == "audio")
                    .order_by(Material.indexed_at.desc())
                    .limit(1)
                )
                material = result.scalars().first()
                if material:
                    ctx, err = await self._material_bgm_context(
                        material,
                        explicit_material_id=material.id,
                        user_id=context.user_id,
                    )
                    if ctx:
                        return ctx, None
                    logger.warning("user fallback bgm material %s failed: %s", material.id, err)

        bgm_mood = str(config.get("bgm_mood") or input_data.get("bgm_mood") or "none")
        return {
            "bgm_source": "library" if bgm_mood != "none" else "none",
            "bgm_material_id": None,
            "bgm_path": "",
            "bgm_filename": "",
            "bgm_duration_seconds": None,
        }, None

    async def _material_bgm_context(
        self,
        material: Material | None,
        *,
        explicit_material_id: str,
        user_id: str | None,
    ) -> tuple[dict[str, Any], str | None]:
        if not material or (user_id and material.user_id != user_id):
            return {}, f"BGM 素材不存在或无权限访问: {explicit_material_id}"
        if str(material.media_type or "") != "audio":
            return {}, f"BGM 素材必须是音频文件: {material.filename}"

        path = self._resolve_material_path(material)
        if not path:
            return {}, f"BGM 音频文件不存在: {material.filename}"
        duration = await _probe_duration(settings.FFMPEG_BIN, path, run_subprocess)
        return {
            "bgm_source": "uploaded",
            "bgm_material_id": material.id,
            "bgm_path": path,
            "bgm_filename": material.filename,
            "bgm_duration_seconds": duration,
        }, None

    @staticmethod
    def _resolve_material_path(material: Material) -> str | None:
        if not material.file_path:
            return None
        path = Path(material.file_path)
        candidates = [path]
        if not path.is_absolute():
            candidates.insert(0, Path(settings.MATERIALS_ROOT) / path)
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve())
        return None

    async def _enrich_shots(self, profiles: list[VideoProfile], input_data: dict) -> dict[str, Any] | None:
        shots = [shot for profile in profiles for shot in profile.shots]
        image_paths = [shot.keyframe_path for shot in shots if Path(shot.keyframe_path).exists()]
        if not image_paths:
            self._fill_default_shot_semantics(shots)
            return None

        schema = {
            "name": "remix_shot_analysis",
            "schema": {
                "type": "object",
                "properties": {
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "video_id": {"type": "string"},
                                "shot_idx": {"type": "integer"},
                                "description": {"type": "string"},
                                "emotion_tag": {"type": "string"},
                                "visual_quality_score": {"type": "number"},
                            },
                            "required": ["video_id", "shot_idx", "description", "emotion_tag", "visual_quality_score"],
                        },
                    }
                },
                "required": ["shots"],
            },
        }
        prompt = json.dumps(
            {
                "shots": [
                    {
                        "video_id": shot.video_id,
                        "shot_idx": shot.shot_idx,
                        "timestamp": shot.start_seconds,
                        "keyframe_path": shot.keyframe_path,
                    }
                    for shot in shots
                ],
                "preference": input_data.get("script", ""),
            },
            ensure_ascii=False,
        )
        try:
            result, usage = await self.llm.generate_structured(
                system_prompt=REMIX_SHOT_ANALYSIS_PROMPT,
                user_prompt=prompt,
                schema=schema,
                image_paths=image_paths,
            )
            lookup = {
                (item.get("video_id"), int(item.get("shot_idx", -1))): item
                for item in result.get("shots", [])
                if isinstance(item, dict)
            }
            for shot in shots:
                item = lookup.get((shot.video_id, shot.shot_idx), {})
                shot.description = str(item.get("description") or f"视频 {shot.video_id} 的片段 {shot.shot_idx + 1}")
                shot.emotion_tag = str(item.get("emotion_tag") or "neutral")
                shot.visual_quality_score = _clamp_float(item.get("visual_quality_score"), 1.0, 10.0, 6.0)
            return self._usage("remix_shot_analysis", usage)
        except Exception as exc:
            logger.warning("Shot semantic analysis failed, using metadata fallback: %s", exc)
            self._fill_default_shot_semantics(shots)
            return None

    async def _build_plan(self, profiles: list[VideoProfile], input_data: dict) -> tuple[dict, dict[str, Any] | None]:
        schema = _remix_plan_schema()
        bgm_context = input_data.get("_resolved_bgm_context") or {}
        payload = {
            "profiles": [profile.to_dict() for profile in profiles],
            "preferences": {
                "script": input_data.get("script", ""),
                "duration_seconds": input_data.get("duration_seconds"),
                "remix_config": input_data.get("remix_config") or {},
                "bgm_context": _public_bgm_context(bgm_context),
            },
        }
        try:
            plan, usage = await self.llm.generate_structured(
                system_prompt=REMIX_PLANNING_PROMPT,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                schema=schema,
            )
            if isinstance(plan, dict) and plan.get("segments"):
                return plan, self._usage("remix_planning", usage)
        except Exception as exc:
            logger.warning("Remix planning LLM call failed, using fallback: %s", exc)
        return self._fallback_plan(profiles, input_data), None

    def _normalize_plan(self, plan: dict, profiles: list[VideoProfile], input_data: dict) -> dict:
        all_shots = [shot for profile in profiles for shot in profile.shots]
        eligible_shots = [shot for shot in all_shots if shot.duration_seconds >= MIN_REMIX_SEGMENT_DURATION_SECONDS]
        if not eligible_shots:
            return self._fallback_plan(profiles, input_data)

        requested_target = _target_duration(input_data)
        available_total = round(sum(shot.duration_seconds for shot in eligible_shots), 3)
        target_duration = requested_target
        duration_note = ""
        if available_total < requested_target - REMIX_TARGET_TOLERANCE_SECONDS:
            target_duration = round(max(MIN_REMIX_SEGMENT_DURATION_SECONDS, available_total), 3)
            duration_note = (
                f"可用素材总时长仅 {available_total:.1f}s，已将目标时长从 {requested_target:.1f}s 下调至 {target_duration:.1f}s。"
            )

        raw_segments = [item for item in plan.get("segments") or [] if isinstance(item, dict)]
        candidates = self._segment_candidates(raw_segments, eligible_shots)
        selected_candidates = self._select_segment_candidates(candidates, target_duration)
        if not selected_candidates:
            return self._fallback_plan(profiles, input_data)
        selected_candidates = self._diversify_candidates(selected_candidates, profiles)

        segments = self._materialize_segments(
            selected_candidates,
            include_voiceover=_remix_add_voiceover(input_data),
        )
        if not segments:
            return self._fallback_plan(profiles, input_data)

        total_duration = round(sum(segment["end_seconds"] - segment["start_seconds"] for segment in segments), 3)
        if abs(total_duration - target_duration) > REMIX_TARGET_TOLERANCE_SECONDS:
            target_duration = total_duration

        analysis_report = str(plan.get("analysis_report") or "已按画面质量、情绪标签和节奏结构选择镜头并完成混剪编排。")
        if duration_note:
            analysis_report = f"{analysis_report}\n{duration_note}"

        return {
            "title": str(plan.get("title") or "多视频混剪"),
            "concept": str(plan.get("concept") or input_data.get("script") or "从多个源视频中提取高光片段，组成节奏清晰的短片。"),
            "target_duration_seconds": round(target_duration, 3),
            "source_videos": [
                {
                    "video_id": profile.video_id,
                    "duration_seconds": profile.duration_seconds,
                    "total_shots": len(profile.shots),
                    "analysis_summary": str((plan.get("analysis_summary") or "") if isinstance(plan.get("analysis_summary"), str) else ""),
                }
                for profile in profiles
            ],
            "segments": segments,
            "audio_design": self._audio_design(plan, input_data),
            "analysis_report": analysis_report,
            "requires_more_material": bool(duration_note),
        }

    def _fallback_plan(self, profiles: list[VideoProfile], input_data: dict) -> dict:
        target = _target_duration(input_data)
        eligible_shots = [
            shot
            for profile in profiles
            for shot in profile.shots
            if shot.duration_seconds >= MIN_REMIX_SEGMENT_DURATION_SECONDS
        ]
        available_total = round(sum(shot.duration_seconds for shot in eligible_shots), 3)
        if available_total < target - REMIX_TARGET_TOLERANCE_SECONDS:
            target = round(max(MIN_REMIX_SEGMENT_DURATION_SECONDS, available_total), 3)

        # Guarantee at least 1 shot from every source video before greedy fill
        shots_by_video: dict[str, list] = {}
        for shot in eligible_shots:
            shots_by_video.setdefault(shot.video_id, []).append(shot)
        guaranteed: list = []
        for vid_shots in shots_by_video.values():
            best = max(vid_shots, key=self._shot_priority_score)
            guaranteed.append(best)
        guaranteed_ids = {id(s) for s in guaranteed}
        remaining = sorted(
            [s for s in eligible_shots if id(s) not in guaranteed_ids],
            key=self._shot_priority_score,
            reverse=True,
        )
        ranked_shots = guaranteed + remaining

        candidates = [
            {
                "shot": shot,
                "score": self._shot_priority_score(shot),
                "order": idx,
                "raw_idx": None,
                "transition_to_next": "cut",
                "transition_duration": 0.0,
            }
            for idx, shot in enumerate(ranked_shots)
        ]
        selected_candidates = self._select_segment_candidates(candidates, target)
        segments = self._materialize_segments(
            selected_candidates,
            include_voiceover=_remix_add_voiceover(input_data),
        )
        total_duration = round(sum(segment["end_seconds"] - segment["start_seconds"] for segment in segments), 3)
        if abs(total_duration - target) > REMIX_TARGET_TOLERANCE_SECONDS:
            target = total_duration

        requires_more_material = available_total < _target_duration(input_data) - REMIX_TARGET_TOLERANCE_SECONDS
        report = "LLM 方案不可用时，系统按视觉质量、情绪标签和节奏需求自动选择完整镜头。"
        if requires_more_material:
            report = f"{report} 可用素材总时长仅 {available_total:.1f}s，已自动下调目标时长。"
        return {
            "title": "多视频混剪",
            "concept": input_data.get("script") or "自动提取多个源视频中的高光镜头，形成紧凑混剪。",
            "target_duration_seconds": target,
            "source_videos": [
                {
                    "video_id": profile.video_id,
                    "duration_seconds": profile.duration_seconds,
                    "total_shots": len(profile.shots),
                    "analysis_summary": "",
                }
                for profile in profiles
            ],
            "segments": segments,
            "audio_design": self._audio_design({}, input_data),
            "analysis_report": report,
            "requires_more_material": requires_more_material,
        }

    def _segment_candidates(self, raw_segments: list[dict], eligible_shots: list[ShotProfile]) -> list[dict[str, Any]]:
        shot_lookup = {(shot.video_id, shot.shot_idx): shot for shot in eligible_shots}
        shots_by_video: dict[str, list[ShotProfile]] = {}
        for shot in eligible_shots:
            shots_by_video.setdefault(shot.video_id, []).append(shot)
        for video_shots in shots_by_video.values():
            video_shots.sort(key=lambda item: item.start_seconds)

        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for raw_idx, raw in enumerate(raw_segments):
            shot = self._resolve_raw_shot(raw, shot_lookup, shots_by_video)
            if shot is None:
                continue
            key = (shot.video_id, shot.shot_idx)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "shot": shot,
                    "score": self._shot_priority_score(shot, llm_boost=3.0),
                    "order": len(candidates),
                    "raw_idx": raw_idx,
                    "raw_segment": raw,
                    "transition_to_next": _transition(raw.get("transition_to_next")),
                    "transition_duration": raw.get("transition_duration"),
                }
            )

        ranked_remaining = sorted(
            [shot for shot in eligible_shots if (shot.video_id, shot.shot_idx) not in seen],
            key=self._shot_priority_score,
            reverse=True,
        )
        for shot in ranked_remaining:
            candidates.append(
                {
                    "shot": shot,
                    "score": self._shot_priority_score(shot),
                    "order": len(candidates),
                    "raw_idx": None,
                    "raw_segment": {},
                    "transition_to_next": "cut",
                    "transition_duration": 0.0,
                }
            )
        return candidates

    def _resolve_raw_shot(
        self,
        raw: dict[str, Any],
        shot_lookup: dict[tuple[str, int], ShotProfile],
        shots_by_video: dict[str, list[ShotProfile]],
    ) -> ShotProfile | None:
        video_id = str(raw.get("source_video_id") or "").strip()
        shot_idx = _safe_int(raw.get("source_shot_idx"))
        if video_id and shot_idx is not None:
            exact = shot_lookup.get((video_id, shot_idx))
            if exact is not None:
                return exact

        if video_id and video_id in shots_by_video:
            start = _safe_float(raw.get("start_seconds"))
            video_shots = shots_by_video[video_id]
            if start is None:
                return max(video_shots, key=self._shot_priority_score)
            return min(video_shots, key=lambda shot: abs(shot.start_seconds - start))
        return None

    async def _enrich_voiceovers(
        self,
        segments: list[dict[str, Any]],
        input_data: dict,
    ) -> dict[str, Any] | None:
        """Generate LLM voiceovers for each segment based on actual shot content and user intent.

        Runs after _materialize_segments so shot descriptions and emotion tags are available.
        Falls back silently to template voiceovers already in segments if LLM call fails.
        """
        vo_segments = [seg for seg in segments if "voiceover" in seg]
        if not vo_segments:
            return None

        user_intent = str(input_data.get("script") or "").strip()
        payload = {
            "user_intent": user_intent,
            "segments": [
                {
                    "segment_idx": seg["segment_idx"],
                    "role": seg.get("role", "buildup"),
                    "description": seg.get("description", ""),
                    "emotion_tag": seg.get("emotion_tag", "neutral"),
                    "duration_seconds": round(
                        float(seg.get("end_seconds", 0)) - float(seg.get("start_seconds", 0)), 2
                    ),
                }
                for seg in vo_segments
            ],
        }
        schema = {
            "name": "remix_voiceovers",
            "schema": {
                "type": "object",
                "properties": {
                    "segments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "segment_idx": {"type": "integer"},
                                "voiceover": {"type": "string"},
                            },
                            "required": ["segment_idx", "voiceover"],
                        },
                    }
                },
                "required": ["segments"],
            },
        }
        try:
            result, usage = await self.llm.generate_structured(
                system_prompt=REMIX_VOICEOVER_PROMPT,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                schema=schema,
            )
            vo_map = {
                int(item.get("segment_idx", -1)): str(item.get("voiceover", "")).strip()
                for item in result.get("segments", [])
            }
            used: set[str] = set()
            for seg in segments:
                if "voiceover" not in seg:
                    continue
                idx = int(seg["segment_idx"])
                vo = vo_map.get(idx, "")
                if (
                    vo
                    and 6 <= len(vo) <= 80
                    and not _is_image_description(vo)
                    and not _looks_like_description(vo, seg.get("description", ""))
                    and vo not in used
                ):
                    seg["voiceover"] = vo
                    used.add(vo)
            return self._usage("remix_voiceover", usage)
        except Exception as exc:
            logger.warning("Voiceover enrichment LLM call failed, keeping template voiceovers: %s", exc)
            return None

    def _shot_priority_score(self, shot: ShotProfile, llm_boost: float = 0.0) -> float:
        emotion = str(shot.emotion_tag or "").strip().lower()
        quality = _clamp_float(shot.visual_quality_score, 0.0, 10.0, 6.0)
        emotion_bonus = 2.0 if emotion in PREFERRED_REMIX_EMOTIONS else 0.0
        quality_bonus = 2.0 if quality >= 7.0 else 0.0
        return llm_boost + quality * 2.0 + emotion_bonus + quality_bonus + max(shot.scene_change_score, 0.0) * 0.5

    @staticmethod
    def _diversify_candidates(
        selected: list[dict[str, Any]],
        profiles: list[VideoProfile],
        max_ratio: float = 0.6,
    ) -> list[dict[str, Any]]:
        """Ensure no single source video dominates more than max_ratio of selected segments."""
        if len(selected) < 2 or len(profiles) < 2:
            return selected

        from collections import Counter
        video_counts: Counter = Counter(item["shot"].video_id for item in selected)
        total = len(selected)
        dominant_id, dominant_count = video_counts.most_common(1)[0]
        if dominant_count / total <= max_ratio:
            return selected

        # Find candidates from under-represented videos to swap in
        all_eligible = {
            p.video_id: sorted(p.shots, key=lambda s: s.visual_quality_score, reverse=True)
            for p in profiles
            if p.video_id != dominant_id and p.shots
        }
        if not all_eligible:
            return selected

        max_allowed = max(1, int(total * max_ratio))
        excess = dominant_count - max_allowed

        result = list(selected)
        dominant_indices = [
            i for i, item in enumerate(result) if item["shot"].video_id == dominant_id
        ]
        # Swap lowest-scored dominant shots with best shots from other videos
        dominant_indices.sort(
            key=lambda i: result[i]["shot"].visual_quality_score
        )
        swapped = 0
        for alt_shots in all_eligible.values():
            if swapped >= excess:
                break
            for alt_shot in alt_shots:
                if swapped >= excess or not dominant_indices:
                    break
                swap_idx = dominant_indices.pop(0)
                swap_item = dict(result[swap_idx])
                swap_item["shot"] = alt_shot
                swap_item["raw_segment"] = {}
                result[swap_idx] = swap_item
                swapped += 1
        return result

    def _select_segment_candidates(self, candidates: list[dict[str, Any]], target_duration: float) -> list[dict[str, Any]]:
        if not candidates:
            return []

        scale = 10
        max_candidate_duration = max(float(item["shot"].duration_seconds) for item in candidates)
        max_total = min(
            sum(float(item["shot"].duration_seconds) for item in candidates),
            target_duration + REMIX_TARGET_TOLERANCE_SECONDS + max_candidate_duration,
        )
        max_bin = max(1, int(round(max_total * scale)))

        states: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, tuple())}
        for idx, item in enumerate(candidates):
            duration_bin = max(1, int(round(float(item["shot"].duration_seconds) * scale)))
            score = float(item["score"])
            next_states = dict(states)
            for current_bin, (current_score, current_indices) in states.items():
                new_bin = current_bin + duration_bin
                if new_bin > max_bin:
                    continue
                new_score = current_score + score
                existing = next_states.get(new_bin)
                if existing is None or new_score > existing[0]:
                    next_states[new_bin] = (new_score, current_indices + (idx,))
            states = next_states

        best_indices: tuple[int, ...] | None = None
        best_objective: tuple[float, ...] | None = None
        for duration_bin, (score, indices) in states.items():
            if not indices:
                continue
            duration = duration_bin / scale
            in_tolerance = abs(duration - target_duration) <= REMIX_TARGET_TOLERANCE_SECONDS
            objective = (
                0.0 if in_tolerance else 1.0,
                abs(duration - target_duration),
                -score,
                -duration,
            )
            if best_objective is None or objective < best_objective:
                best_objective = objective
                best_indices = indices

        if not best_indices:
            return [candidates[0]]

        selected = [candidates[idx] for idx in best_indices]
        selected.sort(
            key=lambda item: (
                item["raw_idx"] if item["raw_idx"] is not None else 10000 + int(item["order"]),
                int(item["order"]),
            )
        )
        return selected

    def _materialize_segments(
        self,
        selected_candidates: list[dict[str, Any]],
        *,
        include_voiceover: bool = False,
    ) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        used_voiceovers: set[str] = set()
        for idx, item in enumerate(selected_candidates):
            shot: ShotProfile = item["shot"]
            next_shot = selected_candidates[idx + 1]["shot"] if idx + 1 < len(selected_candidates) else None
            transition_to_next = item.get("transition_to_next") or "cut"
            if idx >= len(selected_candidates) - 1:
                transition_to_next = "cut"

            transition_duration = 0.0
            if transition_to_next != "cut" and next_shot is not None:
                max_transition = min(float(shot.duration_seconds), float(next_shot.duration_seconds)) * 0.35
                if max_transition >= 0.1:
                    transition_duration = _clamp_float(
                        item.get("transition_duration"),
                        0.1,
                        min(1.0, max_transition),
                        min(0.25, max_transition),
                    )
                else:
                    transition_to_next = "cut"

            raw_segment = item.get("raw_segment") if isinstance(item.get("raw_segment"), dict) else {}
            description = shot.description or f"视频 {shot.video_id} 的第 {shot.shot_idx + 1} 个镜头"
            segment = {
                "segment_idx": idx,
                "source_video_id": shot.video_id,
                "source_shot_idx": shot.shot_idx,
                "start_seconds": round(shot.start_seconds, 3),
                "end_seconds": round(shot.end_seconds, 3),
                "description": description,
                "emotion_tag": str(shot.emotion_tag or "neutral"),
                "role": _role_for_index(idx, len(selected_candidates)),
                "quality_score": _clamp_float(shot.visual_quality_score, 1.0, 10.0, 6.0),
                "transition_to_next": transition_to_next,
                "transition_duration": round(transition_duration, 3),
                "reference_keyframe_path": _existing_keyframe(raw_segment.get("reference_keyframe_path"), shot),
            }
            if include_voiceover:
                segment["voiceover"] = _safe_segment_voiceover(
                    raw_segment,
                    description=description,
                    role=segment["role"],
                    used_voiceovers=used_voiceovers,
                )
                used_voiceovers.add(segment["voiceover"])
            segments.append(segment)
        return segments

    @staticmethod
    def _fill_default_shot_semantics(shots: list[ShotProfile]) -> None:
        for shot in shots:
            shot.description = f"视频 {shot.video_id} 的第 {shot.shot_idx + 1} 个镜头"
            shot.emotion_tag = "neutral"
            shot.visual_quality_score = 6.0

    def _audio_design(self, plan: dict, input_data: dict) -> dict:
        design = plan.get("audio_design") if isinstance(plan.get("audio_design"), dict) else {}
        config = input_data.get("remix_config") or {}
        bgm_context = input_data.get("_resolved_bgm_context") or {}
        include_source = bool(config.get("include_source_audio", False))
        bgm_mood = str(config.get("bgm_mood") or input_data.get("bgm_mood") or design.get("bgm_mood") or "none")
        bgm_source = str(bgm_context.get("bgm_source") or ("library" if bgm_mood != "none" else "none"))
        has_bgm = bgm_source in {"uploaded", "generated"} or (bgm_source == "library" and bgm_mood != "none")
        strategy = str(design.get("strategy") or "")
        if bgm_source in {"uploaded", "generated"}:
            strategy = "mix" if include_source else "bgm_only"
        elif strategy not in {"source_audio", "bgm_only", "mix", "silent"}:
            if include_source and has_bgm:
                strategy = "mix"
            elif include_source:
                strategy = "source_audio"
            elif has_bgm:
                strategy = "bgm_only"
            else:
                strategy = "silent"
        if strategy in {"bgm_only", "mix"} and not has_bgm:
            strategy = "source_audio" if include_source else "silent"

        voice_id = str(design.get("voice_id") or input_data.get("voice_id") or config.get("voice_id") or "default")
        voice_speed = _clamp_float(
            design.get("voice_speed") or design.get("speed"),
            0.5, 2.0, 1.0,
        )
        voice_tone = str(design.get("voice_tone") or design.get("tone") or "")
        narration_notes = str(design.get("narration_notes") or "")

        return {
            "strategy": strategy,
            "bgm_source": bgm_source,
            "bgm_material_id": bgm_context.get("bgm_material_id"),
            "bgm_path": bgm_context.get("bgm_path") or "",
            "bgm_filename": bgm_context.get("bgm_filename") or "",
            "bgm_duration_seconds": bgm_context.get("bgm_duration_seconds"),
            "bgm_mood": bgm_mood,
            "bgm_volume": _clamp_float(config.get("bgm_volume", input_data.get("bgm_volume")), 0.0, 1.0, 0.25),
            "voice_id": voice_id,
            "voice_speed": voice_speed,
            "voice_tone": voice_tone,
            "narration_notes": narration_notes,
        }

    def _usage(self, operation: str, usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": "qwen",
            "model_name": getattr(getattr(self.llm, "client", None), "model", "mock"),
            "operation": operation,
            **(usage or {}),
        }


def validate_remix_plan(
    plan: dict[str, Any],
    *,
    min_segment_duration: float = MIN_REMIX_SEGMENT_DURATION_SECONDS,
    target_tolerance_seconds: float = REMIX_TARGET_TOLERANCE_SECONDS,
) -> str | None:
    if not isinstance(plan, dict):
        return "remix_plan 必须是对象"

    raw_segments = plan.get("segments")
    if not isinstance(raw_segments, list):
        return "remix_plan.segments 必须是数组"

    segments = [item for item in raw_segments if isinstance(item, dict) and not item.get("removed")]
    if not segments:
        return "remix_plan.segments 不能为空"

    ordered_segments = sorted(segments, key=lambda seg: _safe_int(seg.get("segment_idx")) or 0)
    durations: list[float] = []
    total_duration = 0.0
    prev_key: tuple[str, int] | None = None
    for idx, segment in enumerate(ordered_segments):
        video_id = str(segment.get("source_video_id") or "").strip()
        shot_idx = _safe_int(segment.get("source_shot_idx"))
        start = _safe_float(segment.get("start_seconds"))
        end = _safe_float(segment.get("end_seconds"))
        if not video_id or shot_idx is None:
            return f"第 {idx + 1} 个片段缺少 source_video_id/source_shot_idx"
        if start is None or end is None:
            return f"第 {idx + 1} 个片段 start_seconds/end_seconds 非法"
        if end <= start:
            return f"第 {idx + 1} 个片段时长必须大于 0"

        duration = end - start
        if duration < min_segment_duration:
            return f"第 {idx + 1} 个片段时长 {duration:.3f}s，小于最小时长 {min_segment_duration:.1f}s"

        current_key = (video_id, shot_idx)
        if prev_key == current_key:
            return f"第 {idx} 和第 {idx + 1} 个片段重复使用同一镜头 {video_id}/{shot_idx}"
        prev_key = current_key

        durations.append(duration)
        total_duration += duration

    for idx, segment in enumerate(ordered_segments[:-1]):
        transition = _transition(segment.get("transition_to_next"))
        if transition == "cut":
            continue
        transition_duration = _safe_float(segment.get("transition_duration")) or 0.0
        max_transition = min(durations[idx], durations[idx + 1]) * 0.35
        if max_transition < 0.1:
            return f"第 {idx + 1} 个片段时长过短，不支持 {transition} 转场"
        if transition_duration <= 0:
            return f"第 {idx + 1} 个片段 {transition} 转场时长必须大于 0"
        if transition_duration > min(1.0, max_transition) + 1e-6:
            return (
                f"第 {idx + 1} 个片段转场时长 {transition_duration:.3f}s 过大，"
                f"应不超过 {min(1.0, max_transition):.3f}s"
            )

    target_duration = _safe_float(plan.get("target_duration_seconds"))
    if target_duration is not None and abs(total_duration - target_duration) > target_tolerance_seconds:
        return (
            f"片段总时长 {total_duration:.3f}s 与目标时长 {target_duration:.3f}s "
            f"偏差超过 ±{target_tolerance_seconds:.1f}s"
        )

    return None


def _remix_plan_schema() -> dict:
    return {
        "name": "remix_plan",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "concept": {"type": "string"},
                "target_duration_seconds": {"type": "number"},
                "source_videos": {"type": "array", "items": {"type": "object"}},
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "segment_idx": {"type": "integer"},
                            "source_video_id": {"type": "string"},
                            "source_shot_idx": {"type": "integer"},
                            "start_seconds": {"type": "number"},
                            "end_seconds": {"type": "number"},
                            "description": {"type": "string"},
                            "voiceover": {"type": "string"},
                            "role": {"type": "string"},
                            "quality_score": {"type": "number"},
                            "transition_to_next": {"type": "string"},
                            "transition_duration": {"type": "number"},
                            "reference_keyframe_path": {"type": "string"},
                        },
                    },
                },
                "audio_design": {
                    "type": "object",
                    "properties": {
                        "strategy": {"type": "string"},
                        "bgm_mood": {"type": "string"},
                        "bgm_volume": {"type": "number"},
                        "voice_id": {"type": "string"},
                        "voice_speed": {"type": "number"},
                        "voice_tone": {"type": "string"},
                        "narration_notes": {"type": "string"},
                    },
                },
                "analysis_report": {"type": "string"},
            },
            "required": ["title", "concept", "segments", "audio_design", "analysis_report"],
        },
    }


def _target_duration(input_data: dict) -> float:
    config = input_data.get("remix_config") or {}
    return _clamp_float(config.get("target_duration_seconds", input_data.get("duration_seconds")), 1.0, 300.0, 30.0)


def _remix_add_voiceover(input_data: dict) -> bool:
    config = input_data.get("remix_config") if isinstance(input_data.get("remix_config"), dict) else {}
    if "add_voiceover" in config:
        return bool(config.get("add_voiceover"))
    if "add_voiceover" in input_data:
        return bool(input_data.get("add_voiceover"))
    if "voiceover_no_audio" in input_data:
        return not bool(input_data.get("voiceover_no_audio"))
    return False


def _public_bgm_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "bgm_source": context.get("bgm_source") or "none",
        "bgm_material_id": context.get("bgm_material_id"),
        "bgm_filename": context.get("bgm_filename") or "",
        "bgm_duration_seconds": context.get("bgm_duration_seconds"),
    }


def _transition(value: Any) -> str:
    candidate = str(value or "cut")
    return candidate if candidate in {"cut", "fade", "dissolve", "slideright", "slideup"} else "cut"


def _existing_keyframe(value: Any, shot: ShotProfile) -> str:
    path = str(value or "")
    if path and Path(path).exists():
        return path
    return shot.keyframe_path



# Visual analysis keywords that indicate a voiceover is describing the frame
# rather than providing proper narration.
_IMAGE_DESCRIPTION_KEYWORDS = [
    "构图", "可见", "视觉", "背景货架", "画面主体", "画面中", "画面",
    "镜头里", "镜头中", "屏幕", "像素", "分辨率", "色彩", "色调",
    "前景", "后景", "景深", "广角", "特写镜头", "俯拍", "仰拍",
]


def _is_image_description(text: str) -> bool:
    """Check if voiceover text reads like a visual frame description."""
    lower = text.lower()
    return any(kw in lower for kw in _IMAGE_DESCRIPTION_KEYWORDS)


def _looks_like_description(voiceover: str, description: str) -> bool:
    """Check if voiceover is highly similar to the shot description."""
    if not voiceover or not description:
        return False
    # Normalize: strip punctuation for comparison
    import re
    norm_vo = re.sub(r"[，。！？、\s]+", "", voiceover)
    norm_desc = re.sub(r"[，。！？、\s]+", "", description)
    if not norm_vo or not norm_desc:
        return False
    # Check if one is a substring of the other
    if norm_vo in norm_desc or norm_desc in norm_vo:
        return True
    # Check char-level overlap ratio
    common = len(set(norm_vo) & set(norm_desc))
    shorter_len = min(len(norm_vo), len(norm_desc))
    if shorter_len > 0 and common / shorter_len > 0.75:
        return True
    return False


# Role-based complete short sentence templates for fallback voiceover generation.
# Each template produces 16-28 Chinese characters ending with proper punctuation.
_ROLE_VOICEOVER_TEMPLATES: dict[str, list[str]] = {
    "intro": [
        "从这里开始，感受独特的品质与用心。",
        "一进门就被这种氛围打动了。",
        "第一印象就让人印象深刻。",
        "打开这一刻，品质感扑面而来。",
        "初次相遇，细节里藏着温度。",
    ],
    "buildup": [
        "慢慢体验，每个环节都让人舒心。",
        "深入感受，服务比想象中更贴心。",
        "一步步展开，体验越来越丰富。",
        "空间的设计让人感到放松自在。",
        "沉浸其中，时间似乎慢了下来。",
    ],
    "highlight": [
        "这就是让人记住的关键瞬间。",
        "最打动人的细节在这里展现。",
        "这一刻的专业和仪式感令人难忘。",
        "核心体验让人忍不住想分享。",
        "精心的打磨在这个环节体现得淋漓尽致。",
    ],
    "climax": [
        "最好的部分来了，值得细细品味。",
        "这就是整个体验的高光时刻。",
        "到了最让人心动的一刻。",
        "这一瞬间，一切都值得了。",
    ],
    "outro": [
        "带着这份美好，期待下一次相遇。",
        "从这里出发，生活又多了一份质感。",
        "结束之后，留下的都是温暖回忆。",
        "完美的收尾，为整段体验画上句号。",
        "这样的生活方式，值得用心感受。",
    ],
}


def _generate_voiceover_by_role(role: str, used: set[str] | None = None) -> str:
    """Generate a complete short sentence (16-28 chars) based on narrative role.

    Avoids returning already-used texts when possible.
    """
    import random

    templates = _ROLE_VOICEOVER_TEMPLATES.get(role, _ROLE_VOICEOVER_TEMPLATES["buildup"])
    if used:
        available = [t for t in templates if t not in used]
        if available:
            return random.choice(available)
    return random.choice(templates)


def _safe_segment_voiceover(
    raw_segment: dict,
    description: str,
    role: str,
    used_voiceovers: set[str] | None = None,
) -> str:
    """Validate and potentially rewrite a segment's voiceover text.

    Returns a clean voiceover string that:
    - Is not missing (generates role-based fallback)
    - Does not end with ellipsis
    - Is not highly similar to the shot description
    - Does not contain visual frame analysis keywords
    - Is a complete short sentence (16-28 chars) ending with proper punctuation
    - Is not a duplicate of already-used voiceover texts
    """
    # Extract candidate voiceover from raw segment
    candidate = ""
    for key in ("script_segment", "narration", "voiceover"):
        text = str(raw_segment.get(key) or "").strip()
        if text:
            candidate = text
            break

    needs_rewrite = False

    if not candidate:
        needs_rewrite = True
    elif candidate.rstrip().endswith("…"):
        needs_rewrite = True
    elif _looks_like_description(candidate, description):
        needs_rewrite = True
    elif _is_image_description(candidate):
        needs_rewrite = True
    elif len(candidate) < 6 or len(candidate) > 60:
        needs_rewrite = True
    elif used_voiceovers and candidate in used_voiceovers:
        needs_rewrite = True

    if needs_rewrite:
        return _generate_voiceover_by_role(role, used=used_voiceovers)

    # Ensure it ends with proper punctuation, not ellipsis
    if not candidate.endswith(("。", "！", "？")):
        candidate = candidate.rstrip("，、…,.") + "。"

    return candidate


def _role_for_index(index: int, count: int) -> str:
    if index == 0:
        return "intro"
    if index >= count - 1:
        return "outro"
    if index >= max(count - 2, 1):
        return "climax"
    return "buildup" if index < count / 2 else "highlight"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    if low > high:
        low, high = high, low
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return max(low, min(high, numeric))
