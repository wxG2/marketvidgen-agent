from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from app.prompts import QA_REVIEWER_SYSTEM_PROMPT
from app.services.qwen_client import QwenClient


@dataclass
class QualityIssue:
    type: str            # visual_quality | audio_sync | subtitle | compliance | style | pacing
    severity: str        # critical | major | minor
    timestamp: str       # e.g. "00:05-00:08"
    description: str
    suggestion: str


@dataclass
class QualityReport:
    overall_score: float           # 0-100, <70 triggers rollback
    passed: bool
    rollback_level: Optional[str]  # L1 | L2 | None
    rollback_target: Optional[str] # video_editor | video_generator | orchestrator | None
    issues: list[QualityIssue] = field(default_factory=list)
    retry_count: int = 0
    dimension_scores: dict[str, float] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


class QualityAssessor(ABC):
    @abstractmethod
    async def assess(
        self,
        video_path: str,
        script: str,
        audio_path: str,
        target_duration_seconds: int | None = None,
        duration_mode: str = "fixed",
    ) -> QualityReport:
        """Multi-dimension quality assessment of the composed video."""
        ...


class MockQualityAssessor(QualityAssessor):
    """Mock assessor that returns passing scores by default.

    Set `force_fail_mode` to test rollback:
      - None: always pass (default)
      - "L1_editor": first call fails with L1 editor rollback
      - "L1_vidgen": first call fails with L1 video generator rollback
      - "L2": first call fails with L2 orchestrator rollback
    """

    def __init__(self, force_fail_mode: Optional[str] = None):
        self.force_fail_mode = force_fail_mode
        self._call_count = 0

    async def assess(
        self,
        video_path: str,
        script: str,
        audio_path: str,
        target_duration_seconds: int | None = None,
        duration_mode: str = "fixed",
    ) -> QualityReport:
        await asyncio.sleep(2)
        self._call_count += 1

        dimension_scores = {
            "visual_quality": 88.0,
            "audio_sync": 85.0,
            "subtitle_accuracy": 90.0,
            "content_alignment": 82.0,
            "style_consistency": 87.0,
            "pacing": 84.0,
        }

        # On first call, optionally simulate failure for testing
        if self._call_count == 1 and self.force_fail_mode:
            return self._generate_failure(dimension_scores)

        overall_score = sum(dimension_scores.values()) / len(dimension_scores)
        return QualityReport(
            overall_score=overall_score,
            passed=True,
            rollback_level=None,
            rollback_target=None,
            issues=[],
            retry_count=self._call_count - 1,
            dimension_scores=dimension_scores,
            usage={},
        )

    def _generate_failure(self, dimension_scores: dict[str, float]) -> QualityReport:
        if self.force_fail_mode == "L1_editor":
            dimension_scores["audio_sync"] = 40.0
            dimension_scores["pacing"] = 45.0
            return QualityReport(
                overall_score=58.0,
                passed=False,
                rollback_level="L1",
                rollback_target="video_editor",
                issues=[
                    QualityIssue(
                        type="audio_sync",
                        severity="critical",
                        timestamp="00:05-00:12",
                        description="Audio and video are out of sync by ~500ms",
                        suggestion="Re-align audio track with video clips at the transition points",
                    ),
                    QualityIssue(
                        type="pacing",
                        severity="major",
                        timestamp="00:15-00:22",
                        description="Segment pacing is too slow, causing viewer drop-off risk",
                        suggestion="Tighten cuts and reduce hold time on static shots",
                    ),
                ],
                retry_count=0,
                dimension_scores=dimension_scores,
                usage={},
            )
        elif self.force_fail_mode == "L1_vidgen":
            dimension_scores["visual_quality"] = 35.0
            return QualityReport(
                overall_score=55.0,
                passed=False,
                rollback_level="L1",
                rollback_target="video_generator",
                issues=[
                    QualityIssue(
                        type="visual_quality",
                        severity="critical",
                        timestamp="00:08-00:13",
                        description="Shot #2 has visible human figure deformation and blur",
                        suggestion="Regenerate shot #2 with stronger guidance scale",
                    ),
                ],
                retry_count=0,
                dimension_scores=dimension_scores,
                usage={},
            )
        else:  # L2
            dimension_scores["content_alignment"] = 30.0
            dimension_scores["style_consistency"] = 35.0
            return QualityReport(
                overall_score=48.0,
                passed=False,
                rollback_level="L2",
                rollback_target="orchestrator",
                issues=[
                    QualityIssue(
                        type="style",
                        severity="critical",
                        timestamp="full",
                        description="Video style does not match user intent — product demo interpreted as brand story",
                        suggestion="Re-parse user requirements and regenerate all downstream content",
                    ),
                ],
                retry_count=0,
                dimension_scores=dimension_scores,
                usage={},
            )


