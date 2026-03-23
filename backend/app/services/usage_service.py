from __future__ import annotations

import json
import os
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models.pipeline import AgentExecution
from app.models.pipeline import PipelineRun
from app.models.usage import ModelUsage


class UsageRecorder:
    def __init__(self, db_session_factory: async_sessionmaker):
        self.db_session_factory = db_session_factory

    async def record(
        self,
        pipeline_run_id: str,
        trace_id: str,
        agent_name: str,
        provider: str,
        model_name: str,
        operation: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self.db_session_factory() as session:
            session.add(
                ModelUsage(
                    pipeline_run_id=pipeline_run_id,
                    trace_id=trace_id,
                    agent_name=agent_name,
                    provider=provider,
                    model_name=model_name,
                    operation=operation,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                    metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
                )
            )
            await session.commit()

    async def get_run_summary(self, pipeline_run_id: str) -> dict[str, Any]:
        async with self.db_session_factory() as session:
            totals_result = await session.execute(
                select(
                    func.coalesce(func.sum(ModelUsage.prompt_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.completion_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.total_tokens), 0),
                    func.count(ModelUsage.id),
                ).where(ModelUsage.pipeline_run_id == pipeline_run_id)
            )
            prompt_tokens, completion_tokens, total_tokens, request_count = totals_result.one()

            by_agent_result = await session.execute(
                select(
                    ModelUsage.agent_name,
                    func.coalesce(func.sum(ModelUsage.prompt_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.completion_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.total_tokens), 0),
                    func.count(ModelUsage.id),
                )
                .where(ModelUsage.pipeline_run_id == pipeline_run_id)
                .group_by(ModelUsage.agent_name)
                .order_by(ModelUsage.agent_name.asc())
            )

            by_model_result = await session.execute(
                select(
                    ModelUsage.provider,
                    ModelUsage.model_name,
                    func.coalesce(func.sum(ModelUsage.prompt_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.completion_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.total_tokens), 0),
                    func.count(ModelUsage.id),
                )
                .where(ModelUsage.pipeline_run_id == pipeline_run_id)
                .group_by(ModelUsage.provider, ModelUsage.model_name)
                .order_by(ModelUsage.provider.asc(), ModelUsage.model_name.asc())
            )

            return {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "request_count": request_count,
                "by_agent": [
                    {
                        "agent_name": row[0],
                        "prompt_tokens": row[1],
                        "completion_tokens": row[2],
                        "total_tokens": row[3],
                        "request_count": row[4],
                    }
                    for row in by_agent_result.all()
                ],
                "by_model": [
                    {
                        "provider": row[0],
                        "model_name": row[1],
                        "prompt_tokens": row[2],
                        "completion_tokens": row[3],
                        "total_tokens": row[4],
                        "request_count": row[5],
                    }
                    for row in by_model_result.all()
                ],
            }

    async def get_project_summary(self, project_id: str) -> dict[str, Any]:
        async with self.db_session_factory() as session:
            totals_result = await session.execute(
                select(
                    func.coalesce(func.sum(ModelUsage.prompt_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.completion_tokens), 0),
                    func.coalesce(func.sum(ModelUsage.total_tokens), 0),
                    func.count(ModelUsage.id),
                )
                .select_from(PipelineRun)
                .join(ModelUsage, ModelUsage.pipeline_run_id == PipelineRun.id, isouter=True)
                .where(PipelineRun.project_id == project_id)
            )
            prompt_tokens, completion_tokens, total_tokens, request_count = totals_result.one()

            pipeline_rows = await session.execute(
                select(
                    PipelineRun.id,
                    PipelineRun.status,
                    PipelineRun.current_agent,
                    PipelineRun.created_at,
                    func.coalesce(func.sum(ModelUsage.total_tokens), 0),
                    func.count(ModelUsage.id),
                )
                .select_from(PipelineRun)
                .join(ModelUsage, ModelUsage.pipeline_run_id == PipelineRun.id, isouter=True)
                .where(PipelineRun.project_id == project_id)
                .group_by(PipelineRun.id)
                .order_by(PipelineRun.created_at.desc())
            )
            pipelines = [
                {
                    "id": row[0],
                    "status": row[1],
                    "current_agent": row[2],
                    "created_at": row[3],
                    "total_tokens": row[4],
                    "request_count": row[5],
                }
                for row in pipeline_rows.all()
            ]
            latest = pipelines[0] if pipelines else None

            return {
                "project_id": project_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "request_count": request_count,
                "latest_pipeline_status": latest["status"] if latest else None,
                "latest_current_agent": latest["current_agent"] if latest else None,
                "pipelines": pipelines,
            }

    async def get_project_history(self, project_id: str) -> dict[str, Any]:
        async with self.db_session_factory() as session:
            pipeline_rows = await session.execute(
                select(
                    PipelineRun.id,
                    PipelineRun.status,
                    PipelineRun.current_agent,
                    PipelineRun.input_config,
                    PipelineRun.created_at,
                    PipelineRun.completed_at,
                    func.coalesce(func.sum(ModelUsage.total_tokens), 0),
                    func.count(ModelUsage.id),
                )
                .select_from(PipelineRun)
                .join(ModelUsage, ModelUsage.pipeline_run_id == PipelineRun.id, isouter=True)
                .where(PipelineRun.project_id == project_id)
                .group_by(PipelineRun.id)
                .order_by(PipelineRun.created_at.desc())
            )
            pipelines = pipeline_rows.all()
            if not pipelines:
                return {"project_id": project_id, "runs": []}

            run_ids = [row[0] for row in pipelines]
            execution_rows = await session.execute(
                select(AgentExecution)
                .where(AgentExecution.pipeline_run_id.in_(run_ids))
                .order_by(
                    AgentExecution.pipeline_run_id.asc(),
                    AgentExecution.created_at.asc(),
                    AgentExecution.attempt_number.asc(),
                )
            )

            execution_map: dict[str, list[AgentExecution]] = {}
            for execution in execution_rows.scalars().all():
                execution_map.setdefault(execution.pipeline_run_id, []).append(execution)

            runs = []
            for row in pipelines:
                run_id, status, current_agent, input_config, created_at, completed_at, total_tokens, request_count = row
                executions = execution_map.get(run_id, [])
                run_input = json.loads(input_config) if input_config else {}
                runs.append(
                    {
                        "run_id": run_id,
                        "status": status,
                        "created_at": created_at,
                        "completed_at": completed_at,
                        "current_agent": current_agent,
                        "total_tokens": total_tokens,
                        "request_count": request_count,
                        "input_script": run_input.get("script"),
                        **self._collect_run_artifacts(executions),
                    }
                )

            return {"project_id": project_id, "runs": runs}

    def _collect_run_artifacts(self, executions: list[AgentExecution]) -> dict[str, Any]:
        prompts: list[dict[str, Any]] = []
        voice_params: dict[str, Any] | None = None
        audio_files: list[dict[str, Any]] = []
        subtitle_files: list[dict[str, Any]] = []
        generated_videos: list[dict[str, Any]] = []
        final_videos: list[dict[str, Any]] = []

        seen_audio: set[str] = set()
        seen_subtitles: set[str] = set()
        seen_generated: set[str] = set()
        seen_final: set[str] = set()

        for execution in executions:
            if execution.status != "completed" or not execution.output_data:
                continue

            try:
                output_data = json.loads(execution.output_data)
            except json.JSONDecodeError:
                continue

            if execution.agent_name == "prompt_engineer":
                voice_params = output_data.get("voice_params") or voice_params
                prompts = [
                    {
                        "shot_idx": item.get("shot_idx", idx + 1),
                        "script_segment": item.get("script_segment"),
                        "video_prompt": item.get("video_prompt", ""),
                        "duration_seconds": item.get("duration_seconds"),
                    }
                    for idx, item in enumerate(output_data.get("shot_prompts", []))
                    if item.get("video_prompt")
                ] or prompts

            elif execution.agent_name == "audio_subtitle":
                audio_path = output_data.get("audio_path")
                subtitle_path = output_data.get("subtitle_path")
                duration_ms = output_data.get("duration_ms")
                if audio_path and audio_path not in seen_audio:
                    seen_audio.add(audio_path)
                    audio_files.append(
                        self._artifact_entry(
                            audio_path,
                            duration_ms=duration_ms,
                            kind="音频",
                        )
                    )
                if subtitle_path and subtitle_path not in seen_subtitles:
                    seen_subtitles.add(subtitle_path)
                    subtitle_files.append(
                        self._artifact_entry(
                            subtitle_path,
                            content=self._read_text_file(subtitle_path),
                            kind="字幕",
                        )
                    )

            elif execution.agent_name == "video_generator":
                for clip in output_data.get("video_clips", []):
                    video_path = clip.get("video_path")
                    if video_path and video_path not in seen_generated:
                        seen_generated.add(video_path)
                        generated_videos.append(
                            self._artifact_entry(
                                video_path,
                                shot_idx=clip.get("shot_idx"),
                                duration_ms=int((clip.get("duration_seconds") or 0) * 1000) or None,
                                kind="分镜视频",
                            )
                        )

            elif execution.agent_name == "video_editor":
                final_video_path = output_data.get("final_video_path")
                if final_video_path and final_video_path not in seen_final:
                    seen_final.add(final_video_path)
                    final_videos.append(
                        self._artifact_entry(
                            final_video_path,
                            duration_ms=output_data.get("duration_ms"),
                            kind="最终合成视频",
                        )
                    )

        prompts.sort(key=lambda item: item["shot_idx"])
        generated_videos.sort(key=lambda item: (item.get("shot_idx") or 0, item["name"]))

        return {
            "voice_params": voice_params,
            "prompts": prompts,
            "audio_files": audio_files,
            "subtitle_files": subtitle_files,
            "generated_videos": generated_videos,
            "final_videos": final_videos,
        }

    def _artifact_entry(
        self,
        path: str,
        *,
        content: str | None = None,
        shot_idx: int | None = None,
        duration_ms: int | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        filename = os.path.basename(path.rstrip("/")) or path
        return {
            "name": filename,
            "path": path,
            "url": self._path_to_url(path),
            "content": content,
            "shot_idx": shot_idx,
            "duration_ms": duration_ms,
            "kind": kind,
        }

    def _path_to_url(self, path: str) -> str:
        if path.startswith(("http://", "https://", "/api/", "/generated/", "/examples/")):
            return path

        normalized = path.replace("\\", "/")
        generated_root = settings.GENERATED_DIR.replace("\\", "/").lstrip("./")
        if generated_root and generated_root in normalized:
            filename = normalized.split(generated_root, 1)[1].lstrip("/")
            return f"/generated/{filename}"

        return path

    @staticmethod
    def _read_text_file(path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
