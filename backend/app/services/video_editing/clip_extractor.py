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
    ) -> str:
        duration = max(float(end_seconds) - float(start_seconds), 0.1)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

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
            ]
            rc, _, stderr = await run_subprocess(*silent_args)
        if rc != 0:
            raise RuntimeError(f"ffmpeg clip extraction failed: {stderr}")
        if not Path(output_path).exists():
            raise RuntimeError("ffmpeg clip extraction did not produce output")
        return output_path
