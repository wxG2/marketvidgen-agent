from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from pathlib import Path

from app.config import settings
from app.database import get_db
from app.models.material import Material
from app.models.pipeline import PipelineRun, AgentExecution
from app.schemas.pipeline import (
    PipelineCreateRequest,
    PipelineRunResponse,
    AgentExecutionResponse,
    PipelineUsageResponse,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
    PrefightCheckRequest,
    PrefightCheckResponse,
)
from app.agents.pipeline import PipelineExecutor
from app.services.usage_service import UsageRecorder
from app.database import async_session


SCRIPT_GENERATION_PROMPT = """你是一名短视频脚本创作专家。用户会提供一组图片素材，请你仔细观察每张图片的内容、场景、氛围，然后为这些图片撰写一段适合短视频旁白/口播的中文脚本。

要求：
- 脚本应该是连贯的一段话，适合 TTS 口播朗读
- 语言生动、有感染力，适合营销/种草/品牌宣传类短视频
- 根据图片数量控制脚本长度，每张图片大约对应1-2句话
- 不要输出分镜编号或拍摄指导，只输出纯旁白文案
- 如果图片内容涉及商业场景（门店、产品等），要突出卖点和氛围"""


_launch_locks: dict[str, asyncio.Lock] = {}


def _get_launch_lock(project_id: str) -> asyncio.Lock:
    lock = _launch_locks.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _launch_locks[project_id] = lock
    return lock


