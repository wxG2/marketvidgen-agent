from __future__ import annotations

import os
from pathlib import Path

from app.core.config import settings
from app.services.media_utils import run_subprocess


class ClipExtractorService:
    """Extract frame-accurate source clips for remix assembly."""

    def __init__(self, *, ffmpeg_bin: str | None = None):
        self.ffmpeg_bin = ffmpeg_bin or settings.FFMPEG_BIN

    async def extract_clip(
        self,
        source_path: str,
        start_seconds: float,
        end_seconds: float,
        output_path: str,
        *,
        include_audio: bool = True,
        target_width: int | None = None,
        target_height: int | None = None,
        target_fps: int | None = None,
    ) -> str:
        duration = max(float(end_seconds) - float(start_seconds), 0.1)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Build optional video filter for resolution/fps normalization.
        # Applied at extraction time so all clips are uniform before concat.
        vf_parts: list[str] = []
        if target_width and target_height:
            vf_parts.append(
                f"scale={target_width}:{target_height}"
                f":force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            )
        if target_fps:
            vf_parts.append(f"fps={target_fps}")
        vf_filter = ",".join(vf_parts)

        args = [
            self.ffmpeg_bin,
            "-y",
            "-ss",
            f"{float(start_seconds):.3f}",
            "-i",
            source_path,
            "-t",
            f"{duration:.3f}",
            "-map",
            "0:v:0",
        ]
        if vf_filter:
            args.extend(["-vf", vf_filter])
        if include_audio:
            args.extend([
                "-map",
                "0:a?",
                "-c:a",
                "aac",
            ])
        else:
            args.append("-an")
        args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", output_path])

        rc, _, stderr = await run_subprocess(*args)
        if rc != 0 and include_audio:
            silent_args = [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                f"{float(start_seconds):.3f}",
                "-i",
                source_path,
            ]
            if vf_filter:
                silent_args.extend(["-vf", vf_filter])
            silent_args.extend([
                "-f",
                "lavfi",
                "-t",
                f"{duration:.3f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                f"{duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                output_path,
            ])
            rc, _, stderr = await run_subprocess(*silent_args)
        if rc != 0:
            raise RuntimeError(f"ffmpeg clip extraction failed: {stderr}")
        output = Path(output_path)
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg clip extraction produced empty output: {output_path}")
        return output_path
