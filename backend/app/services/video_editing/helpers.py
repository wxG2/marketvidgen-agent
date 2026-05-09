from __future__ import annotations

import os
from pathlib import Path


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
    segments: list[dict],
    width: int,
    height: int,
    temp_dir: str,
    first_overlay_input_idx: int = 2,
) -> tuple[list[str], str]:
    """Render subtitle segments as transparent PNGs and build an overlay filter."""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    font_size = 28
    font_candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
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

        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (width - tw) // 2
        y = height - th - 40

        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

        png_path = os.path.join(temp_dir, f"sub_{i:03d}.png")
        img.save(png_path)
        png_paths.append(png_path)

    parts = []
    prev = "[0:v]"
    for i, seg in enumerate(segments):
        input_idx = i + first_overlay_input_idx
        out_label = f"[v{i}]" if i < len(segments) - 1 else "[vout]"
        start = f"{seg['start_s']:.3f}"
        end = f"{seg['end_s']:.3f}"
        parts.append(
            f"{prev}[{input_idx}]overlay=0:0:enable='between(t,{start},{end})'{out_label}"
        )
        prev = out_label

    return png_paths, ";".join(parts)


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
    """Concatenate clips with xfade transitions between each pair."""
    import logging as _logging

    _log = _logging.getLogger(__name__)

    if len(clip_paths) == 1:
        import shutil

        shutil.copy2(clip_paths[0], output_path)
        return output_path

    durations: list[float] = []
    for cp in clip_paths:
        dur = await _probe_duration(ffmpeg_bin, cp, run_subprocess)
        durations.append(dur or 5.0)

    input_args: list[str] = []
    for cp in clip_paths:
        input_args.extend(["-i", cp])

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

    args = [ffmpeg_bin, "-y"] + input_args + [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    rc, _, stderr = await run_subprocess(*args)
    if rc != 0:
        _log.warning(f"xfade concat failed, falling back to simple concat: {stderr}")
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
    import random

    from app.core.config import settings

    bgm_dir = Path(settings.BGM_DIR)
    if not bgm_dir.exists():
        return None

    mood_dir = bgm_dir / mood
    candidates: list[Path] = []
    if mood_dir.is_dir():
        candidates = list(mood_dir.glob("*.mp3")) + list(mood_dir.glob("*.wav"))
    if not candidates:
        candidates = list(bgm_dir.glob(f"{mood}*.mp3")) + list(bgm_dir.glob(f"{mood}*.wav"))
    if not candidates:
        candidates = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav"))

    if candidates:
        return str(random.choice(candidates))
    return None
