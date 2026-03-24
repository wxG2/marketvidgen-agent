from __future__ import annotations

import asyncio
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.prompts import VIDEO_EDITOR_SYSTEM_PROMPT

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
        import os
        import tempfile
        from pathlib import Path

        from app.services.media_utils import ensure_local_file, run_subprocess

        context_data = context_data or {}
        shot_prompts = context_data.get("shot_prompts", [])
        duration_mode = context_data.get("duration_mode", "fixed")
        shot_durations = context_data.get("shot_durations", [])
        transition_type = context_data.get("transition", "none")
        transition_dur = float(context_data.get("transition_duration", 0.5))
        bgm_mood = context_data.get("bgm_mood", "none")
        bgm_volume = float(context_data.get("bgm_volume", 0.15))
        watermark_path = context_data.get("watermark_path")
        target_duration_s = (
            sum(float(d) for d in shot_durations)
            if duration_mode == "fixed" and shot_durations
            else None
        )
        subtitle_segments = _parse_srt(subtitle_path)

        schema = {
            "name": "edit_plan",
            "schema": {
                "type": "object",
                "properties": {
                    "ordered_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                    }
                },
                "required": ["ordered_indices"],
            },
        }
        video_clips_data = context_data.get("video_clips_data", [])
        clip_context = []
        for idx, clip in enumerate(video_clips_data):
            prompt = shot_prompts[idx] if idx < len(shot_prompts) else {}
            clip_context.append(
                {
                    "shot_idx": clip.get("shot_idx", idx),
                    "prompt": prompt.get("video_prompt", ""),
                    "script_segment": prompt.get("script_segment", ""),
                    "subtitle_text": subtitle_segments[idx]["text"] if idx < len(subtitle_segments) else "",
                }
            )
        if not clip_context:
            clip_context = [{"shot_idx": idx, "prompt": "", "script_segment": "", "subtitle_text": ""} for idx in range(len(video_clips))]

        plan, usage = await self.llm.generate_structured(
            system_prompt=VIDEO_EDITOR_SYSTEM_PROMPT,
            user_prompt=str({"clips": clip_context, "subtitle_segments": subtitle_segments}),
            schema=schema,
        )
        ordered_indices = plan.get("ordered_indices") or list(range(len(video_clips)))
        if sorted(ordered_indices) != list(range(len(video_clips))):
            ordered_indices = list(range(len(video_clips)))

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="vidgen_edit_")
        processed_paths: list[str] = []
        try:
            for output_idx, clip_idx in enumerate(ordered_indices):
                local_clip = await ensure_local_file(video_clips[clip_idx], workdir=temp_dir)
                clip_out = os.path.join(temp_dir, f"clip_{output_idx:03d}.mp4")
                duration = None
                if duration_mode == "auto":
                    if output_idx < len(subtitle_segments):
                        duration = max(subtitle_segments[output_idx]["duration_s"], 1.0)
                else:
                    if output_idx < len(shot_durations):
                        duration = max(float(shot_durations[output_idx]), 1.0)
                    elif output_idx < len(subtitle_segments):
                        duration = max(subtitle_segments[output_idx]["duration_s"], 1.0)
                ffmpeg_args = [self.ffmpeg_bin, "-y", "-i", local_clip]
                if duration:
                    ffmpeg_args.extend(["-t", f"{duration:.2f}"])
                ffmpeg_args.extend(["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_out])
                return_code, _, stderr = await run_subprocess(*ffmpeg_args)
                if return_code != 0:
                    raise RuntimeError(f"ffmpeg clip process failed: {stderr}")
                processed_paths.append(clip_out)

            merged_path = os.path.join(temp_dir, "merged.mp4")

            if transition_type != "none" and len(processed_paths) >= 2:
                # ── xfade transitions between clips ──
                xfade_name = _XFADE_MAP.get(transition_type, "fade")
                merged_path = await _concat_with_xfade(
                    self.ffmpeg_bin, processed_paths, merged_path,
                    xfade_name, transition_dur, run_subprocess,
                )
            else:
                # ── Simple concat (no transitions) ──
                concat_file = os.path.join(temp_dir, "concat.txt")
                Path(concat_file).write_text(
                    "\n".join(f"file '{Path(p).as_posix()}'" for p in processed_paths),
                    encoding="utf-8",
                )
                return_code, _, stderr = await run_subprocess(
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
                    merged_path,
                )
                if return_code != 0:
                    raise RuntimeError(f"ffmpeg concat failed: {stderr}")

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
            if bgm_mood != "none" and actual_audio_path and os.path.exists(actual_audio_path):
                bgm_path = _find_bgm(bgm_mood)
                if bgm_path:
                    mixed_audio = os.path.join(temp_dir, "audio_with_bgm.mp3")
                    bgm_dur = effective_dur_s or video_dur_s or 30
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
                    timed_segments, vid_w, vid_h, temp_dir,
                )
                mux_args = [self.ffmpeg_bin, "-y", "-i", merged_path, "-i", actual_audio_path]
                for png_path in sub_inputs:
                    mux_args.extend(["-i", png_path])
                mux_args.extend([
                    "-filter_complex", filter_complex,
                    "-map", "[vout]",
                    "-map", "1:a:0",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-shortest",
                ])
                if target_duration_s:
                    mux_args.extend(["-t", f"{target_duration_s:.3f}"])
                mux_args.append(output_path)
            else:
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


async def _probe_duration(ffmpeg_bin: str, file_path: str, run_subprocess) -> float | None:
    """Probe media file duration in seconds using ffprobe."""
    import re as _re
    rc, stdout, stderr = await run_subprocess(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", file_path,
    )
    if rc == 0 and stdout.strip():
        try:
            return float(stdout.strip())
        except ValueError:
            pass
    # Fallback: parse from ffmpeg stderr
    rc, _, stderr = await run_subprocess(ffmpeg_bin, "-i", file_path, "-hide_banner", "-f", "null", "-")
    match = _re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", stderr)
    if match:
        h, m, s, cs = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        return h * 3600 + m * 60 + s + cs / 100.0
    return None


def _parse_srt(subtitle_path: str) -> list[dict]:
    import re

    if not subtitle_path or not os.path.exists(subtitle_path):
        return []

    blocks = Path(subtitle_path).read_text(encoding="utf-8").strip().split("\n\n")
    segments = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        time_line = lines[1]
        text = " ".join(lines[2:])
        match = re.match(r"(\d\d:\d\d:\d\d,\d\d\d)\s+-->\s+(\d\d:\d\d:\d\d,\d\d\d)", time_line)
        if not match:
            continue
        start_s = _srt_time_to_seconds(match.group(1))
        end_s = _srt_time_to_seconds(match.group(2))
        segments.append({"text": text, "duration_s": max(end_s - start_s, 1.0)})
    return segments


def _srt_time_to_seconds(value: str) -> float:
    hh, mm, rest = value.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def _parse_srt_timed(subtitle_path: str) -> list[dict]:
    """Parse SRT file and return segments with start_s, end_s, text."""
    import re

    if not subtitle_path or not os.path.exists(subtitle_path):
        return []

    blocks = Path(subtitle_path).read_text(encoding="utf-8").strip().split("\n\n")
    segments = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        time_line = lines[1]
        text = " ".join(lines[2:])
        match = re.match(r"(\d\d:\d\d:\d\d,\d\d\d)\s+-->\s+(\d\d:\d\d:\d\d,\d\d\d)", time_line)
        if not match:
            continue
        start_s = _srt_time_to_seconds(match.group(1))
        end_s = _srt_time_to_seconds(match.group(2))
        segments.append({"start_s": start_s, "end_s": end_s, "text": text})
    return segments


def _render_subtitle_overlays(
    segments: list[dict], width: int, height: int, temp_dir: str
) -> tuple[list[str], str]:
    """Render each subtitle segment as a transparent PNG and build ffmpeg overlay filter.

    Returns (list_of_png_paths, filter_complex_string).
    Uses Pillow for text rendering — no libass/freetype dependency in ffmpeg needed.
    The overlay filter is built-in and always available.
    """
    from PIL import Image, ImageDraw, ImageFont

    # Try to find a CJK-capable font
    font = None
    font_size = 28
    font_candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc",          # macOS Chinese
        "/System/Library/Fonts/PingFang.ttc",                # macOS PingFang
        "/System/Library/Fonts/Hiragino Sans GB.ttc",        # macOS Hiragino
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except Exception:
                continue
    if font is None:
        try:
            font = ImageFont.truetype("Arial", font_size)
        except Exception:
            font = ImageFont.load_default()

    png_paths: list[str] = []
    for i, seg in enumerate(segments):
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        text = seg["text"]

        # Measure text
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (width - tw) // 2
        y = height - th - 40  # 40px from bottom

        # Draw black outline
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 220))
        # Draw white text
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

        png_path = os.path.join(temp_dir, f"sub_{i:03d}.png")
        img.save(png_path)
        png_paths.append(png_path)

    # Build filter_complex: chain overlays with enable=between(t,start,end)
    # Inputs: [0:v] = video, [1:a] = audio, [2] [3] ... = subtitle PNGs
    parts = []
    prev = "[0:v]"
    for i, seg in enumerate(segments):
        input_idx = i + 2  # 0=video, 1=audio, 2..N=PNGs
        out_label = f"[v{i}]" if i < len(segments) - 1 else "[vout]"
        start = f"{seg['start_s']:.3f}"
        end = f"{seg['end_s']:.3f}"
        parts.append(
            f"{prev}[{input_idx}]overlay=0:0:enable='between(t,{start},{end})'{out_label}"
        )
        prev = out_label

    filter_complex = ";".join(parts)
    return png_paths, filter_complex


