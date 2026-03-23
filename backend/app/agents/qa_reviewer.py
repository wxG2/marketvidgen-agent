from __future__ import annotations

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.services.quality_assessor import QualityAssessor


class QAReviewerAgent(BaseAgent):
    """Quality gate: multi-dimension assessment with rollback decisions."""

    name = "qa_reviewer"

    def __init__(self, quality_assessor: QualityAssessor):
        self.assessor = quality_assessor

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        video_path: str = input_data["final_video_path"]
        script: str = input_data.get("script", "")
        audio_path: str = input_data.get("audio_path", "")

        report = await self.assessor.assess(
            video_path=video_path,
            script=script,
            audio_path=audio_path,
            target_duration_seconds=input_data.get("target_duration_seconds"),
            duration_mode=input_data.get("duration_mode", "fixed"),
        )

        output = {
            "overall_score": report.overall_score,
            "passed": report.passed,
            "rollback_level": report.rollback_level,
            "rollback_target": report.rollback_target,
            "issues": [
                {
                    "type": issue.type,
                    "severity": issue.severity,
                    "timestamp": issue.timestamp,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                }
                for issue in report.issues
            ],
            "retry_count": report.retry_count,
            "dimension_scores": report.dimension_scores,
        }

        usage_records = []
        if report.usage:
            usage_records.append({
                "provider": "qwen",
                "model_name": getattr(getattr(self.assessor, "client", None), "model", "mock"),
                "operation": "quality_assessment",
                **report.usage,
            })
        return AgentResult(success=True, output_data=output, usage_records=usage_records)
