from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.services.video_editing.helpers import (
    _XFADE_MAP,
    _concat_with_xfade,
    _find_bgm,
    _parse_srt,
    _parse_srt_timed,
    _probe_duration,
    _render_subtitle_overlays,
)


logger = logging.getLogger(__name__)

@dataclass
class ComposeResult:
    output_path: str
    duration_ms: int
    usage: dict[str, int] | None = None


class VideoEditorService(ABC):
    @abstractmethod
    async def compose(
        self,
        video_clips: list[str],
        audio_path: str,
        subtitle_path: str,
        output_path: str,
        context_data: dict | None = None,
    ) -> ComposeResult:
        """Assemble video clips + audio + subtitles into a final video."""
        ...


class MockVideoEditorService(VideoEditorService):
    """Mock editor that creates a placeholder output after a simulated delay."""

    async def compose(
        self,
        video_clips: list[str],
        audio_path: str,
        subtitle_path: str,
        output_path: str,
        context_data: dict | None = None,
    ) -> ComposeResult:
        await asyncio.sleep(3)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Create placeholder file
        with open(output_path, "wb") as f:
            f.write(b"\x00" * 2048)

        # Estimate total duration from number of clips (5s each)
        duration_ms = len(video_clips) * 5000

        return ComposeResult(output_path=output_path, duration_ms=duration_ms, usage={})


