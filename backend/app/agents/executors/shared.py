from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.models.pipeline import PipelineRun
from app.services.media_utils import run_subprocess
from app.services.background_template_learning import learn_background_template_from_run
from app.services.video_editing.helpers import _probe_duration

logger = logging.getLogger(__name__)

# Chinese character TTS speed: ~4.5 chars per second at speed=1.0
_VOICEOVER_CHARS_PER_SECOND = 4.5
_VOICEOVER_CAP_FACTOR = 1.2
_VOICEOVER_CAP_MAX_EXTRA_SECONDS = 6.0

# 构建各个agent输入
class PipelineExecutorSupportMixin:
    """Shared helpers for pipeline-style executors.

    Executors are expected to provide these attributes:

    - ``orchestrator``
    - ``prompt_engineer``
    - ``audio_agent``
    - ``video_gen_agent``
    - ``video_editor``
    - ``qa_reviewer``
    - ``db_session_factory``
    """

    @staticmethod
    def _build_shot_script(shot_prompts: list[dict[str, Any]]) -> str:
        return "\n".join(
            str(item.get("script_segment") or "").strip()
            for item in shot_prompts
            if isinstance(item, dict) and str(item.get("script_segment") or "").strip()
        )

    @staticmethod
    def _remix_config(input_config: dict[str, Any]) -> dict[str, Any]:
        remix_config = input_config.get("remix_config")
        return remix_config if isinstance(remix_config, dict) else {}

    def should_run_remix_audio(self, input_config: dict[str, Any]) -> bool:
        remix_config = self._remix_config(input_config)
        if "add_voiceover" in remix_config:
            return bool(remix_config.get("add_voiceover"))
        if "add_voiceover" in input_config:
            return bool(input_config.get("add_voiceover"))
        if "voiceover_no_audio" in input_config:
            return not bool(input_config.get("voiceover_no_audio"))
        return False

    @staticmethod
    def _build_remix_script(remix_plan: dict[str, Any], input_config: dict[str, Any]) -> str:
        remix_config = input_config.get("remix_config") if isinstance(input_config.get("remix_config"), dict) else {}
        explicit_script = str(remix_config.get("voiceover_script") or input_config.get("voiceover_script") or "").strip()
        if explicit_script:
            return explicit_script

        lines: list[str] = []
        for segment in remix_plan.get("segments", []):
            if not isinstance(segment, dict) or segment.get("removed"):
                continue
            text = (
                segment.get("voiceover")
                or segment.get("script_segment")
                or segment.get("narration")
                or ""
            )
            text = str(text or "").strip()
            if text:
                lines.append(text)
        if lines:
            return "\n".join(lines)
        # Use the _voiceover_script_for_segments helper as last resort
        return PipelineExecutorSupportMixin._voiceover_script_for_segments(
            remix_plan.get("segments", [])
        ) or str(input_config.get("script") or "").strip()

    @staticmethod
    def _remix_voice_params(remix_plan: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        audio_design = remix_plan.get("audio_design") if isinstance(remix_plan.get("audio_design"), dict) else {}
        return {
            "voice_id": audio_design.get("voice_id") or input_config.get("voice_id", "default"),
            "speed": audio_design.get("voice_speed") or audio_design.get("speed") or 1.0,
            "tone": audio_design.get("voice_tone") or audio_design.get("tone"),
        }

    @staticmethod
    def _estimate_voiceover_duration_seconds(text: str, speed: float = 1.0) -> float:
        """Estimate TTS duration from Chinese text length and speed."""
        if not text:
            return 0.0
        char_count = len(text)
        return char_count / max(_VOICEOVER_CHARS_PER_SECOND * speed, 1.0)

    @staticmethod
    def _compute_voiceover_cap_seconds(segments: list[dict]) -> float:
        """Compute the maximum allowed voiceover duration from video segments.

        cap = min(video_total * 1.2, video_total + 6.0)
        """
        video_total = sum(
            _segment_duration(seg)
            for seg in segments
            if isinstance(seg, dict) and not seg.get("removed")
        )
        if video_total <= 0:
            return 30.0
        return min(video_total * _VOICEOVER_CAP_FACTOR, video_total + _VOICEOVER_CAP_MAX_EXTRA_SECONDS)

    @staticmethod
    def _compress_voiceover_segments(
        segments: list[dict],
        original_script: str,
        estimated_duration: float,
        cap_seconds: float,
        speed: float = 1.0,
    ) -> tuple[str, bool]:
        """Compress voiceover text to fit within the duration cap.

        Returns (compressed_script, was_compressed).

        Never truncates with ellipsis — instead rewrites over-budget segments
        as complete short sentences using role-based fallback generation.
        """
        if estimated_duration <= cap_seconds or not segments:
            return original_script, False

        # Target char count based on cap
        target_chars = int(cap_seconds * _VOICEOVER_CHARS_PER_SECOND * max(speed, 0.5))
        original_chars = len(original_script)
        if original_chars <= target_chars:
            return original_script, False

        # Collect voiceover segments with their metadata
        voice_segments: list[dict] = []
        total_orig_chars = 0
        for seg in segments:
            if not isinstance(seg, dict) or seg.get("removed"):
                continue
            voiceover = str(
                seg.get("voiceover")
                or seg.get("script_segment")
                or seg.get("narration")
                or ""
            ).strip()
            if not voiceover:
                continue
            voice_segments.append({
                "voiceover": voiceover,
                "role": str(seg.get("role") or "buildup"),
                "orig_len": len(voiceover),
            })
            total_orig_chars += len(voiceover)

        if not voice_segments:
            return original_script, False

        ratio = target_chars / max(total_orig_chars, 1)
        compressed_lines: list[str] = []
        for vs in voice_segments:
            budget = max(12, int(vs["orig_len"] * ratio))
            if vs["orig_len"] <= budget:
                compressed_lines.append(vs["voiceover"])
            else:
                # Rewrite as a compact complete sentence instead of truncating
                compressed_lines.append(
                    _rewrite_voiceover_compact(vs["voiceover"], vs["role"], budget)
                )
        return "\n".join(compressed_lines) if compressed_lines else original_script, True

    @staticmethod
    def _voiceover_script_for_segments(segments: list[dict]) -> str:
        """Build concatenated voiceover script from segments."""
        lines: list[str] = []
        for seg in segments:
            if not isinstance(seg, dict) or seg.get("removed"):
                continue
            text = str(
                seg.get("voiceover")
                or seg.get("script_segment")
                or seg.get("narration")
                or ""
            ).strip()
            if text:
                lines.append(text)
        return "\n".join(lines)

    def build_audio_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        remix_plan = artifacts.get("remix_plan")
        if isinstance(remix_plan, dict) and self.should_run_remix_audio(input_config):
            return {
                "script": self._build_remix_script(remix_plan, input_config),
                "voice_params": self._remix_voice_params(remix_plan, input_config),
                "no_audio": False,
                "voiceover_no_audio": False,
            }

        prompt_plan = artifacts.get("prompt_plan", {})
        orchestrator_plan = artifacts.get("orchestrator_plan", {})
        shot_prompts = prompt_plan.get("shot_prompts", [])
        shot_script = self._build_shot_script(shot_prompts)
        voiceover_no_audio = input_config.get("voiceover_no_audio", input_config.get("no_audio", False))
        return {
            "script": shot_script or orchestrator_plan.get("script") or input_config.get("script", ""),
            "voice_params": prompt_plan.get("voice_params", {}),
            # voiceover_no_audio controls TTS/subtitle generation only.
            # Falls back to legacy no_audio for backwards compatibility.
            "no_audio": voiceover_no_audio,
            "voiceover_no_audio": voiceover_no_audio,
        }

    def build_video_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        prompt_plan = artifacts.get("prompt_plan", {})
        orchestrator_plan = artifacts.get("orchestrator_plan", {})
        source_images = orchestrator_plan.get("source_images") or orchestrator_plan.get("image_context") or []

        # Build fallback lookup by image_idx / shot_idx for backward compat
        source_by_idx = {}
        for item in source_images:
            if not isinstance(item, dict):
                continue
            for key in ("image_idx", "shot_idx"):
                if item.get(key) is not None:
                    source_by_idx[item[key]] = item
                    break

        shot_prompts = []
        for shot in prompt_plan.get("shot_prompts", []):
            if not isinstance(shot, dict):
                continue
            enriched = dict(shot)
            # Trust director's image_path when already set.
            # Fall back to source_images lookup only when image_path is missing.
            if not enriched.get("image_path"):
                lookup_idx = enriched.get("source_image_idx", enriched.get("shot_idx"))
                source = source_by_idx.get(lookup_idx)
                if source and source.get("image_path"):
                    enriched["image_path"] = source["image_path"]
                    enriched.setdefault("source_image", source)
                    enriched.setdefault("image_content", source.get("image_content", ""))
            shot_prompts.append(enriched)

        video_model_no_audio = input_config.get("video_model_no_audio", input_config.get("no_audio", True))
        return {
            "shot_prompts": shot_prompts,
            "source_images": source_images,
            # video_model_no_audio controls model-generated audio track only.
            # Falls back to legacy no_audio for backwards compatibility.
            "no_audio": video_model_no_audio,
            "video_model_no_audio": video_model_no_audio,
            "generation_model": input_config.get("generation_model"),
            "platform": orchestrator_plan.get("platform") or input_config.get("platform", "generic"),
        }

    def build_editor_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        video_clips = artifacts.get("video_clips", {})
        audio = artifacts.get("audio", {})
        prompt_plan = artifacts.get("prompt_plan", {})
        orchestrator_plan = artifacts.get("orchestrator_plan", {})
        duration_source = prompt_plan.get("shot_prompts") or orchestrator_plan.get("shots", [])
        shot_durations = [
            float(shot["duration_seconds"])
            for shot in duration_source
            if isinstance(shot, dict) and shot.get("duration_seconds") is not None
        ]
        return {
            "video_clips": video_clips.get("video_clips", []),
            "audio_path": audio.get("audio_path", ""),
            "subtitle_path": audio.get("subtitle_path", ""),
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "duration_mode": input_config.get("duration_mode", "fixed"),
            "shot_durations": shot_durations,
            "transition": input_config.get("transition", "none"),
            "transition_duration": input_config.get("transition_duration", 0.5),
            "bgm_mood": input_config.get("bgm_mood", "none"),
            "bgm_volume": input_config.get("bgm_volume", 0.15),
            "watermark_path": input_config.get("watermark_path"),
            # Forward video_model_no_audio so the editor knows whether to preserve
            # the model-generated audio track (when no TTS voiceover is present).
            "video_model_no_audio": input_config.get("video_model_no_audio", input_config.get("no_audio", True)),
        }

    def build_qa_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        return {
            "shot_prompts": artifacts.get("prompt_plan", {}).get("shot_prompts", []),
            "video_clips": artifacts.get("video_clips", {}).get("video_clips", []),
            "audio": artifacts.get("audio", {}),
            "final_video": artifacts.get("final_video", {}),
            "input_config": input_config,
        }

    def build_remix_assembler_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        return {
            "remix_plan": artifacts.get("remix_plan", {}),
            "audio": artifacts.get("audio", {}),
            "input_config": input_config,
        }

    async def run_remix_audio_if_needed(self, context, input_config: dict[str, Any]) -> dict[str, Any] | None:
        if not self.should_run_remix_audio(input_config):
            return None
        audio_input = self.build_audio_input(context.artifacts, input_config)
        existing_audio = context.artifacts.get("audio")
        if self._can_reuse_remix_audio(existing_audio, audio_input):
            return existing_audio

        result = await self.audio_agent.run(context, audio_input)
        if not result.success:
            await self._update_run(
                context.pipeline_run_id,
                status="running",
                current_agent="audio_subtitle",
            )
            raise RuntimeError(f"Audio Agent failed: {result.error}")
        output_data = dict(result.output_data)
        output_data["_remix_script"] = audio_input.get("script", "")
        context.artifacts["audio"] = output_data
        await context.save_checkpoint()
        return output_data

    @staticmethod
    def _can_reuse_remix_audio(existing_audio: Any, audio_input: dict[str, Any]) -> bool:
        if not isinstance(existing_audio, dict):
            return False
        audio_path = str(existing_audio.get("audio_path") or "").strip()
        if not audio_path or not os.path.exists(audio_path):
            return False
        if existing_audio.get("_reuse_for_audio_aligned_remix"):
            return True
        return str(existing_audio.get("_remix_script") or "") == str(audio_input.get("script") or "")

    async def prepare_remix_after_audio(self, context, input_config: dict[str, Any]) -> dict[str, Any] | None:
        audio = await self.run_remix_audio_if_needed(context, input_config)
        if not audio:
            return None

        audio_duration = await self._remix_audio_duration_seconds(audio)
        if not audio_duration:
            return None

        # Check if audio is significantly over the voiceover cap
        remix_plan = context.artifacts.get("remix_plan")
        if isinstance(remix_plan, dict):
            segments = [
                seg for seg in (remix_plan.get("segments") or [])
                if isinstance(seg, dict) and not seg.get("removed")
            ]
            voiceover_cap = self._compute_voiceover_cap_seconds(segments) if segments else 30.0

            if audio_duration > voiceover_cap + 0.5:
                # Audio exceeds cap: attempt voiceover compression and TTS retry
                retry_result = await self._retry_tts_with_compressed_voiceover(
                    context, input_config, audio_duration, voiceover_cap, remix_plan, segments
                )
                if retry_result:
                    return retry_result
                # If retry didn't fully resolve, mark as compressed and continue
                if isinstance(context.artifacts.get("remix_plan"), dict):
                    timing = context.artifacts["remix_plan"].setdefault("timing_adjustment", {})
                    timing["strategy"] = "voiceover_compressed"

        alignment = self.align_remix_plan_to_audio(context.artifacts, audio_duration)
        if alignment.get("requires_replan"):
            return await self._rerun_remix_planner_for_audio_duration(context, input_config, audio_duration)

        if alignment.get("changed"):
            context.artifacts["remix_plan"] = alignment["remix_plan"]
            await context.save_checkpoint()
        return None

    async def _retry_tts_with_compressed_voiceover(
        self,
        context,
        input_config: dict[str, Any],
        audio_duration: float,
        voiceover_cap: float,
        remix_plan: dict,
        segments: list[dict],
    ) -> dict[str, Any] | None:
        """Compress voiceover text and retry TTS once when audio exceeds cap."""
        audio = context.artifacts.get("audio")
        if not isinstance(audio, dict):
            return None

        original_script = audio.get("_remix_script") or self._voiceover_script_for_segments(segments)
        voice_params = self._remix_voice_params(remix_plan, input_config)
        speed = float(voice_params.get("speed", 1.0))
        estimated = self._estimate_voiceover_duration_seconds(original_script, speed)

        compressed_script, was_compressed = self._compress_voiceover_segments(
            segments, original_script, estimated, voiceover_cap, speed
        )
        if not was_compressed:
            return None

        logger.info(
            "Voiceover %s chars (est %.1fs) exceeds cap %.1fs, compressing to %s chars",
            len(original_script), estimated, voiceover_cap, len(compressed_script),
        )

        # Update audio input with compressed script
        compressed_audio_input = {
            "script": compressed_script,
            "voice_params": voice_params,
            "no_audio": False,
            "voiceover_no_audio": False,
        }

        try:
            result = await self.audio_agent.run(context, compressed_audio_input)
            if result.success:
                output_data = dict(result.output_data)
                output_data["_remix_script"] = compressed_script
                output_data["_voiceover_compressed"] = True
                context.artifacts["audio"] = output_data

                # Update plan's voiceover text to match compressed
                compressed_lines = compressed_script.split("\n")
                compressed_idx = 0
                for seg in segments:
                    if not isinstance(seg, dict) or seg.get("removed"):
                        continue
                    has_voiceover = any(
                        seg.get(k) for k in ("voiceover", "script_segment", "narration")
                    )
                    if has_voiceover and compressed_idx < len(compressed_lines):
                        seg["voiceover"] = compressed_lines[compressed_idx]
                        compressed_idx += 1

                context.artifacts["remix_plan"] = remix_plan
                await context.save_checkpoint()

                # Check new audio duration
                new_duration = await self._remix_audio_duration_seconds(output_data)
                if new_duration and new_duration <= voiceover_cap + 0.25:
                    logger.info("Compressed voiceover now fits: %.1fs <= %.1fs", new_duration, voiceover_cap)
                    return None
        except Exception as exc:
            logger.warning("TTS retry with compressed voiceover failed: %s", exc)

        # Mark compressed even if retry had issues
        remix_plan["timing_adjustment"] = {
            "original_duration_seconds": round(sum(_segment_duration(seg) for seg in segments), 3),
            "audio_duration_seconds": round(audio_duration, 3),
            "voiceover_cap_seconds": round(voiceover_cap, 3),
            "strategy": "voiceover_compressed",
        }
        context.artifacts["remix_plan"] = remix_plan
        await context.save_checkpoint()
        return None

    async def _remix_audio_duration_seconds(self, audio: dict[str, Any]) -> float | None:
        audio_path = str(audio.get("audio_path") or "").strip()
        if audio_path and os.path.exists(audio_path):
            duration = await _probe_duration(settings.FFMPEG_BIN, audio_path, run_subprocess)
            if duration:
                return float(duration)
        duration_ms = audio.get("duration_ms")
        try:
            duration = float(duration_ms) / 1000
        except (TypeError, ValueError):
            return None
        return duration if duration > 0 else None

    def align_remix_plan_to_audio(self, artifacts: dict[str, Any], audio_duration_seconds: float) -> dict[str, Any]:
        remix_plan = artifacts.get("remix_plan")
        if not isinstance(remix_plan, dict):
            return {"changed": False}

        segments = [
            segment
            for segment in sorted(remix_plan.get("segments") or [], key=lambda item: int(item.get("segment_idx", 0)))
            if isinstance(segment, dict) and not segment.get("removed")
        ]
        if not segments:
            return {"changed": False}

        durations = [_segment_duration(segment) for segment in segments]
        original_duration = sum(durations)
        if original_duration <= 0:
            return {"changed": False}

        voiceover_cap = self._compute_voiceover_cap_seconds(segments)
        source_durations = self._remix_source_durations(artifacts)
        max_durations = [
            self._max_segment_duration(segment, source_durations, fallback=duration)
            for segment, duration in zip(segments, durations)
        ]
        max_total = sum(max_durations)

        # Video-priority: target is capped at voiceover_cap, not full audio duration
        target_duration = min(max(float(audio_duration_seconds), 1.0), voiceover_cap)

        # Only replan when material itself is insufficient for reasonable cap
        if target_duration > max_total + 0.05:
            return {
                "changed": False,
                "requires_replan": True,
                "audio_duration_seconds": round(audio_duration_seconds, 3),
                "voiceover_cap_seconds": round(voiceover_cap, 3),
                "max_available_duration_seconds": round(max_total, 3),
            }

        if abs(target_duration - original_duration) <= 0.25:
            return {"changed": False}

        adjusted_plan = dict(remix_plan)
        all_segments = [dict(segment) if isinstance(segment, dict) else segment for segment in remix_plan.get("segments", [])]
        segment_by_idx = {
            int(segment.get("segment_idx", idx)): segment
            for idx, segment in enumerate(all_segments)
            if isinstance(segment, dict)
        }

        kept_segments = segments
        strategy = "extended"
        if target_duration < original_duration:
            keep_count = max(1, min(len(segments), int(target_duration // 1.0)))
            kept_segments = segments[:keep_count]
            kept_durations = durations[:keep_count]
            target_duration = max(float(keep_count), target_duration)
            new_durations = _scale_durations_with_minimum(kept_durations, target_duration, minimum=1.0)
            strategy = "compressed" if keep_count == len(segments) else "compressed_removed_tail"
            kept_ids = {int(segment.get("segment_idx", idx)) for idx, segment in enumerate(kept_segments)}
            for idx, segment in enumerate(segments):
                segment_idx = int(segment.get("segment_idx", idx))
                if segment_idx not in kept_ids and segment_idx in segment_by_idx:
                    segment_by_idx[segment_idx]["removed"] = True
        else:
            new_durations = _extend_durations_with_capacity(durations, max_durations, target_duration)

        for idx, (segment, new_duration) in enumerate(zip(kept_segments, new_durations)):
            segment_idx = int(segment.get("segment_idx", idx))
            target_segment = segment_by_idx.get(segment_idx)
            if not isinstance(target_segment, dict):
                continue
            start = _safe_float(target_segment.get("start_seconds"), 0.0)
            max_duration = self._max_segment_duration(target_segment, source_durations, fallback=new_duration)
            clamped_duration = min(max(float(new_duration), 1.0), max_duration)
            target_segment["start_seconds"] = round(start, 3)
            target_segment["end_seconds"] = round(start + clamped_duration, 3)

        adjusted_segments = [segment for segment in all_segments if isinstance(segment, dict)]
        adjusted_duration = sum(
            _segment_duration(segment)
            for segment in adjusted_segments
            if not segment.get("removed")
        )
        adjusted_plan["segments"] = adjusted_segments
        adjusted_plan["target_duration_seconds"] = round(adjusted_duration, 3)
        adjusted_plan["timing_adjustment"] = {
            "original_duration_seconds": round(original_duration, 3),
            "audio_duration_seconds": round(audio_duration_seconds, 3),
            "voiceover_cap_seconds": round(voiceover_cap, 3),
            "adjusted_duration_seconds": round(adjusted_duration, 3),
            "strategy": strategy,
        }
        return {"changed": True, "remix_plan": adjusted_plan}

    def _remix_source_durations(self, artifacts: dict[str, Any]) -> dict[str, float]:
        profiles = artifacts.get("video_profiles")
        if not isinstance(profiles, list):
            remix_planner_output = artifacts.get("remix_planner")
            if isinstance(remix_planner_output, dict):
                profiles = remix_planner_output.get("video_profiles")
        source_durations: dict[str, float] = {}
        for profile in profiles or []:
            if not isinstance(profile, dict):
                continue
            video_id = str(profile.get("video_id") or "").strip()
            if not video_id:
                continue
            duration = _safe_float(profile.get("duration_seconds"), 0.0)
            if duration > 0:
                source_durations[video_id] = duration
        return source_durations

    @staticmethod
    def _max_segment_duration(segment: dict[str, Any], source_durations: dict[str, float], *, fallback: float) -> float:
        start = _safe_float(segment.get("start_seconds"), 0.0)
        source_duration = source_durations.get(str(segment.get("source_video_id") or ""))
        if source_duration is None:
            return max(fallback, 1.0)
        return max(source_duration - start, 0.0)

    async def _rerun_remix_planner_for_audio_duration(
        self,
        context,
        input_config: dict[str, Any],
        audio_duration_seconds: float,
    ) -> dict[str, Any]:
        if getattr(self, "remix_planner", None) is None:
            raise RuntimeError("旁白音频超过当前混剪片段可覆盖时长，且 remix_planner 未配置，无法重新规划")

        # Use voiceover cap as target, not full audio duration (video-priority)
        remix_plan = context.artifacts.get("remix_plan")
        segments = [
            seg for seg in (remix_plan.get("segments") or [])
            if isinstance(seg, dict) and not seg.get("removed")
        ] if isinstance(remix_plan, dict) else []
        voiceover_cap = self._compute_voiceover_cap_seconds(segments) if segments else audio_duration_seconds
        target_for_replan = min(audio_duration_seconds, voiceover_cap)

        adjusted_config = dict(input_config)
        remix_config = dict(adjusted_config.get("remix_config") or {})
        remix_config["target_duration_seconds"] = round(float(target_for_replan), 3)
        adjusted_config["remix_config"] = remix_config
        feedback = str(adjusted_config.get("remix_adjustment_feedback") or "").strip()
        auto_feedback = "系统检测到素材不足以覆盖合理旁白时长，请按此目标时长重新选择更多源视频片段。"
        adjusted_config["remix_adjustment_feedback"] = f"{feedback}\n{auto_feedback}".strip()

        audio = context.artifacts.get("audio")
        if isinstance(audio, dict):
            audio["_reuse_for_audio_aligned_remix"] = True
            audio["_remix_audio_duration_seconds"] = round(float(target_for_replan), 3)

        result = await self.remix_planner.run(context, adjusted_config)
        if not result.success:
            raise RuntimeError(f"Remix Planner failed after audio duration check: {result.error}")
        context.artifacts["remix_plan"] = result.output_data.get("remix_plan", {})
        context.artifacts["remix_planner"] = result.output_data
        await context.save_checkpoint()
        await self._update_run(
            context.pipeline_run_id,
            status="waiting_remix_confirmation",
            current_agent="remix_planner",
            input_config=json.dumps(adjusted_config, ensure_ascii=False),
        )
        return {
            "status": "waiting_remix_confirmation",
            "reason": "voiceover_audio_exceeds_selected_segments",
            "audio_duration_seconds": round(float(audio_duration_seconds), 3),
            "voiceover_cap_seconds": round(voiceover_cap, 3),
        }

    def get_agent_map(self) -> dict[str, object]:
        agents = {
            "orchestrator": self.orchestrator,
            "prompt_engineer": self.prompt_engineer,
            "audio_subtitle": self.audio_agent,
            "video_generator": self.video_gen_agent,
            "video_editor": self.video_editor,
        }
        remix_planner = getattr(self, "remix_planner", None)
        if remix_planner is not None:
            agents["remix_planner"] = remix_planner
        remix_assembler = getattr(self, "remix_assembler", None)
        if remix_assembler is not None:
            agents["remix_assembler"] = remix_assembler
        if self.qa_reviewer is not None:
            agents["qa_reviewer"] = self.qa_reviewer
        return agents

    @staticmethod
    def get_agent_to_artifact_key() -> dict[str, str]:
        return {
            "orchestrator": "orchestrator_plan",
            "prompt_engineer": "prompt_plan",
            "audio_subtitle": "audio",
            "video_generator": "video_clips",
            "video_editor": "final_video",
            "qa_reviewer": "qa_report",
            "remix_assembler": "final_video",
        }

    def build_agent_input(self, agent_name: str, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        if agent_name == "orchestrator":
            return input_config
        if agent_name == "prompt_engineer":
            orchestrator_plan = artifacts.get("orchestrator_plan", {})
            # Merge key input_config fields so director has full context even if
            # orchestrator_plan was built by a different path (e.g. replication).
            merged = dict(orchestrator_plan)
            for key in ("platform", "style", "duration_seconds", "target_duration_seconds",
                        "duration_mode", "voice_id", "background_context"):
                if key not in merged and key in input_config:
                    merged[key] = input_config[key]
            # Ensure voice_config is present
            if "voice_config" not in merged:
                merged["voice_config"] = {
                    "voice_id": input_config.get("voice_id", "default"),
                    "speed": 1.0,
                }
            return merged
        if agent_name == "audio_subtitle":
            return self.build_audio_input(artifacts, input_config)
        if agent_name == "video_generator":
            return self.build_video_input(artifacts, input_config)
        if agent_name == "video_editor":
            return self.build_editor_input(artifacts, input_config)
        if agent_name == "qa_reviewer":
            return self.build_qa_input(artifacts, input_config)
        if agent_name == "remix_planner":
            return input_config
        if agent_name == "remix_assembler":
            return self.build_remix_assembler_input(artifacts, input_config)
        return {}

    async def _maybe_learn_background_template(self, context, input_config: dict[str, Any]) -> None:
        llm = getattr(self.prompt_engineer, "llm", None) or getattr(self.prompt_engineer, "llm_service", None)
        if llm is None:
            return
        await learn_background_template_from_run(
            db_session_factory=self.db_session_factory,
            llm=llm,
            pipeline_run_id=context.pipeline_run_id,
            input_config=input_config,
            artifacts=context.artifacts,
        )

    async def _update_run(self, pipeline_run_id: str, **kwargs: Any) -> None:
        kwargs["updated_at"] = datetime.now(timezone.utc)
        async with self.db_session_factory() as session:
            run = await session.get(PipelineRun, pipeline_run_id)
            if run:
                if run.status == "cancelled" and kwargs.get("status") != "cancelled":
                    return
                for key, value in kwargs.items():
                    setattr(run, key, value)
                await session.commit()


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _segment_duration(segment: dict[str, Any]) -> float:
    start = _safe_float(segment.get("start_seconds"), 0.0)
    end = _safe_float(segment.get("end_seconds"), start)
    return max(end - start, 0.0)


def _scale_durations_with_minimum(durations: list[float], target: float, *, minimum: float) -> list[float]:
    if not durations:
        return []
    target = max(target, len(durations) * minimum)
    total = sum(durations)
    if total <= 0:
        return [target / len(durations)] * len(durations)

    scaled = [max(minimum, target * duration / total) for duration in durations]
    excess = sum(scaled) - target
    while excess > 1e-6:
        adjustable = [idx for idx, value in enumerate(scaled) if value > minimum + 1e-6]
        if not adjustable:
            break
        adjustable_total = sum(scaled[idx] - minimum for idx in adjustable)
        if adjustable_total <= 0:
            break
        for idx in adjustable:
            room = scaled[idx] - minimum
            reduction = min(room, excess * room / adjustable_total)
            scaled[idx] -= reduction
        new_excess = sum(scaled) - target
        if new_excess >= excess:
            break
        excess = new_excess
    return _absorb_rounding_delta(scaled, target, minimum=minimum)


def _extend_durations_with_capacity(durations: list[float], max_durations: list[float], target: float) -> list[float]:
    adjusted = [float(duration) for duration in durations]
    remaining = max(target - sum(adjusted), 0.0)
    while remaining > 1e-6:
        capacities = [max(max_duration - duration, 0.0) for duration, max_duration in zip(adjusted, max_durations)]
        capacity_total = sum(capacities)
        if capacity_total <= 0:
            break
        for idx, capacity in enumerate(capacities):
            if capacity <= 0:
                continue
            addition = min(capacity, remaining * capacity / capacity_total)
            adjusted[idx] += addition
        new_remaining = max(target - sum(adjusted), 0.0)
        if new_remaining >= remaining:
            break
        remaining = new_remaining
    return _absorb_rounding_delta(adjusted, min(target, sum(max_durations)), minimum=1.0, maximums=max_durations)


def _absorb_rounding_delta(
    durations: list[float],
    target: float,
    *,
    minimum: float,
    maximums: list[float] | None = None,
) -> list[float]:
    rounded = [round(duration, 3) for duration in durations]
    delta = round(target - sum(rounded), 3)
    if abs(delta) < 0.001:
        return rounded

    indices = range(len(rounded) - 1, -1, -1)
    for idx in indices:
        maximum = maximums[idx] if maximums is not None else None
        candidate = rounded[idx] + delta
        if candidate < minimum:
            continue
        if maximum is not None and candidate > maximum:
            continue
        rounded[idx] = round(candidate, 3)
        break
    return rounded


# Compact role-based voiceover templates (12-20 chars) for compression rewrites.
# These are shorter than the normal 16-28 char fallback to fit tight budgets.
_COMPACT_VOICEOVER_TEMPLATES: dict[str, list[str]] = {
    "intro": [
        "开场就感受到品质。",
        "第一印象让人安心。",
        "打开瞬间被打动。",
        "进门就感到用心。",
    ],
    "buildup": [
        "每个细节都很贴心。",
        "体验比预期更舒适。",
        "服务让人感到温暖。",
        "空间设计充满巧思。",
    ],
    "highlight": [
        "这一刻值得被记住。",
        "专业细节让人信服。",
        "核心环节体现了水准。",
        "最打动人的瞬间。",
    ],
    "climax": [
        "高潮部分令人难忘。",
        "这就是最精彩之处。",
        "一切凝聚在这一刻。",
    ],
    "outro": [
        "结束后的回味悠长。",
        "带着满足期待再见。",
        "完美收尾让人回味。",
        "留下温暖的记忆。",
    ],
}


def _rewrite_voiceover_compact(original: str, role: str, budget_chars: int) -> str:
    """Rewrite voiceover as a complete short sentence fitting within budget.

    Never produces truncated text or ellipsis — always returns a full sentence
    ending with proper punctuation (。/！/？).
    """
    import random

    templates = _COMPACT_VOICEOVER_TEMPLATES.get(role, _COMPACT_VOICEOVER_TEMPLATES["buildup"])
    # Find templates that fit within budget
    fitting = [t for t in templates if len(t) <= budget_chars]
    if not fitting:
        # Even the shortest template exceeds budget — use the shortest available
        fitting = [min(templates, key=len)]
    # If budget is generous enough, prefer slightly longer sentences for variety
    if budget_chars >= 18:
        longer = [t for t in fitting if len(t) >= 12]
        if longer:
            fitting = longer
    return random.choice(fitting)
