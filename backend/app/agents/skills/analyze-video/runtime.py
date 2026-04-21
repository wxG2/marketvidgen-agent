from __future__ import annotations

from app.models.video_upload import VideoUpload
from app.prompts import VIDEO_ANALYSIS_SYSTEM_PROMPT


def create_analyze_video_skill(llm_service, db_factory):
    async def analyze_video(
        *,
        project_id: str,
        session_id: str,
        user_id: str,
        reference_video_id: str,
        focus: str = "",
        force_reanalyze: bool = False,
        **kwargs,
    ) -> dict:
        async with db_factory() as db:
            upload = await db.get(VideoUpload, reference_video_id)
            if not upload or upload.project_id != project_id:
                raise ValueError("参考视频不存在。")

            video_path = upload.file_path
            cached_report = upload.analysis_report

        normalized_focus = (focus or "").strip()

        # Return cached report if available and no specific focus or force flag
        if cached_report and not normalized_focus and not force_reanalyze:
            return {
                "status": "completed",
                "analysis_report": cached_report,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "from_cache": True,
            }

        user_prompt = (
            "请对这段视频进行全面专业的多维度解析报告。"
            "使用中文输出，按【视频概述】【镜头方案】【视觉风格】【配音与口播】【音乐设计】【营销策略】【复刻建议】七个维度，"
            "每个维度标题加【】标注，内容连贯详细，每个观察都说明原因和营销作用。"
        )
        if normalized_focus:
            user_prompt += f"\n\n用户特别关注：{normalized_focus}"

        analysis_report, usage = await llm_service.generate_text(
            system_prompt=VIDEO_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            video_paths=[video_path],
        )
        analysis_report = analysis_report.strip()
        if not analysis_report:
            raise ValueError("视频解析模型返回空报告，请检查模型调用、视频输入或模型服务配置。")

        # Persist to DB (only when no custom focus, so it's the full generic report)
        if not normalized_focus:
            async with db_factory() as db:
                upload = await db.get(VideoUpload, reference_video_id)
                if upload:
                    upload.analysis_report = analysis_report
                    await db.commit()

        return {
            "status": "completed",
            "analysis_report": analysis_report,
            "usage": usage,
        }

    return analyze_video