# ── Transition xfade mapping ──
_XFADE_MAP: dict[str, str] = {
    "fade": "fade",
    "dissolve": "dissolve",
    "slideright": "slideright",
    "slideup": "slideup",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
}


async def _concat_with_xfade(
    ffmpeg_bin: str,
    clip_paths: list[str],
    output_path: str,
    xfade_name: str,
    xfade_dur: float,
    run_subprocess,
) -> str:
    """Concatenate clips with xfade transitions between each pair.

    FFmpeg xfade filter: [v0][v1]xfade=transition=fade:duration=0.5:offset=4.5[vx0]
    where offset = duration_of_clip0 - xfade_dur
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    if len(clip_paths) == 1:
        # Single clip, just copy
        import shutil
        shutil.copy2(clip_paths[0], output_path)
        return output_path

    # Probe each clip's duration
    durations: list[float] = []
    for cp in clip_paths:
        dur = await _probe_duration(ffmpeg_bin, cp, run_subprocess)
        durations.append(dur or 5.0)

    # Build input args and filter_complex
    input_args: list[str] = []
    for cp in clip_paths:
        input_args.extend(["-i", cp])

    # Chain xfade filters
    filter_parts: list[str] = []
    offset = durations[0] - xfade_dur
    prev_label = "[0:v]"
    for i in range(1, len(clip_paths)):
        out_label = f"[vx{i}]" if i < len(clip_paths) - 1 else "[vout]"
        offset_clamped = max(offset, 0.1)
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition={xfade_name}:duration={xfade_dur:.2f}:offset={offset_clamped:.3f}{out_label}"
        )
        prev_label = out_label
        if i < len(clip_paths) - 1:
            offset = offset_clamped + durations[i] - xfade_dur

    filter_complex = ";".join(filter_parts)

    args = [ffmpeg_bin, "-y"] + input_args + [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    rc, _, stderr = await run_subprocess(*args)
    if rc != 0:
        _log.warning(f"xfade concat failed, falling back to simple concat: {stderr}")
        # Fallback: simple concat
        import tempfile
        concat_file = os.path.join(tempfile.mkdtemp(), "concat.txt")
        Path(concat_file).write_text(
            "\n".join(f"file '{Path(p).as_posix()}'" for p in clip_paths),
            encoding="utf-8",
        )
        rc2, _, stderr2 = await run_subprocess(
            ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file, "-c:v", "libx264", "-pix_fmt", "yuv420p",
            output_path,
        )
        if rc2 != 0:
            raise RuntimeError(f"ffmpeg concat fallback failed: {stderr2}")
    return output_path


def _find_bgm(mood: str) -> str | None:
    """Find a BGM audio file for the given mood from the BGM directory."""
    from app.config import settings
    import random

    bgm_dir = Path(settings.BGM_DIR)
    if not bgm_dir.exists():
        return None

    # Look for files in mood subdirectory or root with mood prefix
    mood_dir = bgm_dir / mood
    candidates: list[Path] = []
    if mood_dir.is_dir():
        candidates = list(mood_dir.glob("*.mp3")) + list(mood_dir.glob("*.wav"))
    if not candidates:
        candidates = list(bgm_dir.glob(f"{mood}*.mp3")) + list(bgm_dir.glob(f"{mood}*.wav"))
    if not candidates:
        # Fall back to any BGM file
        candidates = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav"))

    if candidates:
        return str(random.choice(candidates))
    return None
