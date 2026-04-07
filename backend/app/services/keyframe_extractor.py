from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from app.config import settings
from app.services.media_utils import run_subprocess

logger = logging.getLogger(__name__)


class KeyframeExtractor(ABC):
    """Extract key frames from a video file."""

    @abstractmethod
    async def extract(
        self,
        video_path: str,
        *,
        strategy: str = "scene_change",
        max_frames: int = 10,
        threshold: float | None = None,
        output_dir: str | None = None,
    ) -> list[dict]:
        """Return list of dicts with frame_path, timestamp_seconds, frame_index."""
        ...


class FFmpegKeyframeExtractor(KeyframeExtractor):
    """Extract key frames using FFmpeg."""

    async def extract(
        self,
        video_path: str,
        *,
        strategy: str = "scene_change",
        max_frames: int = 10,
        threshold: float | None = None,
        output_dir: str | None = None,
    ) -> list[dict]:
        max_frames = min(max_frames, settings.KEYFRAME_MAX_EXTRACT)
        if threshold is None:
            threshold = settings.KEYFRAME_SCENE_THRESHOLD

        if output_dir is None:
            output_dir = os.path.join(settings.GENERATED_DIR, "keyframes")
        os.makedirs(output_dir, exist_ok=True)

        # Get video duration first
        duration = await self._get_duration(video_path)

        if strategy == "scene_change":
            return await self._extract_scene_change(video_path, max_frames, threshold, output_dir)
        elif strategy == "uniform":
            return await self._extract_uniform(video_path, max_frames, duration, output_dir)
        elif strategy == "interval":
            interval = max(1.0, duration / max_frames) if duration > 0 else 2.0
            return await self._extract_interval(video_path, interval, max_frames, output_dir)
        else:
            raise ValueError(f"Unknown extraction strategy: {strategy}")

    async def _get_duration(self, video_path: str) -> float:
        rc, stdout, stderr = await run_subprocess(
            settings.FFMPEG_BIN.replace("ffmpeg", "ffprobe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        )
        try:
            return float(stdout.strip())
        except (ValueError, TypeError):
            logger.warning(f"Could not determine video duration: {stderr}")
            return 0.0

    async def _extract_scene_change(
        self, video_path: str, max_frames: int, threshold: float, output_dir: str
    ) -> list[dict]:
        pattern = os.path.join(output_dir, "scene_%04d.jpg")
        rc, stdout, stderr = await run_subprocess(
            settings.FFMPEG_BIN,
            "-i", video_path,
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-vsync", "vfn",
            "-frames:v", str(max_frames),
            "-q:v", "2",
            pattern,
            "-y",
        )
        if rc != 0:
            logger.error(f"FFmpeg scene change extraction failed: {stderr}")
            return []

        # Parse timestamps from showinfo output in stderr
        timestamps = self._parse_showinfo_timestamps(stderr)
        return self._collect_frames(output_dir, "scene_", timestamps)

    async def _extract_uniform(
        self, video_path: str, num_frames: int, duration: float, output_dir: str
    ) -> list[dict]:
        if duration <= 0 or num_frames <= 0:
            return []
        # Calculate frame interval to get evenly spaced frames
        total_frames_cmd = f"select='not(mod(n\\,{max(1, int(1))}))'"
        # Use fps filter to get uniform frames
        fps_val = num_frames / duration if duration > 0 else 1
        pattern = os.path.join(output_dir, "uniform_%04d.jpg")
        rc, stdout, stderr = await run_subprocess(
            settings.FFMPEG_BIN,
            "-i", video_path,
            "-vf", f"fps={fps_val:.4f}",
            "-frames:v", str(num_frames),
            "-q:v", "2",
            pattern,
            "-y",
        )
        if rc != 0:
            logger.error(f"FFmpeg uniform extraction failed: {stderr}")
            return []

        frames = []
        for i in range(1, num_frames + 1):
            fpath = os.path.join(output_dir, f"uniform_{i:04d}.jpg")
            if os.path.exists(fpath):
                ts = (i - 1) * (duration / num_frames) if num_frames > 0 else 0
                frames.append({
                    "frame_path": fpath,
                    "timestamp_seconds": round(ts, 2),
                    "frame_index": i - 1,
                })
        return frames

    async def _extract_interval(
        self, video_path: str, interval: float, max_frames: int, output_dir: str
    ) -> list[dict]:
        fps_val = 1.0 / interval
        pattern = os.path.join(output_dir, "interval_%04d.jpg")
        rc, stdout, stderr = await run_subprocess(
            settings.FFMPEG_BIN,
            "-i", video_path,
            "-vf", f"fps={fps_val:.4f}",
            "-frames:v", str(max_frames),
            "-q:v", "2",
            pattern,
            "-y",
        )
        if rc != 0:
            logger.error(f"FFmpeg interval extraction failed: {stderr}")
            return []

        frames = []
        for i in range(1, max_frames + 1):
            fpath = os.path.join(output_dir, f"interval_{i:04d}.jpg")
            if os.path.exists(fpath):
                frames.append({
                    "frame_path": fpath,
                    "timestamp_seconds": round((i - 1) * interval, 2),
                    "frame_index": i - 1,
                })
        return frames

    def _parse_showinfo_timestamps(self, stderr: str) -> list[float]:
        """Parse pts_time values from FFmpeg showinfo filter output."""
        timestamps = []
        for match in re.finditer(r"pts_time:\s*([\d.]+)", stderr):
            timestamps.append(float(match.group(1)))
        return timestamps

    def _collect_frames(
        self, output_dir: str, prefix: str, timestamps: list[float]
    ) -> list[dict]:
        frames = []
        i = 0
        while True:
            fpath = os.path.join(output_dir, f"{prefix}{i + 1:04d}.jpg")
            if not os.path.exists(fpath):
                break
            ts = timestamps[i] if i < len(timestamps) else 0.0
            frames.append({
                "frame_path": fpath,
                "timestamp_seconds": round(ts, 2),
                "frame_index": i,
            })
            i += 1
        return frames


class MockKeyframeExtractor(KeyframeExtractor):
    """Return placeholder frames for testing."""

    async def extract(
        self,
        video_path: str,
        *,
        strategy: str = "scene_change",
        max_frames: int = 10,
        threshold: float | None = None,
        output_dir: str | None = None,
    ) -> list[dict]:
        # Return mock frame entries without actual files
        num = min(max_frames, 5)
        return [
            {
                "frame_path": f"/mock/keyframes/frame_{i:04d}.jpg",
                "timestamp_seconds": round(i * 3.0, 2),
                "frame_index": i,
            }
            for i in range(num)
        ]
