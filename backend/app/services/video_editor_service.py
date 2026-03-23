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

            concat_file = os.path.join(temp_dir, "concat.txt")
            Path(concat_file).write_text(
                "\n".join(f"file '{Path(p).as_posix()}'" for p in processed_paths),
                encoding="utf-8",
            )
            merged_path = os.path.join(temp_dir, "merged.mp4")
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