class RealQualityAssessor(QualityAssessor):
    def __init__(self, api_key: str, api_url: str, model: str, ffmpeg_bin: str = "ffmpeg"):
        self.client = QwenClient(api_key=api_key, base_url=api_url, model=model)
        self.ffmpeg_bin = ffmpeg_bin

    async def assess(
        self,
        video_path: str,
        script: str,
        audio_path: str,
        target_duration_seconds: int | None = None,
        duration_mode: str = "fixed",
    ) -> QualityReport:
        schema = {
            "name": "quality_report",
            "schema": {
                "type": "object",
                "properties": {
                    "overall_score": {"type": "number"},
                    "passed": {"type": "boolean"},
                    "rollback_level": {"type": ["string", "null"]},
                    "rollback_target": {"type": ["string", "null"]},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "severity": {"type": "string"},
                                "timestamp": {"type": "string"},
                                "description": {"type": "string"},
                                "suggestion": {"type": "string"},
                            },
                            "required": ["type", "severity", "timestamp", "description", "suggestion"],
                        },
                    },
                    "dimension_scores": {
                        "type": "object",
                        "properties": {
                            "visual_quality": {"type": "number"},
                            "audio_sync": {"type": "number"},
                            "subtitle_accuracy": {"type": "number"},
                            "content_alignment": {"type": "number"},
                            "style_consistency": {"type": "number"},
                            "pacing": {"type": "number"},
                        },
                        "required": [
                            "visual_quality",
                            "audio_sync",
                            "subtitle_accuracy",
                            "content_alignment",
                            "style_consistency",
                            "pacing",
                        ],
                    },
                },
                "required": [
                    "overall_score",
                    "passed",
                    "rollback_level",
                    "rollback_target",
                    "issues",
                    "dimension_scores",
                ],
            },
        }
        duration_hint = ""
        if target_duration_seconds and duration_mode == "fixed":
            duration_hint = (
                f"\nIMPORTANT: The user requested a {target_duration_seconds}s video. "
                f"The audio was trimmed to fit this duration. "
                f"Do NOT penalize the video for audio being shorter than the full script — "
                f"this is expected behavior in fixed-duration mode. "
                f"Focus on visual quality, content alignment, and audio-video sync within the {target_duration_seconds}s window."
            )
        prompt = (
            "Assess the final marketing video quality. "
            "Return conservative rollback decisions: video_editor for sync/pacing issues, "
            "video_generator for visual defects, orchestrator for requirement misunderstanding.\n\n"
            f"Script:\n{script}\n\n"
            f"Audio path reference:\n{audio_path}"
            f"{duration_hint}"
        )
        image_paths = await self._extract_frames(video_path)
        data, usage = await self.client.chat_json(
            system_prompt=QA_REVIEWER_SYSTEM_PROMPT,
            user_prompt=prompt,
            image_paths=image_paths,
            response_schema=schema,
        )
        issues = [
            QualityIssue(
                type=issue["type"],
                severity=issue["severity"],
                timestamp=issue["timestamp"],
                description=issue["description"],
                suggestion=issue["suggestion"],
            )
            for issue in data.get("issues", [])
        ]
        return QualityReport(
            overall_score=float(data.get("overall_score", 0.0)),
            passed=bool(data.get("passed", False)),
            rollback_level=data.get("rollback_level"),
            rollback_target=data.get("rollback_target"),
            issues=issues,
            retry_count=0,
            dimension_scores=data.get("dimension_scores", {}),
            usage=usage,
        )

    async def _extract_frames(self, video_path: str) -> list[str]:
        import os
        import tempfile

        from app.services.media_utils import run_subprocess

        if not video_path or not os.path.exists(video_path):
            return []

        temp_dir = tempfile.mkdtemp(prefix="vidgen_qa_frames_")
        output_pattern = os.path.join(temp_dir, "frame_%02d.jpg")
        return_code, _, _ = await run_subprocess(
            self.ffmpeg_bin,
            "-y",
            "-i",
            video_path,
            "-vf",
            "fps=1/2",
            "-frames:v",
            "4",
            output_pattern,
        )
        if return_code != 0:
            return []
        return [
            os.path.join(temp_dir, name)
            for name in sorted(os.listdir(temp_dir))
            if name.endswith(".jpg")
        ]
