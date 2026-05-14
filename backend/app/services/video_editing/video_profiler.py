from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass

from app.core.config import settings
from app.services.keyframe_extractor import KeyframeExtractor
from app.services.media_utils import run_subprocess
from app.services.video_editing.helpers import _probe_duration

logger = logging.getLogger(__name__)


@dataclass
class ShotProfile:
    video_id: str
    shot_idx: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    keyframe_path: str
    scene_change_score: float
    audio_mean_volume: float
    audio_max_volume: float
    description: str = ""
    emotion_tag: str = ""
    visual_quality_score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VideoProfile:
    video_id: str
    video_path: str
    duration_seconds: float
    shots: list[ShotProfile]

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "video_path": self.video_path,
            "duration_seconds": self.duration_seconds,
            "total_shots": len(self.shots),
            "shots": [shot.to_dict() for shot in self.shots],
        }


class VideoProfiler:
    """Build lightweight shot metadata for remix planning."""

    def __init__(
        self,
        keyframe_extractor: KeyframeExtractor,
        *,
        ffmpeg_bin: str | None = None,
        output_root: str | None = None,
    ):
        self.keyframe_extractor = keyframe_extractor
        self.ffmpeg_bin = ffmpeg_bin or settings.FFMPEG_BIN
        self.output_root = output_root or settings.GENERATED_DIR

    async def profile_video(self, video_path: str, video_id: str) -> VideoProfile:
        duration = await _probe_duration(self.ffmpeg_bin, video_path, run_subprocess) or 0.0
        keyframe_dir = os.path.join(self.output_root, "remix_keyframes", video_id)
        frames = await self.keyframe_extractor.extract(
            video_path,
            strategy="scene_change",
            max_frames=settings.KEYFRAME_MAX_EXTRACT,
            output_dir=keyframe_dir,
        )
        if not frames:
            frames = await self.keyframe_extractor.extract(
                video_path,
                strategy="uniform",
                max_frames=min(8, settings.KEYFRAME_MAX_EXTRACT),
                output_dir=keyframe_dir,
            )
        shots = await self._build_shots(video_path, video_id, duration, frames)
        return VideoProfile(
            video_id=video_id,
            video_path=video_path,
            duration_seconds=round(duration, 3),
            shots=shots,
        )

    async def _build_shots(
        self,
        video_path: str,
        video_id: str,
        duration: float,
        frames: list[dict],
    ) -> list[ShotProfile]:
        normalized = sorted(
            (
                {
                    "timestamp_seconds": max(float(frame.get("timestamp_seconds") or 0), 0.0),
                    "frame_path": str(frame.get("frame_path") or ""),
                }
                for frame in frames
            ),
            key=lambda item: item["timestamp_seconds"],
        )
        if duration <= 0:
            duration = max((item["timestamp_seconds"] for item in normalized), default=0.0) + 5.0

        boundaries = [0.0]
        for item in normalized:
            ts = min(item["timestamp_seconds"], duration)
            if ts > 0.05 and all(abs(ts - existing) > 0.05 for existing in boundaries):
                boundaries.append(ts)
        boundaries = sorted(boundaries)

        if len(boundaries) == 1 and normalized:
            step = max(duration / max(len(normalized), 1), 1.0)
            boundaries = [round(i * step, 3) for i in range(len(normalized)) if i * step < duration]
            if not boundaries or boundaries[0] != 0:
                boundaries.insert(0, 0.0)

        shots: list[ShotProfile] = []
        for idx, start in enumerate(boundaries):
            end = boundaries[idx + 1] if idx + 1 < len(boundaries) else duration
            if end - start < 0.3:
                continue
            frame = self._nearest_frame(normalized, start)
            mean_volume, max_volume = await self._measure_audio(video_path, start, end)
            shots.append(
                ShotProfile(
                    video_id=video_id,
                    shot_idx=len(shots),
                    start_seconds=round(start, 3),
                    end_seconds=round(end, 3),
                    duration_seconds=round(end - start, 3),
                    keyframe_path=frame.get("frame_path", "") if frame else "",
                    scene_change_score=0.0 if start == 0 else 1.0,
                    audio_mean_volume=mean_volume,
                    audio_max_volume=max_volume,
                )
            )
        return shots

    async def _measure_audio(self, video_path: str, start: float, end: float) -> tuple[float, float]:
        duration = max(end - start, 0.1)
        rc, _, stderr = await run_subprocess(
            self.ffmpeg_bin,
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            video_path,
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        )
        if rc != 0:
            logger.debug("volumedetect failed for %s %.2f-%.2f: %s", video_path, start, end, stderr)
        return self._parse_volume(stderr)

    @staticmethod
    def _parse_volume(stderr: str) -> tuple[float, float]:
        mean = _volume_value(stderr, "mean_volume")
        max_volume = _volume_value(stderr, "max_volume")
        return mean if mean is not None else -90.0, max_volume if max_volume is not None else -90.0

    @staticmethod
    def _nearest_frame(frames: list[dict], timestamp: float) -> dict | None:
        if not frames:
            return None
        return min(frames, key=lambda item: abs(float(item.get("timestamp_seconds") or 0) - timestamp))


def _volume_value(stderr: str, label: str) -> float | None:
    match = re.search(rf"{label}:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", stderr)
    if not match:
        return None
    if match.group(1) == "-inf":
        return -90.0
    return float(match.group(1))
