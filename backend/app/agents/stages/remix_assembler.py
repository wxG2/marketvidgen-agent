from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from app.agents.core.base import AgentContext, AgentResult, BaseAgent
from app.agents.stages.remix_planner import validate_remix_plan
from app.core.config import settings
from app.models.video_upload import VideoUpload
from app.services.media_utils import run_subprocess
from app.services.tts_service import TTSService
from app.services.video_editing.clip_extractor import ClipExtractorService
from app.services.video_editing.helpers import (
    _XFADE_MAP,
    _concat_with_xfade,
    _find_bgm,
    _parse_srt_timed,
    _probe_duration,
    _render_subtitle_overlays,
)


class RemixAssemblerAgent(BaseAgent):
    """Extract planned source segments and assemble a remix video."""

    name = "remix_assembler"

    def __init__(
        self,
        clip_extractor: ClipExtractorService,
        *,
        output_dir: str | None = None,
        ffmpeg_bin: str | None = None,
        tts_service: TTSService | None = None,
    ):
        self.clip_extractor = clip_extractor
        self.output_dir = output_dir or settings.GENERATED_DIR
        self.ffmpeg_bin = ffmpeg_bin or settings.FFMPEG_BIN
        self.tts_service = tts_service

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        plan = input_data.get("remix_plan") or context.artifacts.get("remix_plan") or {}
        if not isinstance(plan, dict) or not plan.get("segments"):
            return AgentResult(success=False, output_data={}, error="remix_assembler: remix_plan with segments is required")

        segments = [
            item for item in sorted(plan.get("segments", []), key=lambda seg: int(seg.get("segment_idx", 0)))
            if isinstance(item, dict) and not item.get("removed")
        ]
        if not segments:
            return AgentResult(success=False, output_data={}, error="混剪方案中没有可用片段")

        validation_error = validate_remix_plan(
            {
                **plan,
                "segments": segments,
            }
        )
        if validation_error:
            return AgentResult(success=False, output_data={}, error=f"remix_plan 校验失败: {validation_error}")

        source_paths = await self._resolve_sources(context, {str(seg.get("source_video_id")) for seg in segments})
        missing = [seg.get("source_video_id") for seg in segments if str(seg.get("source_video_id")) not in source_paths]
        if missing:
            return AgentResult(success=False, output_data={}, error=f"找不到源视频: {', '.join(map(str, missing))}")

        await context.report_progress("正在从源视频提取混剪片段...", agent_name=self.name)
        audio_design = plan.get("audio_design") if isinstance(plan.get("audio_design"), dict) else {}
        input_config = input_data.get("input_config") if isinstance(input_data.get("input_config"), dict) else {}
        strategy = self._effective_audio_strategy(audio_design, input_config)
        audio_design["strategy"] = strategy
        include_audio = strategy in {"source_audio", "mix"}
        temp_dir = tempfile.mkdtemp(prefix="vidgen_remix_")
        os.makedirs(self.output_dir, exist_ok=True)
        try:
            clip_paths = await asyncio.gather(
                *[
                    self._extract_segment(source_paths[str(seg.get("source_video_id"))], seg, idx, temp_dir, include_audio)
                    for idx, seg in enumerate(segments)
                ]
            )

            clip_durations = await asyncio.gather(
                *[_probe_duration(self.ffmpeg_bin, p, run_subprocess) for p in clip_paths]
            )
            total_clips_duration = sum(d or 0.0 for d in clip_durations)
            logger.info(
                "remix clip extraction: %d clips, durations=%s, total=%.3fs",
                len(clip_paths),
                [round(d or 0.0, 2) for d in clip_durations],
                total_clips_duration,
            )

            await context.report_progress("正在拼接混剪片段...", agent_name=self.name)
            merged_path = os.path.join(temp_dir, "remix_merged.mp4")
            transition = self._primary_transition(segments)
            if transition != "none" and strategy != "source_audio" and len(clip_paths) > 1:
                merged_path = await _concat_with_xfade(
                    self.ffmpeg_bin,
                    clip_paths,
                    merged_path,
                    _XFADE_MAP.get(transition, "fade"),
                    self._transition_duration(segments),
                    run_subprocess,
                )
            else:
                await self._concat_simple(clip_paths, merged_path, include_audio=include_audio)

            voiceover_config = self._voiceover_config(
                {**input_config, "remix_plan": plan}
            )
            final_path = os.path.join(self.output_dir, f"remix_{uuid.uuid4().hex[:8]}.mp4")
            audio_output_path = final_path
            if voiceover_config.get("add_voiceover"):
                audio_output_path = os.path.join(temp_dir, "remix_audio_designed.mp4")
            output_path = await self._apply_audio_design(merged_path, audio_output_path, audio_design, strategy)
            audio_artifact = input_data.get("audio") if isinstance(input_data.get("audio"), dict) else {}
            if voiceover_config.get("add_voiceover"):
                output_path = await self._apply_voiceover_subtitles(
                    output_path,
                    final_path,
                    voiceover_config,
                    segments,
                    audio_artifact,
                )
            duration = await _probe_duration(self.ffmpeg_bin, output_path, run_subprocess) or 0.0
            return AgentResult(
                success=True,
                output_data={
                    "final_video_path": output_path,
                    "duration_ms": int(duration * 1000),
                    "remix_plan": plan,
                },
            )
        except Exception as exc:
            return AgentResult(success=False, output_data={}, error=f"混剪组装失败: {exc}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _extract_segment(
        self,
        source_path: str,
        segment: dict,
        index: int,
        temp_dir: str,
        include_audio: bool,
    ) -> str:
        output_path = os.path.join(temp_dir, f"segment_{index:03d}.mp4")
        return await self.clip_extractor.extract_clip(
            source_path,
            float(segment.get("start_seconds") or 0),
            float(segment.get("end_seconds") or 0),
            output_path,
            include_audio=include_audio,
        )

    async def _resolve_sources(self, context: AgentContext, video_ids: set[str]) -> dict[str, str]:
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

    async def _concat_simple(self, clip_paths: list[str], output_path: str, *, include_audio: bool) -> None:
        concat_file = os.path.join(os.path.dirname(output_path), "concat.txt")
        Path(concat_file).write_text(
            "\n".join(f"file '{Path(path).as_posix()}'" for path in clip_paths),
            encoding="utf-8",
        )
        args = [
            self.ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
        ]
        if include_audio:
            args.extend(["-c:a", "aac"])
        else:
            args.append("-an")
        args.append(output_path)
        rc, _, stderr = await run_subprocess(*args)
        if rc != 0:
            raise RuntimeError(f"ffmpeg concat failed: {stderr}")

    async def _apply_audio_design(self, input_path: str, output_path: str, audio_design: dict, strategy: str) -> str:
        bgm_mood = str(audio_design.get("bgm_mood") or "none")
        bgm_path = self._resolve_bgm_path(audio_design, bgm_mood)

        if strategy == "source_audio" or strategy == "silent" or not bgm_path:
            args = [self.ffmpeg_bin, "-y", "-i", input_path, "-c:v", "copy"]
            args.extend(["-c:a", "aac"] if strategy in {"source_audio", "mix"} else ["-an"])
            args.append(output_path)
            rc, _, stderr = await run_subprocess(*args)
            if rc != 0:
                raise RuntimeError(f"ffmpeg output mux failed: {stderr}")
            return output_path

        duration = await _probe_duration(self.ffmpeg_bin, input_path, run_subprocess) or 30.0
        volume = _clamp_float(audio_design.get("bgm_volume"), 0.0, 1.0, 0.15)
        fade_duration = min(2.0, duration * 0.15)
        fade_out_start = max(duration - fade_duration, 0)
        if strategy == "mix" and await self._has_audio_stream(input_path):
            rc, _, stderr = await run_subprocess(
                self.ffmpeg_bin,
                "-y",
                "-i",
                input_path,
                "-stream_loop",
                "-1",
                "-i",
                bgm_path,
                "-filter_complex",
                (
                    f"[0:a]volume=0.30[src];"
                    f"[1:a]volume={volume:.2f},afade=t=in:d=1.0,afade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}[bgm];"
                    "[src][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                ),
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                output_path,
            )
            if rc != 0:
                raise RuntimeError(f"ffmpeg bgm mix failed: {stderr}")
            return output_path

        rc, _, stderr = await run_subprocess(
            self.ffmpeg_bin,
            "-y",
            "-i",
            input_path,
            "-stream_loop",
            "-1",
            "-i",
            bgm_path,
            "-filter_complex",
            f"[1:a]volume={volume:.2f},afade=t=in:d=1.0,afade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}[bgm]",
            "-map",
            "0:v:0",
            "-map",
            "[bgm]",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            output_path,
        )
        if rc != 0:
            raise RuntimeError(f"ffmpeg bgm mux failed: {stderr}")
        return output_path

    def _resolve_bgm_path(self, audio_design: dict, bgm_mood: str) -> str | None:
        bgm_source = str(audio_design.get("bgm_source") or "").lower()
        bgm_path = str(audio_design.get("bgm_path") or "").strip()
        if bgm_path:
            if os.path.exists(bgm_path):
                return bgm_path
            if bgm_source in {"uploaded", "generated"}:
                raise RuntimeError(f"BGM 文件不存在: {bgm_path}")
        if bgm_source in {"uploaded", "generated"}:
            raise RuntimeError("BGM 来源已指定，但缺少可用音频文件")
        if bgm_source == "none" or bgm_mood == "none":
            return None
        return _find_bgm(bgm_mood)

    async def _has_audio_stream(self, path: str) -> bool:
        rc, stdout, _ = await run_subprocess(
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            path,
        )
        return rc == 0 and bool(stdout.strip())

    def _effective_audio_strategy(self, audio_design: dict, input_config: dict) -> str:
        remix_config = input_config.get("remix_config") if isinstance(input_config.get("remix_config"), dict) else {}
        requested = str(audio_design.get("strategy") or "")
        if requested not in {"source_audio", "bgm_only", "mix", "silent"}:
            requested = "source_audio"
        bgm_source = str(audio_design.get("bgm_source") or "").lower()
        config_bgm_material_id = str(remix_config.get("bgm_material_id") or input_config.get("bgm_material_id") or "").strip()
        if config_bgm_material_id:
            audio_design.setdefault("bgm_source", "uploaded")
            audio_design.setdefault("bgm_material_id", config_bgm_material_id)
            bgm_source = str(audio_design.get("bgm_source") or "").lower()
        has_uploaded_bgm = bgm_source in {"uploaded", "generated"} or bool(audio_design.get("bgm_material_id"))
        if has_uploaded_bgm:
            include_source = bool(remix_config.get("include_source_audio", input_config.get("include_source_audio", False)))
            return "mix" if include_source else "bgm_only"
        return requested

    def _voiceover_config(self, input_config: dict) -> dict:
        remix_config = input_config.get("remix_config") if isinstance(input_config.get("remix_config"), dict) else {}
        remix_plan = input_config.get("remix_plan") if isinstance(input_config.get("remix_plan"), dict) else {}
        audio_design = remix_plan.get("audio_design") if isinstance(remix_plan.get("audio_design"), dict) else {}
        if "add_voiceover" in remix_config:
            add_voiceover = bool(remix_config.get("add_voiceover"))
        elif "add_voiceover" in input_config:
            add_voiceover = bool(input_config.get("add_voiceover"))
        elif "voiceover_no_audio" in input_config:
            add_voiceover = not bool(input_config.get("voiceover_no_audio"))
        else:
            add_voiceover = False
        return {
            "add_voiceover": add_voiceover,
            "voiceover_script": remix_config.get("voiceover_script") or input_config.get("voiceover_script") or "",
            "voice_id": input_config.get("voice_id") or remix_config.get("voice_id") or audio_design.get("voice_id") or "default",
            "voice_speed": audio_design.get("voice_speed") or input_config.get("voice_speed") or 1.0,
        }

    async def _build_per_segment_voiceover(
        self,
        segments: list[dict],
        voice_id: str,
        speed: float,
        temp_dir: str,
    ) -> tuple[str, list[dict]]:
        """Generate per-segment TTS audio and merge them with adelay positioning.

        Returns (combined_audio_path, merged_timed_subtitle_segments).
        Each segment's narration starts at its video timeline offset so that
        narration for segment N plays while segment N is on screen.
        """
        offset_s = 0.0
        per_segment_items: list[tuple[str, list[dict], float]] = []
        for segment in segments:
            text = str(segment.get("voiceover") or segment.get("script_segment") or segment.get("narration") or "").strip()
            seg_duration = _segment_duration(segment)
            if text:
                tts_result = await self.tts_service.synthesize(text=text, voice_id=voice_id, speed=speed)
                sub_path = await self.tts_service.generate_subtitles(text=text, audio_path=tts_result.audio_path)
                raw_subs = _parse_srt_timed(sub_path) if sub_path and os.path.exists(sub_path) else []
                offset_subs = [
                    {"start_s": s["start_s"] + offset_s, "end_s": s["end_s"] + offset_s, "text": s["text"]}
                    for s in raw_subs
                ]
                per_segment_items.append((tts_result.audio_path, offset_subs, offset_s))
            offset_s += seg_duration

        if not per_segment_items:
            return "", []

        merged_subs: list[dict] = []
        for _, subs, _ in per_segment_items:
            merged_subs.extend(subs)

        if len(per_segment_items) == 1:
            return per_segment_items[0][0], merged_subs

        combined_audio_path = os.path.join(temp_dir, f"voiceover_combined_{uuid.uuid4().hex[:8]}.aac")
        input_args: list[str] = []
        for audio_path, _, _ in per_segment_items:
            input_args.extend(["-i", audio_path])

        filter_parts: list[str] = []
        mix_labels: list[str] = []
        for idx, (_, _, offset) in enumerate(per_segment_items):
            delay_ms = int(offset * 1000)
            label = f"[a{idx}]"
            filter_parts.append(f"[{idx}:a]adelay={delay_ms}:all=1{label}")
            mix_labels.append(label)
        n = len(per_segment_items)
        filter_parts.append(f"{''.join(mix_labels)}amix=inputs={n}:duration=longest:normalize=0[aout]")

        rc, _, stderr = await run_subprocess(
            self.ffmpeg_bin,
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            combined_audio_path,
        )
        if rc != 0:
            raise RuntimeError(f"ffmpeg per-segment voiceover combine failed: {stderr}")
        return combined_audio_path, merged_subs

    async def _apply_voiceover_subtitles(
        self,
        input_path: str,
        output_path: str,
        input_config: dict,
        segments: list[dict],
        audio_artifact: dict | None = None,
    ) -> str:
        audio_artifact = audio_artifact or {}
        audio_path = str(audio_artifact.get("audio_path") or "").strip()
        subtitle_path = str(audio_artifact.get("subtitle_path") or "").strip()
        timed_segments: list[dict] = []
        per_seg_temp: str | None = None
        try:
            if not audio_path:
                if self.tts_service is None:
                    raise RuntimeError("未配置 TTS 服务，无法生成混剪旁白")

                voice_id = str(input_config.get("voice_id") or "default")
                speed = float(input_config.get("voice_speed") or input_config.get("speed") or 1.0)

                has_per_segment_voiceover = any(
                    str(seg.get("voiceover") or seg.get("script_segment") or seg.get("narration") or "").strip()
                    for seg in segments
                )

                if has_per_segment_voiceover:
                    per_seg_temp = tempfile.mkdtemp(prefix="vidgen_remix_vo_")
                    audio_path, timed_segments = await self._build_per_segment_voiceover(
                        segments, voice_id, speed, per_seg_temp
                    )
                    if not audio_path:
                        shutil.copy2(input_path, output_path)
                        return output_path
                else:
                    script = str(input_config.get("voiceover_script") or "").strip()
                    if not script:
                        shutil.copy2(input_path, output_path)
                        return output_path
                    tts_result = await self.tts_service.synthesize(text=script, voice_id=voice_id, speed=speed)
                    audio_path = tts_result.audio_path
                    subtitle_path = await self.tts_service.generate_subtitles(text=script, audio_path=audio_path)

            if not os.path.exists(audio_path):
                raise RuntimeError(f"旁白音频文件不存在: {audio_path}")
            has_existing_audio = await self._has_audio_stream(input_path)
            video_duration = await _probe_duration(self.ffmpeg_bin, input_path, run_subprocess) or 0.0
            if not timed_segments:
                has_subtitles = bool(subtitle_path and os.path.exists(subtitle_path))
                timed_segments = _clamped_subtitle_segments(subtitle_path, video_duration) if has_subtitles else []

            if timed_segments:
                overlay_temp_dir = tempfile.mkdtemp(prefix="vidgen_remix_subtitles_")
                try:
                    width, height = await self._probe_video_dimensions(input_path)
                    sub_inputs, overlay_filter = _render_subtitle_overlays(
                        timed_segments,
                        width,
                        height,
                        overlay_temp_dir,
                        first_overlay_input_idx=2,
                    )
                    args = [
                        self.ffmpeg_bin,
                        "-y",
                        "-i",
                        input_path,
                        "-i",
                        audio_path,
                    ]
                    for png_path in sub_inputs:
                        args.extend(["-i", png_path])
                    if has_existing_audio:
                        filter_complex = (
                            f"{overlay_filter};"
                            "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                        )
                        args.extend([
                            "-filter_complex",
                            filter_complex,
                            "-map",
                            "[vout]",
                            "-map",
                            "[aout]",
                        ])
                    else:
                        args.extend([
                            "-filter_complex",
                            overlay_filter,
                            "-map",
                            "[vout]",
                            "-map",
                            "1:a:0",
                        ])
                    args.extend([
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-t",
                        f"{video_duration:.3f}",
                        output_path,
                    ])
                    rc, _, stderr = await run_subprocess(*args)
                    if rc != 0:
                        raise RuntimeError(f"ffmpeg voiceover/subtitle mux failed: {stderr}")
                    return output_path
                finally:
                    shutil.rmtree(overlay_temp_dir, ignore_errors=True)

            if has_existing_audio:
                args = [
                    self.ffmpeg_bin,
                    "-y",
                    "-i",
                    input_path,
                    "-i",
                    audio_path,
                    "-filter_complex",
                    "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                    "-map",
                    "0:v:0",
                    "-map",
                    "[aout]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    output_path,
                ]
            else:
                args = [
                    self.ffmpeg_bin,
                    "-y",
                    "-i",
                    input_path,
                    "-i",
                    audio_path,
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    output_path,
                ]
            rc, _, stderr = await run_subprocess(*args)
            if rc != 0:
                raise RuntimeError(f"ffmpeg voiceover/subtitle mux failed: {stderr}")
            return output_path
        finally:
            if per_seg_temp:
                shutil.rmtree(per_seg_temp, ignore_errors=True)

    async def _probe_video_dimensions(self, path: str) -> tuple[int, int]:
        rc, stdout, stderr = await run_subprocess(self.ffmpeg_bin, "-i", path, "-hide_banner", "-f", "null", "-")
        match = re.search(r"(\d{3,5})x(\d{3,5})", stdout + stderr)
        if rc == 0 and match:
            return int(match.group(1)), int(match.group(2))
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1280, 720

    @staticmethod
    def _primary_transition(segments: list[dict]) -> str:
        for segment in segments[:-1]:
            transition = str(segment.get("transition_to_next") or "cut")
            if transition in {"fade", "dissolve", "slideright", "slideup"}:
                return transition
        return "none"

    @staticmethod
    def _transition_duration(segments: list[dict]) -> float:
        for idx, segment in enumerate(segments[:-1]):
            transition = str(segment.get("transition_to_next") or "cut")
            if transition not in {"fade", "dissolve", "slideright", "slideup"}:
                continue
            current_duration = _segment_duration(segment)
            next_duration = _segment_duration(segments[idx + 1])
            max_allowed = min(current_duration, next_duration) * 0.35
            if max_allowed < 0.1:
                continue
            duration = _clamp_float(
                segment.get("transition_duration"),
                0.1,
                min(1.0, max_allowed),
                min(0.25, max_allowed),
            )
            if duration > 0:
                return duration
        return 0.25


def _segment_duration(segment: dict[str, Any]) -> float:
    start = _clamp_float(segment.get("start_seconds"), 0.0, 10_000.0, 0.0)
    end = _clamp_float(segment.get("end_seconds"), start, 10_000.0, start)
    return max(end - start, 0.0)


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return max(low, min(high, numeric))


def _clamped_subtitle_segments(subtitle_path: str, video_duration: float) -> list[dict]:
    segments = _parse_srt_timed(subtitle_path)
    if video_duration <= 0:
        return segments
    clamped = [dict(segment) for segment in segments if float(segment.get("start_s", 0.0)) < video_duration]
    for segment in clamped:
        segment["end_s"] = min(float(segment.get("end_s", 0.0)), video_duration)
    return [segment for segment in clamped if float(segment.get("end_s", 0.0)) > float(segment.get("start_s", 0.0))]