class RealVideoEditorService(VideoEditorService):
    def __init__(self, llm_service, ffmpeg_bin: str = "ffmpeg"):
        self.llm = llm_service
        self.ffmpeg_bin = ffmpeg_bin

    async def compose(
        self,
        video_clips: list[str],
        audio_path: str,
        subtitle_path: str,
        output_path: str,
        context_data: dict | None = None,
    ) -> ComposeResult:
        import tempfile

        from app.services.media_utils import ensure_local_file, run_subprocess

        if not isinstance(context_data, dict):
            logger.warning("video_editor received non-dict context_data (%s), fallback to empty context", type(context_data).__name__)
            context_data = {}
        duration_mode = context_data.get("duration_mode", "fixed")
        shot_durations = context_data.get("shot_durations", [])
        transition_type = context_data.get("transition", "none")
        transition_dur = float(context_data.get("transition_duration", 0.5))
        bgm_mood = context_data.get("bgm_mood", "none")
        bgm_volume = float(context_data.get("bgm_volume", 0.15))
        watermark_path = context_data.get("watermark_path")
        target_duration_s = sum(float(d) for d in shot_durations) if shot_durations else None
        video_model_no_audio = context_data.get("video_model_no_audio", True)
        # Preserve model-generated audio only when explicitly enabled AND no TTS voiceover.
        # TTS always wins; xfade transitions strip audio (complex audio chaining not supported).
        has_tts_audio = bool(audio_path and os.path.exists(audio_path))
        preserve_model_audio = not video_model_no_audio and not has_tts_audio

        subtitle_segments = _parse_srt(subtitle_path)
        video_clips_data = context_data.get("video_clips_data", [])

        # Shot order from the director plan is canonical. The video generator
        # already emits clips in shot_idx order, so the editor must not reorder.
        ordered_indices = list(range(len(video_clips)))
        usage = {}

        # Build a per-clip-index duration map from the director plan.
        clips_duration_map: dict[int, float] = {}
        for ci, clip in enumerate(video_clips_data):
            clip_item = clip if isinstance(clip, dict) else {}
            # Prefer duration_seconds (final-cut time) from clip record
            d = clip_item.get("duration_seconds")
            if d is None and ci < len(shot_durations):
                d = shot_durations[ci]
            if d is not None:
                clips_duration_map[ci] = float(d)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="vidgen_edit_")
        processed_paths: list[str] = []
        try:
            for output_idx, clip_idx in enumerate(ordered_indices):
                local_clip = await ensure_local_file(video_clips[clip_idx], workdir=temp_dir)
                clip_out = os.path.join(temp_dir, f"clip_{output_idx:03d}.mp4")
                duration = None
                # Use per-clip duration from the director plan first, even in auto mode.
                # The plan duration is the final-cut authority; subtitles are only a fallback.
                clip_dur = clips_duration_map.get(clip_idx)
                if clip_dur is not None:
                    duration = max(clip_dur, 1.0)
                elif output_idx < len(subtitle_segments):
                    duration = max(subtitle_segments[output_idx]["duration_s"], 1.0)
                ffmpeg_args = [self.ffmpeg_bin, "-y", "-i", local_clip]
                if duration:
                    ffmpeg_args.extend(["-t", f"{duration:.2f}"])
                if preserve_model_audio:
                    ffmpeg_args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"])
                else:
                    ffmpeg_args.extend(["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p"])
                ffmpeg_args.append(clip_out)
                return_code, _, stderr = await run_subprocess(*ffmpeg_args)
                if return_code != 0:
                    raise RuntimeError(f"ffmpeg clip process failed: {stderr}")
                processed_paths.append(clip_out)

            merged_path = os.path.join(temp_dir, "merged.mp4")

            if transition_type != "none" and len(processed_paths) >= 2:
                # ── xfade transitions between clips ──
                # xfade filter only handles video; audio cannot be preserved here.
                xfade_name = _XFADE_MAP.get(transition_type, "fade")
                merged_path = await _concat_with_xfade(
                    self.ffmpeg_bin, processed_paths, merged_path,
                    xfade_name, transition_dur, run_subprocess,
                )
                merged_has_audio = False
            else:
                # ── Simple concat (no transitions) ──
                concat_file = os.path.join(temp_dir, "concat.txt")
                Path(concat_file).write_text(
                    "\n".join(f"file '{Path(p).as_posix()}'" for p in processed_paths),
                    encoding="utf-8",
                )
                concat_cmd = [
                    self.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_file, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                ]
                if preserve_model_audio:
                    concat_cmd.extend(["-c:a", "aac"])
                concat_cmd.append(merged_path)
                return_code, _, stderr = await run_subprocess(*concat_cmd)
                if return_code != 0:
                    raise RuntimeError(f"ffmpeg concat failed: {stderr}")
                merged_has_audio = preserve_model_audio

            # ── Probe merged video duration ──
            import logging as _logging
            _log = _logging.getLogger(__name__)
            video_dur_s = await _probe_duration(self.ffmpeg_bin, merged_path, run_subprocess)
            _log.info(f"Merged video duration: {video_dur_s}s, target: {target_duration_s}s")

            # ── Pre-trim audio to match video (fixed mode) ──
            # In fixed mode, audio MUST NOT exceed the video length.
            # Strategy: trim audio to target duration with 1s fade-out.
            actual_audio_path = audio_path
            tempo_ratio = 1.0
            effective_dur_s = target_duration_s or video_dur_s  # hard cap
            if effective_dur_s and audio_path and os.path.exists(audio_path):
                audio_dur_s = await _probe_duration(self.ffmpeg_bin, audio_path, run_subprocess)
                _log.info(f"Audio duration: {audio_dur_s}s, effective target: {effective_dur_s}s")
                if audio_dur_s and audio_dur_s > effective_dur_s * 1.05:
                    ratio = audio_dur_s / effective_dur_s
                    # Mild mismatch (≤1.3x): speed up audio slightly — still natural
                    if ratio <= 1.3:
                        adjusted_audio = os.path.join(temp_dir, "audio_adjusted.mp3")
                        rc, _, err = await run_subprocess(
                            self.ffmpeg_bin, "-y", "-i", audio_path,
                            "-filter:a", f"atempo={ratio:.4f}",
                            "-vn", adjusted_audio,
                        )
                        if rc == 0:
                            actual_audio_path = adjusted_audio
                            tempo_ratio = ratio
                            _log.info(f"Audio sped up {ratio:.2f}x to fit video")
                        else:
                            _log.warning(f"Audio atempo failed, falling back to trim: {err}")
                    # Larger mismatch: trim + fade-out (don't distort speech)
                    if actual_audio_path == audio_path:
                        adjusted_audio = os.path.join(temp_dir, "audio_trimmed.mp3")
                        fade_start = max(effective_dur_s - 1.0, 0)
                        rc, _, err = await run_subprocess(
                            self.ffmpeg_bin, "-y", "-i", audio_path,
                            "-t", f"{effective_dur_s:.3f}",
                            "-af", f"afade=t=out:st={fade_start:.3f}:d=1.0",
                            "-vn", adjusted_audio,
                        )
                        if rc == 0:
                            actual_audio_path = adjusted_audio
                            _log.info(f"Audio trimmed to {effective_dur_s}s with fade-out")
                        else:
                            _log.warning(f"Audio trim failed: {err}")

            # ── Mix background music if requested ──
            if bgm_mood != "none":
                bgm_path = _find_bgm(bgm_mood)
                if bgm_path:
                    bgm_dur = effective_dur_s or video_dur_s or 30
                    if actual_audio_path and os.path.exists(actual_audio_path):
                        mixed_audio = os.path.join(temp_dir, "audio_with_bgm.mp3")
                        rc, _, err = await run_subprocess(
                            self.ffmpeg_bin, "-y",
                            "-i", actual_audio_path,
                            "-stream_loop", "-1", "-i", bgm_path,
                            "-filter_complex",
                            f"[1:a]volume={bgm_volume:.2f},afade=t=in:d=1.5,afade=t=out:st={max(bgm_dur - 2, 0):.1f}:d=2[bgm];"
                            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[out]",
                            "-map", "[out]",
                            "-t", f"{bgm_dur:.3f}",
                            "-c:a", "libmp3lame", "-q:a", "2",
                            mixed_audio,
                        )
                        if rc == 0:
                            actual_audio_path = mixed_audio
                            _log.info(f"Mixed BGM ({bgm_mood}) at volume {bgm_volume}")
                        else:
                            _log.warning(f"BGM mixing failed, continuing without: {err}")
                    elif not merged_has_audio:
                        bgm_only_audio = os.path.join(temp_dir, "bgm_only.mp3")
                        fade_out_start = max(bgm_dur - 2, 0)
                        rc, _, err = await run_subprocess(
                            self.ffmpeg_bin, "-y",
                            "-stream_loop", "-1", "-i", bgm_path,
                            "-af",
                            f"volume={bgm_volume:.2f},afade=t=in:d=1.5,afade=t=out:st={fade_out_start:.1f}:d=2",
                            "-t", f"{bgm_dur:.3f}",
                            "-c:a", "libmp3lame", "-q:a", "2",
                            bgm_only_audio,
                        )
                        if rc == 0:
                            actual_audio_path = bgm_only_audio
                            _log.info(f"Created BGM-only audio ({bgm_mood}) at volume {bgm_volume}")
                        else:
                            _log.warning(f"BGM-only audio failed, continuing silent: {err}")
                    else:
                        _log.info("Skipping BGM mix because model audio is already preserved")

            # Scale subtitle timings if audio was sped up
            timed_segments = _parse_srt_timed(subtitle_path)
            if timed_segments and tempo_ratio > 1.0:
                for seg in timed_segments:
                    seg["start_s"] /= tempo_ratio
                    seg["end_s"] /= tempo_ratio
            # Also clamp subtitle timings to video duration
            if timed_segments and effective_dur_s:
                timed_segments = [
                    seg for seg in timed_segments if seg["start_s"] < effective_dur_s
                ]
                for seg in timed_segments:
                    seg["end_s"] = min(seg["end_s"], effective_dur_s)
            has_audio = bool(actual_audio_path and os.path.exists(actual_audio_path))

            # Burn subtitles into the video
            if timed_segments:
                # Probe video dimensions
                probe_rc, probe_stdout, probe_stderr = await run_subprocess(
                    self.ffmpeg_bin, "-i", merged_path,
                    "-hide_banner", "-f", "null", "-",
                )
                vid_w, vid_h = 1280, 720
                import re as _re
                dim_match = _re.search(r"(\d{3,5})x(\d{3,5})", probe_stdout + probe_stderr)
                if dim_match:
                    vid_w, vid_h = int(dim_match.group(1)), int(dim_match.group(2))

                sub_inputs, filter_complex = _render_subtitle_overlays(
                    timed_segments,
                    vid_w,
                    vid_h,
                    temp_dir,
                    first_overlay_input_idx=2 if has_audio else 1,
                )
                mux_args = [self.ffmpeg_bin, "-y", "-i", merged_path]
                if has_audio:
                    mux_args.extend(["-i", actual_audio_path])
                for png_path in sub_inputs:
                    mux_args.extend(["-i", png_path])
                mux_args.extend([
                    "-filter_complex", filter_complex,
                    "-map", "[vout]",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                ])
                if has_audio:
                    # TTS audio as separate input (stream 1)
                    mux_args.extend(["-map", "1:a:0", "-c:a", "aac", "-shortest"])
                elif merged_has_audio:
                    # Model audio embedded in merged video (stream 0)
                    mux_args.extend(["-map", "0:a:0", "-c:a", "aac", "-shortest"])
                else:
                    mux_args.append("-an")
                if target_duration_s:
                    mux_args.extend(["-t", f"{target_duration_s:.3f}"])
                mux_args.append(output_path)
            elif has_audio:
                mux_args = [
                    self.ffmpeg_bin, "-y",
                    "-i", merged_path,
                    "-i", actual_audio_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-shortest",
                ]
                if target_duration_s:
                    mux_args.extend(["-t", f"{target_duration_s:.3f}"])
                mux_args.append(output_path)
            elif merged_has_audio:
                # Model audio in merged video — just copy it through
                mux_args = [
                    self.ffmpeg_bin, "-y",
                    "-i", merged_path,
                    "-c:v", "copy",
                    "-c:a", "copy",
                ]
                if target_duration_s:
                    mux_args.extend(["-t", f"{target_duration_s:.3f}"])
                mux_args.append(output_path)
            else:
                mux_args = [
                    self.ffmpeg_bin, "-y",
                    "-i", merged_path,
                    "-c:v", "copy",
                    "-an",
                ]
                if target_duration_s:
                    mux_args.extend(["-t", f"{target_duration_s:.3f}"])
                mux_args.append(output_path)
            return_code, _, stderr = await run_subprocess(*mux_args)
            if return_code != 0:
                raise RuntimeError(f"ffmpeg mux failed: {stderr}")

            # ── Watermark overlay (if provided) ──
            if watermark_path and os.path.exists(watermark_path):
                watermarked_path = os.path.join(temp_dir, "watermarked.mp4")
                # Overlay watermark in top-right corner with padding and 70% opacity
                wm_filter = (
                    "[1:v]format=rgba,colorchannelmixer=aa=0.7[wm];"
                    "[0:v][wm]overlay=W-w-20:20[vout]"
                )
                wm_rc, _, wm_err = await run_subprocess(
                    self.ffmpeg_bin, "-y",
                    "-i", output_path,
                    "-i", watermark_path,
                    "-filter_complex", wm_filter,
                    "-map", "[vout]",
                    "-map", "0:a?",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "copy",
                    watermarked_path,
                )
                if wm_rc == 0:
                    os.replace(watermarked_path, output_path)
                    _log.info(f"Watermark applied from {watermark_path}")
                else:
                    _log.warning(f"Watermark overlay failed, continuing without: {wm_err}")

            # ── Probe ACTUAL output duration (authoritative) ──
            actual_output_dur = await _probe_duration(self.ffmpeg_bin, output_path, run_subprocess)
            if actual_output_dur:
                duration_ms = int(actual_output_dur * 1000)
            elif duration_mode == "auto":
                duration_ms = int(sum(seg["duration_s"] for seg in subtitle_segments) * 1000) if subtitle_segments else len(video_clips) * 5000
            else:
                duration_ms = int(sum(float(d) for d in shot_durations) * 1000) if shot_durations else len(video_clips) * 5000
            _log.info(f"Final output duration: {duration_ms}ms (probed={actual_output_dur}s)")
            return ComposeResult(output_path=output_path, duration_ms=duration_ms, usage=usage)
        finally:
            for path in processed_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass
