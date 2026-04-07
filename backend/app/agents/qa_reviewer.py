from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.config import settings
from app.prompts import QA_REVIEWER_SYSTEM_PROMPT
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

# Category → which agent to retry
_RECOMMENDATION_MAP: dict[str, str] = {
    "missing_clips": "retry_video_generator",
    "duration_mismatch": "retry_editor",
    "av_sync": "retry_audio",
    "partial_coverage": "retry_video_generator",
    "prompt_quality": "retry_editor",
}


class QAReviewerAgent(BaseAgent):
    """Reviews assembled pipeline output for quality issues before delivery.

    Combines two layers of checking:
    1. Hard-coded rule checks (missing clips, timing drift, A/V sync)
    2. LLM-based holistic review (prompt quality, script coverage, platform fit)

    Output ``output_data`` keys:
        - ``passed`` (bool): True if no critical issues were found.
        - ``overall_score`` (float 0–1): Composite quality estimate.
        - ``issues`` (list[dict]): Severity / category / message per issue.
        - ``recommendation`` (str): "pass" | "retry_video_generator" |
          "retry_audio" | "retry_editor"
    """

    name = "qa_reviewer"

    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        shot_prompts: list[dict] = input_data.get("shot_prompts", [])
        video_clips: list[dict] = input_data.get("video_clips", [])
        audio: dict = input_data.get("audio", {})
        final_video: dict = input_data.get("final_video", {})
        input_config: dict = input_data.get("input_config", {})

        # ── Build compact review payload ──────────────────────────────────────
        clip_indices = {c.get("shot_idx") for c in video_clips}
        missing_clip_indices = [
            s.get("shot_idx")
            for s in shot_prompts
            if s.get("shot_idx") not in clip_indices
        ]

        payload: dict[str, Any] = {
            "shot_count": len(shot_prompts),
            "clip_count": len(video_clips),
            "shots": [
                {
                    "shot_idx": s.get("shot_idx"),
                    "duration_seconds": s.get("duration_seconds"),
                    "prompt_word_count": len(s.get("video_prompt", "").split()),
                    "has_camera_motion": any(
                        kw in s.get("video_prompt", "").lower()
                        for kw in ("dolly", "pan", "push", "pull", "zoom", "track", "tilt")
                    ),
                }
                for s in shot_prompts
            ],
            "audio_duration_seconds": audio.get("total_duration_seconds"),
            "video_total_seconds": sum(
                float(c.get("duration_seconds", 0)) for c in video_clips
            ),
            "final_video_duration": final_video.get("total_duration_seconds"),
            "target_duration": input_config.get("duration_seconds"),
            "platform": input_config.get("platform", "generic"),
            "script_length": len(input_config.get("script", "")),
        }
        if missing_clip_indices:
            payload["missing_clip_indices"] = missing_clip_indices

        # ── Hard-coded rule checks (always run) ───────────────────────────────
        hard_issues = self._run_hard_checks(payload)

        # ── LLM-based review ──────────────────────────────────────────────────
        llm_report = await self._llm_review(payload)

        # ── Merge results ─────────────────────────────────────────────────────
        all_issues = hard_issues + llm_report.get("issues", [])

        # Hard critical issues override the LLM's "passed" verdict
        has_critical = any(i["severity"] == "critical" for i in all_issues)
        passed = llm_report.get("passed", True) and not has_critical

        if has_critical:
            # Pick the most relevant retry target from critical issues
            recommendation = "retry_video_generator"
            for issue in all_issues:
                if issue["severity"] == "critical":
                    recommendation = _RECOMMENDATION_MAP.get(
                        issue.get("category", ""), "retry_video_generator"
                    )
                    break
        else:
            recommendation = llm_report.get("recommendation", "pass") if not passed else "pass"

        # Clamp score
        score = float(llm_report.get("overall_score", 0.8))
        if has_critical:
            score = min(score, 0.4)
        elif any(i["severity"] == "warning" for i in all_issues):
            score = min(score, 0.75)

        report = {
            "passed": passed,
            "overall_score": round(score, 3),
            "issues": all_issues,
            "recommendation": recommendation,
        }

        logger.info(
            f"[{context.trace_id}] QA review: passed={passed}, "
            f"score={score:.2f}, issues={len(all_issues)}, "
            f"recommendation={recommendation}"
        )

        return AgentResult(
            success=True,
            output_data=report,
            usage_records=llm_report.get("_usage_records", []),
        )

    # ── LLM review ───────────────────────────────────────────────────────────

    async def _llm_review(self, payload: dict) -> dict:
        """Ask the LLM to review the payload.  Returns partial qa_report dict."""
        qa_schema = {
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "overall_score": {"type": "number"},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string"},
                            "category": {"type": "string"},
                            "message": {"type": "string"},
                        },
                        "required": ["severity", "category", "message"],
                    },
                },
                "recommendation": {"type": "string"},
            },
            "required": ["passed", "overall_score", "issues", "recommendation"],
        }

        try:
            result, usage = await self.llm.generate_structured(
                system_prompt=QA_REVIEWER_SYSTEM_PROMPT,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                schema=qa_schema,
            )
        except Exception as exc:
            logger.warning(f"QA reviewer LLM call failed: {exc} — using defaults")
            return {
                "passed": True,
                "overall_score": 0.75,
                "issues": [
                    {
                        "severity": "info",
                        "category": "qa_skipped",
                        "message": f"LLM-based QA review skipped due to error: {exc}",
                    }
                ],
                "recommendation": "pass",
            }

        usage_records = []
        if usage:
            usage_records = [
                {
                    "provider": "qwen",
                    "model_name": settings.QWEN_OMNI_MODEL,
                    "operation": "qa_review",
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            ]
        result["_usage_records"] = usage_records
        return result

    # ── Hard-coded rule checks ────────────────────────────────────────────────

    def _run_hard_checks(self, payload: dict) -> list[dict]:
        issues: list[dict] = []

        # 1. Missing clips
        if payload.get("missing_clip_indices"):
            issues.append(
                {
                    "severity": "critical",
                    "category": "missing_clips",
                    "message": (
                        f"Missing video clips for shots: "
                        f"{payload['missing_clip_indices']}"
                    ),
                }
            )

        # 2. Duration compliance
        target = payload.get("target_duration")
        actual = payload.get("final_video_duration") or payload.get("video_total_seconds")
        if target and actual:
            drift = abs(float(actual) - float(target)) / max(float(target), 1.0)
            if drift > 0.30:
                sev = "critical" if drift > 0.50 else "warning"
                issues.append(
                    {
                        "severity": sev,
                        "category": "duration_mismatch",
                        "message": (
                            f"Video duration {actual:.1f}s vs target {target:.1f}s "
                            f"(drift {drift * 100:.0f}%)"
                        ),
                    }
                )

        # 3. Audio/video sync
        audio_dur = payload.get("audio_duration_seconds")
        video_dur = payload.get("video_total_seconds")
        if audio_dur and video_dur:
            diff = abs(float(audio_dur) - float(video_dur))
            if diff > 5.0:
                issues.append(
                    {
                        "severity": "warning",
                        "category": "av_sync",
                        "message": (
                            f"Audio ({audio_dur:.1f}s) and video ({video_dur:.1f}s) "
                            f"differ by {diff:.1f}s"
                        ),
                    }
                )

        # 4. Partial clip coverage
        shot_count = payload.get("shot_count", 0)
        clip_count = payload.get("clip_count", 0)
        if shot_count > 0 and 0 < clip_count < shot_count:
            issues.append(
                {
                    "severity": "warning",
                    "category": "partial_coverage",
                    "message": (
                        f"Only {clip_count}/{shot_count} shots have video clips"
                    ),
                }
            )

        # 5. Prompt quality — flag shots without camera motion
        shots_without_motion = [
            s["shot_idx"]
            for s in payload.get("shots", [])
            if not s.get("has_camera_motion")
        ]
        if shots_without_motion and len(shots_without_motion) == shot_count:
            issues.append(
                {
                    "severity": "warning",
                    "category": "prompt_quality",
                    "message": "No shots include a camera motion keyword "
                               "(dolly, pan, push, zoom, tilt, track)",
                }
            )

        return issues