def get_pipeline_router(executor: PipelineExecutor) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["pipeline"])

    @router.post("/projects/{project_id}/pipeline", response_model=PipelineRunResponse)
    async def launch_pipeline(project_id: str, req: PipelineCreateRequest, db: AsyncSession = Depends(get_db)):
        """Launch a new pipeline run as a background task.

        Deduplication: if a pending or running pipeline already exists for this
        project, the existing run is returned instead of creating a duplicate.
        """
        async with _get_launch_lock(project_id):
            # --- deduplication check ---
            existing_result = await db.execute(
                select(PipelineRun)
                .where(
                    PipelineRun.project_id == project_id,
                    PipelineRun.status.in_(["pending", "running"]),
                )
                .order_by(PipelineRun.created_at.desc())
                .limit(1)
            )
            existing_run = existing_result.scalars().first()
            if existing_run is not None:
                return existing_run

            # --- resolve watermark image path if provided ---
            watermark_path = None
            if req.watermark_image_id:
                wm_material = await db.get(Material, req.watermark_image_id)
                if wm_material and wm_material.file_path:
                    wm_full = Path(settings.MATERIALS_ROOT) / wm_material.file_path
                    if wm_full.exists():
                        watermark_path = str(wm_full.resolve())

            # --- create new run ---
            input_config = req.model_dump()
            if watermark_path:
                input_config["watermark_path"] = watermark_path

            run = PipelineRun(
                project_id=project_id,
                status="pending",
                input_config=json.dumps(input_config, ensure_ascii=False),
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)

        # Fire and forget — pipeline runs in background
        asyncio.create_task(_run_pipeline(executor, run.id, project_id, input_config))

        return run

    @router.get("/projects/{project_id}/pipelines", response_model=list[PipelineRunResponse])
    async def list_pipelines(project_id: str, db: AsyncSession = Depends(get_db)):
        """List all pipeline runs for a project."""
        result = await db.execute(
            select(PipelineRun)
            .where(PipelineRun.project_id == project_id)
            .order_by(PipelineRun.created_at.desc())
        )
        return result.scalars().all()

    @router.get("/projects/{project_id}/pipeline/{run_id}", response_model=PipelineRunResponse)
    async def get_pipeline_run(project_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
        """Get pipeline run status."""
        run = await db.get(PipelineRun, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        return run

    @router.get("/projects/{project_id}/pipeline/{run_id}/agents", response_model=list[AgentExecutionResponse])
    async def get_agent_executions(project_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
        """List all agent executions for a pipeline run."""
        # Verify run exists
        run = await db.get(PipelineRun, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")

        result = await db.execute(
            select(AgentExecution)
            .where(AgentExecution.pipeline_run_id == run_id)
            .order_by(AgentExecution.created_at.asc())
        )
        executions = result.scalars().all()
        items = []
        for execution in executions:
            items.append(
                AgentExecutionResponse(
                    id=execution.id,
                    agent_name=execution.agent_name,
                    status=execution.status,
                    attempt_number=execution.attempt_number,
                    input_data=json.loads(execution.input_data) if execution.input_data else None,
                    output_data=json.loads(execution.output_data) if execution.output_data else None,
                    duration_ms=execution.duration_ms,
                    error_message=execution.error_message,
                    created_at=execution.created_at,
                    completed_at=execution.completed_at,
                )
            )
        return items

    @router.get("/projects/{project_id}/pipeline/{run_id}/usage", response_model=PipelineUsageResponse)
    async def get_pipeline_usage(project_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
        run = await db.get(PipelineRun, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        recorder = UsageRecorder(async_session)
        return await recorder.get_run_summary(run_id)

    @router.get("/projects/{project_id}/pipeline/{run_id}/stream")
    async def stream_pipeline(project_id: str, run_id: str):
        """SSE stream that pushes run status + agent executions every 2s until terminal."""

        log = logging.getLogger(__name__)

        async def _event_generator() -> AsyncGenerator[dict, None]:
            while True:
                try:
                    async with async_session() as session:
                        run = await session.get(PipelineRun, run_id)
                        if not run or run.project_id != project_id:
                            yield {"event": "error", "data": json.dumps({"detail": "not found"})}
                            return

                        run_data = PipelineRunResponse.model_validate(run).model_dump(mode="json")

                        result = await session.execute(
                            select(AgentExecution)
                            .where(AgentExecution.pipeline_run_id == run_id)
                            .order_by(AgentExecution.created_at.asc())
                        )
                        execs = result.scalars().all()
                        agents_data = [
                            AgentExecutionResponse(
                                id=e.id,
                                agent_name=e.agent_name,
                                status=e.status,
                                attempt_number=e.attempt_number,
                                input_data=json.loads(e.input_data) if e.input_data else None,
                                output_data=json.loads(e.output_data) if e.output_data else None,
                                duration_ms=e.duration_ms,
                                error_message=e.error_message,
                                created_at=e.created_at,
                                completed_at=e.completed_at,
                            ).model_dump(mode="json")
                            for e in execs
                        ]

                        payload = json.dumps({"run": run_data, "agents": agents_data})
                        yield {"event": "update", "data": payload}

                        if run.status in ("completed", "failed", "cancelled"):
                            yield {"event": "done", "data": payload}
                            return
                except Exception as exc:
                    log.warning(f"SSE stream error for run {run_id}: {exc}")
                    yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
                    return

                await asyncio.sleep(2)

        return EventSourceResponse(_event_generator())

    @router.post("/projects/{project_id}/pipeline/{run_id}/retry-agent")
    async def retry_failed_agent(project_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
        """Re-run only the last failed agent, reconstructing context from prior successful executions."""
        run = await db.get(PipelineRun, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status != "failed":
            raise HTTPException(status_code=400, detail="Only failed pipelines can be retried")

        # Find the failed agent execution (latest)
        result = await db.execute(
            select(AgentExecution)
            .where(
                AgentExecution.pipeline_run_id == run_id,
                AgentExecution.status == "failed",
            )
            .order_by(AgentExecution.created_at.desc())
            .limit(1)
        )
        failed_exec = result.scalars().first()
        if not failed_exec:
            raise HTTPException(status_code=400, detail="No failed agent execution found")

        # Get all successful executions to rebuild context
        result = await db.execute(
            select(AgentExecution)
            .where(
                AgentExecution.pipeline_run_id == run_id,
                AgentExecution.status == "completed",
            )
            .order_by(AgentExecution.created_at.asc())
        )
        completed_execs = result.scalars().all()

        # Rebuild artifacts from completed executions
        artifacts: dict = {}
        agent_to_artifact_key = {
            "orchestrator": "orchestrator_plan",
            "prompt_engineer": "prompt_plan",
            "audio_subtitle": "audio",
            "video_generator": "video_clips",
            "video_editor": "final_video",
        }
        for exec_record in completed_execs:
            if exec_record.output_data:
                key = agent_to_artifact_key.get(exec_record.agent_name)
                if key:
                    artifacts[key] = json.loads(exec_record.output_data)

        # Reconstruct input_data for the failed agent
        failed_input = json.loads(failed_exec.input_data) if failed_exec.input_data else {}
        input_config = json.loads(run.input_config) if run.input_config else {}

        # Reset pipeline status
        run.status = "running"
        run.current_agent = failed_exec.agent_name
        run.error_message = None
        run.retry_count += 1
        run.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(run)

        # Fire background task to retry the failed agent
        asyncio.create_task(
            _retry_agent(
                executor, run.id, project_id, failed_exec.agent_name,
                failed_input, input_config, artifacts,
            )
        )

        return run

    @router.post("/projects/{project_id}/pipeline/{run_id}/cancel")
    async def cancel_pipeline(project_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
        """Cancel a running pipeline."""
        run = await db.get(PipelineRun, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        if run.status not in ("pending", "running"):
            raise HTTPException(status_code=400, detail=f"Cannot cancel pipeline in '{run.status}' status")

        run.status = "cancelled"
        run.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "cancelled"}

    @router.post("/projects/{project_id}/generate-script", response_model=ScriptGenerateResponse)
    async def generate_script(project_id: str, req: ScriptGenerateRequest, db: AsyncSession = Depends(get_db)):
        """Analyze selected images and generate a suitable video script."""
        result = await db.execute(
            select(Material).where(Material.id.in_(req.image_ids))
        )
        materials_list = result.scalars().all()
        root = Path(settings.MATERIALS_ROOT)
        image_paths = [str((root / m.file_path).resolve()) for m in materials_list if m.file_path]

        if not image_paths:
            raise HTTPException(status_code=400, detail="No valid images found")

        llm = executor.orchestrator.llm
        schema = {
            "name": "script_output",
            "schema": {
                "type": "object",
                "properties": {
                    "script": {"type": "string"},
                },
                "required": ["script"],
            },
        }
        try:
            output, _ = await llm.generate_structured(
                system_prompt=SCRIPT_GENERATION_PROMPT,
                user_prompt=f"请根据以下 {len(image_paths)} 张图片素材撰写短视频脚本。",
                schema=schema,
                image_paths=image_paths,
            )
            script_text = output.get("script", "")
            if not script_text:
                raise ValueError("LLM returned empty script")
            return ScriptGenerateResponse(script=script_text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"脚本生成失败：{e}")

    @router.post("/projects/{project_id}/preflight-check", response_model=PrefightCheckResponse)
    async def preflight_check(project_id: str, req: PrefightCheckRequest):
        """Pre-launch check: estimate audio duration vs available video capacity."""
        from app.config import settings as _settings

        script = req.script
        image_count = req.image_count
        duration_mode = req.duration_mode
        duration_seconds = req.duration_seconds
        supported = _settings.SEEDANCE_SUPPORTED_DURATIONS
        max_d = max(supported)

        # Estimate TTS audio duration (~4 Chinese chars/sec, ~8 English chars/sec)
        cn_chars = sum(1 for c in script if '\u4e00' <= c <= '\u9fff')
        other_chars = len(script) - cn_chars
        estimated_audio_s = cn_chars / 4.0 + other_chars / 8.0

        # Max video duration achievable with current images
        max_video_s = image_count * max_d

        # In fixed mode, max video is the user's target
        if duration_mode == "fixed":
            effective_video_s = duration_seconds
        else:
            effective_video_s = max_video_s

        # How many images needed to cover the audio
        import math
        recommended_count = max(math.ceil(estimated_audio_s / max_d), 1)

        # Rough token cost estimate:
        # - Orchestrator: ~2k tokens per image (vision analysis)
        # - Prompt Engineer: ~1k tokens per shot
        # - Video Editor: ~500 tokens for edit plan
        # - TTS/Audio: not token-based, negligible
        estimated_tokens = (
            image_count * 2000  # orchestrator vision
            + image_count * 1000  # prompt engineer per-shot
            + 500  # editor plan
            + len(script) * 2  # script encoding overhead
        )

        if estimated_audio_s > effective_video_s * 1.3:
            shortfall = estimated_audio_s - effective_video_s
            extra_needed = recommended_count - image_count
            if duration_mode == "fixed":
                warning = (
                    f"脚本约 {len(script)} 字，预计口播 {estimated_audio_s:.0f}s，"
                    f"但目标视频仅 {duration_seconds}s。"
                    f"建议缩短脚本，或将时长增加到 {int(estimated_audio_s) + 1}s 以上"
                    f"（需至少 {recommended_count} 张素材）。"
                )
            else:
                warning = (
                    f"脚本约 {len(script)} 字，预计口播 {estimated_audio_s:.0f}s，"
                    f"但当前 {image_count} 张素材最多支撑 {max_video_s}s 视频。"
                    f"建议再补充 {max(extra_needed, 1)} 张素材"
                    f"（总共至少 {recommended_count} 张），或缩短脚本。"
                )
            return PrefightCheckResponse(
                ok=False,
                warning=warning,
                estimated_audio_seconds=round(estimated_audio_s, 1),
                max_video_seconds=effective_video_s,
                recommended_image_count=recommended_count,
                estimated_tokens=estimated_tokens,
            )

        return PrefightCheckResponse(
            ok=True,
            estimated_audio_seconds=round(estimated_audio_s, 1),
            max_video_seconds=effective_video_s,
            recommended_image_count=image_count,
            estimated_tokens=estimated_tokens,
        )

    return router


async def _run_pipeline(executor: PipelineExecutor, run_id: str, project_id: str, input_config: dict):
    """Background task wrapper for pipeline execution."""
    try:
        await executor.run(run_id, project_id, input_config)
    except Exception:
        import logging
        logging.getLogger(__name__).error(f"Pipeline {run_id} background task failed", exc_info=True)


async def _retry_agent(
    executor: PipelineExecutor,
    run_id: str,
    project_id: str,
    agent_name: str,
    agent_input: dict,
    input_config: dict,
    artifacts: dict,
):
    """Background task: re-run a single failed agent and continue the pipeline from there."""
    import logging
    import uuid
    from app.agents.base import AgentContext
    from app.services.usage_service import UsageRecorder

    log = logging.getLogger(__name__)
    try:
        context = AgentContext(
            trace_id=str(uuid.uuid4()),
            pipeline_run_id=run_id,
            project_id=project_id,
            db_session_factory=async_session,
            usage_recorder=UsageRecorder(async_session),
            artifacts=artifacts,
        )

        # Map agent name to the executor's agent instance
        agent_map = {
            "orchestrator": executor.orchestrator,
            "prompt_engineer": executor.prompt_engineer,
            "audio_subtitle": executor.audio_agent,
            "video_generator": executor.video_gen_agent,
            "video_editor": executor.video_editor,
        }
        agent = agent_map.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        result = await agent.run(context, agent_input)

        if not result.success:
            raise RuntimeError(f"Agent {agent_name} retry failed: {result.error}")

        # Store the new output in artifacts
        agent_to_artifact_key = {
            "orchestrator": "orchestrator_plan",
            "prompt_engineer": "prompt_plan",
            "audio_subtitle": "audio",
            "video_generator": "video_clips",
            "video_editor": "final_video",
        }
        artifact_key = agent_to_artifact_key.get(agent_name)
        if artifact_key:
            context.artifacts[artifact_key] = result.output_data

        await _continue_pipeline_from_retry(executor, context, agent_name, input_config)

        # Mark completed
        final_video = context.artifacts.get("final_video", {}).get("final_video_path")
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run:
                if run.status == "cancelled":
                    return
                run.status = "completed"
                run.final_video_path = final_video
                run.completed_at = datetime.now(timezone.utc)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()

    except Exception as e:
        log.error(f"Retry agent {agent_name} for pipeline {run_id} failed: {e}", exc_info=True)
        async with async_session() as session:
            run = await session.get(PipelineRun, run_id)
            if run:
                if run.status == "cancelled":
                    return
                run.status = "failed"
                run.error_message = str(e)
                run.updated_at = datetime.now(timezone.utc)
                await session.commit()


def _build_agent_input(agent_name: str, artifacts: dict, input_config: dict) -> dict:
    """Build the input dict for a given agent from accumulated artifacts."""
    if agent_name == "prompt_engineer":
        return artifacts.get("orchestrator_plan", {})
    elif agent_name == "audio_subtitle":
        prompt_plan = artifacts.get("prompt_plan", {})
        return {
            "script": input_config.get("script", ""),
            "voice_params": prompt_plan.get("voice_params", {}),
        }
    elif agent_name == "video_generator":
        prompt_plan = artifacts.get("prompt_plan", {})
        return {
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "no_audio": input_config.get("no_audio", True),
        }
    elif agent_name == "video_editor":
        video_clips = artifacts.get("video_clips", {})
        audio = artifacts.get("audio", {})
        prompt_plan = artifacts.get("prompt_plan", {})
        orch_plan = artifacts.get("orchestrator_plan", {})
        return {
            "video_clips": video_clips.get("video_clips", []),
            "audio_path": audio.get("audio_path", ""),
            "subtitle_path": audio.get("subtitle_path", ""),
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "duration_mode": input_config.get("duration_mode", "fixed"),
            "shot_durations": [s["duration_seconds"] for s in orch_plan.get("shots", [])],
            "transition": input_config.get("transition", "none"),
            "transition_duration": input_config.get("transition_duration", 0.5),
            "bgm_mood": input_config.get("bgm_mood", "none"),
            "bgm_volume": input_config.get("bgm_volume", 0.15),
            "watermark_path": input_config.get("watermark_path"),
        }
    return {}


async def _run_agent(executor: PipelineExecutor, context, agent_name: str, input_config: dict) -> dict:
    agent_map = {
        "orchestrator": executor.orchestrator,
        "prompt_engineer": executor.prompt_engineer,
        "audio_subtitle": executor.audio_agent,
        "video_generator": executor.video_gen_agent,
        "video_editor": executor.video_editor,
    }
    agent_to_artifact_key = {
        "orchestrator": "orchestrator_plan",
        "prompt_engineer": "prompt_plan",
        "audio_subtitle": "audio",
        "video_generator": "video_clips",
        "video_editor": "final_video",
    }

    agent = agent_map[agent_name]
    agent_input = _build_agent_input(agent_name, context.artifacts, input_config)
    result = await agent.run(context, agent_input)
    if not result.success:
        raise RuntimeError(f"Agent {agent_name} failed: {result.error}")

    artifact_key = agent_to_artifact_key.get(agent_name)
    if artifact_key:
        context.artifacts[artifact_key] = result.output_data
    return result.output_data


async def _continue_pipeline_from_retry(
    executor: PipelineExecutor,
    context,
    agent_name: str,
    input_config: dict,
):
    if agent_name == "orchestrator":
        await _run_agent(executor, context, "prompt_engineer", input_config)
        audio_result, video_result = await asyncio.gather(
            _run_agent(executor, context, "audio_subtitle", input_config),
            _run_agent(executor, context, "video_generator", input_config),
        )
        context.artifacts["audio"] = audio_result
        context.artifacts["video_clips"] = video_result
        await _run_agent(executor, context, "video_editor", input_config)
        return

    if agent_name == "prompt_engineer":
        audio_result, video_result = await asyncio.gather(
            _run_agent(executor, context, "audio_subtitle", input_config),
            _run_agent(executor, context, "video_generator", input_config),
        )
        context.artifacts["audio"] = audio_result
        context.artifacts["video_clips"] = video_result
        await _run_agent(executor, context, "video_editor", input_config)
        return

    if agent_name == "audio_subtitle":
        if "video_clips" not in context.artifacts:
            await _run_agent(executor, context, "video_generator", input_config)
        await _run_agent(executor, context, "video_editor", input_config)
        return

    if agent_name == "video_generator":
        if "audio" not in context.artifacts:
            await _run_agent(executor, context, "audio_subtitle", input_config)
        await _run_agent(executor, context, "video_editor", input_config)
        return

    if agent_name == "video_editor":
        await _run_agent(executor, context, "video_editor", input_config)
