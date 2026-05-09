from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.pipeline import PipelineRun
from app.models.repository_asset import RepositoryAsset


TRACKED_AGENT_NAMES = {"prompt_engineer", "audio_subtitle", "video_generator"}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _safe_component(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return safe.strip("._") or "artifact"


def _looks_like_remote_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


def _looks_like_public_path(path: str) -> bool:
    return path.startswith(("/api/", "/generated/", "/repository/", "/examples/"))


def _guess_mime(path: str | None, fallback: str | None = None) -> str | None:
    if not path:
        return fallback
    return mimetypes.guess_type(path)[0] or fallback


def _read_text_file(path: str | None) -> str | None:
    if not path or _looks_like_remote_url(path) or _looks_like_public_path(path):
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _repository_file_url(path: str | None) -> str | None:
    if not path:
        return None
    if _looks_like_remote_url(path) or _looks_like_public_path(path):
        return path

    try:
        abs_path = Path(path).resolve()
        repo_root = Path(settings.VIDEO_REPOSITORY_DIR).resolve()
        abs_path.relative_to(repo_root)  # raises ValueError if not under repo_root
        suffix = str(abs_path.relative_to(repo_root)).replace("\\", "/")
        return f"/repository/{suffix}"
    except (ValueError, OSError):
        pass

    try:
        abs_path = Path(path).resolve()
        generated_root = Path(settings.GENERATED_DIR).resolve()
        abs_path.relative_to(generated_root)
        suffix = str(abs_path.relative_to(generated_root)).replace("\\", "/")
        return f"/generated/{suffix}"
    except (ValueError, OSError):
        pass

    return path


def _copy_file_into_repository(
    *,
    run: PipelineRun,
    source_path: str | None,
    source_agent: str,
    asset_key: str,
) -> tuple[str | None, int | None, str | None]:
    if not source_path:
        return None, None, None

    mime_type = _guess_mime(source_path)
    if _looks_like_remote_url(source_path) or _looks_like_public_path(source_path):
        return source_path, None, mime_type

    source = Path(source_path).expanduser()
    if not source.exists() or not source.is_file():
        return source_path, None, mime_type

    repo_root = Path(settings.VIDEO_REPOSITORY_DIR).resolve()
    source_resolved = source.resolve()
    try:
        if source_resolved.is_relative_to(repo_root):
            return str(source_resolved), source_resolved.stat().st_size, mime_type
    except AttributeError:
        if str(source_resolved).startswith(str(repo_root)):
            return str(source_resolved), source_resolved.stat().st_size, mime_type

    target_dir = repo_root / "artifacts" / str(run.user_id or "unknown-user") / run.project_id / run.id / source_agent
    target_dir.mkdir(parents=True, exist_ok=True)
    extension = source.suffix or mimetypes.guess_extension(mime_type or "") or ""
    target = target_dir / f"{_safe_component(asset_key)}{extension}"
    if source_resolved != target.resolve():
        shutil.copy2(source_resolved, target)
    return str(target), target.stat().st_size, mime_type


def _base_metadata(source_agent: str, output_data: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {
        "source_agent": source_agent,
        "saved_from": "pipeline_agent_output",
        "output_keys": sorted(output_data.keys()),
    }
    if extra:
        metadata.update(extra)
    return metadata


def _prompt_artifact_specs(output_data: dict[str, Any]) -> list[dict[str, Any]]:
    shot_prompts = [item for item in output_data.get("shot_prompts", []) if isinstance(item, dict)]
    voice_params = output_data.get("voice_params") or {}
    specs: list[dict[str, Any]] = [
        {
            "asset_key": "prompt_engineer.plan",
            "asset_type": "plan",
            "title": "提示词方案",
            "description": f"共 {len(shot_prompts)} 个分镜提示词。",
            "mime_type": "application/json",
            "text_content": _json_text(output_data),
            "metadata": _base_metadata("prompt_engineer", output_data, {"shot_count": len(shot_prompts)}),
        }
    ]

    for index, shot in enumerate(shot_prompts):
        shot_idx = shot.get("shot_idx", index)
        video_prompt = str(shot.get("video_prompt") or "").strip()
        if not video_prompt:
            continue
        specs.append(
            {
                "asset_key": f"prompt_engineer.shot.{shot_idx}",
                "asset_type": "prompt",
                "title": f"Shot {shot_idx} 视频提示词",
                "description": str(shot.get("script_segment") or ""),
                "mime_type": "text/plain; charset=utf-8",
                "text_content": video_prompt,
                "duration_ms": int(float(shot.get("duration_seconds") or 0) * 1000) or None,
                "metadata": _base_metadata(
                    "prompt_engineer",
                    output_data,
                    {
                        "shot_idx": shot_idx,
                        "script_segment": shot.get("script_segment"),
                        "image_path": shot.get("image_path"),
                        "image_content": shot.get("image_content"),
                        "source_image": shot.get("source_image"),
                    },
                ),
            }
        )

    if voice_params:
        specs.append(
            {
                "asset_key": "prompt_engineer.voice_params",
                "asset_type": "voice_params",
                "title": "配音参数",
                "description": "Prompt Engineer 生成的声音设计参数。",
                "mime_type": "application/json",
                "text_content": _json_text(voice_params),
                "metadata": _base_metadata("prompt_engineer", output_data),
            }
        )
    return specs


def _audio_artifact_specs(run: PipelineRun, output_data: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if output_data.get("skipped"):
        specs.append(
            {
                "asset_key": "audio_subtitle.status",
                "asset_type": "status",
                "title": "音频状态",
                "description": "AudioSubtitle 未生成音频文件。",
                "mime_type": "text/plain; charset=utf-8",
                "text_content": f"已跳过音频生成：{output_data.get('skip_reason') or 'unknown'}",
                "metadata": _base_metadata("audio_subtitle", output_data),
            }
        )
        return specs

    audio_path, audio_size, audio_mime = _copy_file_into_repository(
        run=run,
        source_path=output_data.get("audio_path"),
        source_agent="audio_subtitle",
        asset_key="audio_subtitle.audio",
    )
    if audio_path:
        specs.append(
            {
                "asset_key": "audio_subtitle.audio",
                "asset_type": "audio",
                "title": "旁白音频",
                "description": "AudioSubtitle 生成的 TTS 音频。",
                "mime_type": audio_mime or "audio/mpeg",
                "file_path": audio_path,
                "duration_ms": output_data.get("duration_ms"),
                "metadata": _base_metadata(
                    "audio_subtitle",
                    output_data,
                    {"source_path": output_data.get("audio_path"), "file_size": audio_size},
                ),
            }
        )

    subtitle_path, subtitle_size, subtitle_mime = _copy_file_into_repository(
        run=run,
        source_path=output_data.get("subtitle_path"),
        source_agent="audio_subtitle",
        asset_key="audio_subtitle.subtitle",
    )
    if subtitle_path:
        specs.append(
            {
                "asset_key": "audio_subtitle.subtitle",
                "asset_type": "subtitle",
                "title": "字幕文件",
                "description": "AudioSubtitle 生成的字幕文本。",
                "mime_type": subtitle_mime or "application/x-subrip",
                "file_path": subtitle_path,
                "text_content": _read_text_file(subtitle_path),
                "metadata": _base_metadata(
                    "audio_subtitle",
                    output_data,
                    {"source_path": output_data.get("subtitle_path"), "file_size": subtitle_size},
                ),
            }
        )
    return specs


def _video_artifact_specs(run: PipelineRun, output_data: dict[str, Any]) -> list[dict[str, Any]]:
    clips = [item for item in output_data.get("video_clips", []) if isinstance(item, dict)]
    specs: list[dict[str, Any]] = [
        {
            "asset_key": "video_generator.manifest",
            "asset_type": "video_manifest",
            "title": "分镜视频清单",
            "description": f"共 {len(clips)} 个视频分镜。",
            "mime_type": "application/json",
            "text_content": _json_text(output_data),
            "metadata": _base_metadata("video_generator", output_data, {"clip_count": len(clips)}),
        }
    ]

    for index, clip in enumerate(clips):
        shot_idx = clip.get("shot_idx", index)
        asset_key = f"video_generator.shot.{shot_idx}"
        video_path, file_size, video_mime = _copy_file_into_repository(
            run=run,
            source_path=clip.get("video_path"),
            source_agent="video_generator",
            asset_key=asset_key,
        )
        if not video_path:
            continue
        specs.append(
            {
                "asset_key": asset_key,
                "asset_type": "video",
                "title": f"Shot {shot_idx} 生成视频",
                "description": f"任务 ID：{clip.get('task_id') or '-'}",
                "mime_type": video_mime or "video/mp4",
                "file_path": video_path,
                "duration_ms": int(float(clip.get("duration_seconds") or 0) * 1000) or None,
                "metadata": _base_metadata(
                    "video_generator",
                    output_data,
                    {
                        "shot_idx": shot_idx,
                        "task_id": clip.get("task_id"),
                        "generation_model": clip.get("generation_model"),
                        "source_path": clip.get("video_path"),
                        "file_size": file_size,
                    },
                ),
            }
        )
    return specs


def _artifact_specs_for_agent(run: PipelineRun, agent_name: str, output_data: dict[str, Any]) -> list[dict[str, Any]]:
    if agent_name == "prompt_engineer":
        return _prompt_artifact_specs(output_data)
    if agent_name == "audio_subtitle":
        return _audio_artifact_specs(run, output_data)
    if agent_name == "video_generator":
        return _video_artifact_specs(run, output_data)
    return []


async def _upsert_asset(session: AsyncSession, run: PipelineRun, spec: dict[str, Any]) -> None:
    result = await session.execute(
        select(RepositoryAsset).where(
            RepositoryAsset.pipeline_run_id == run.id,
            RepositoryAsset.asset_key == spec["asset_key"],
        )
    )
    asset = result.scalars().first()
    if asset is None:
        asset = RepositoryAsset(
            user_id=run.user_id or "",
            project_id=run.project_id,
            pipeline_run_id=run.id,
            asset_key=spec["asset_key"],
            asset_type=spec["asset_type"],
        )
        session.add(asset)

    asset.user_id = run.user_id or ""
    asset.project_id = run.project_id
    asset.asset_type = spec["asset_type"]
    asset.title = spec.get("title")
    asset.description = spec.get("description")
    asset.mime_type = spec.get("mime_type")
    asset.file_path = spec.get("file_path")
    asset.text_content = spec.get("text_content")
    asset.metadata_json = _json_text(spec.get("metadata") or {})
    asset.duration_ms = spec.get("duration_ms")
    asset.updated_at = datetime.now(timezone.utc)


async def save_agent_artifacts(
    db_session_factory: async_sessionmaker,
    *,
    pipeline_run_id: str,
    agent_name: str,
    output_data: dict[str, Any],
) -> None:
    if agent_name not in TRACKED_AGENT_NAMES or not output_data:
        return

    async with db_session_factory() as session:
        run = await session.get(PipelineRun, pipeline_run_id)
        if not run or not run.user_id:
            return

        specs = _artifact_specs_for_agent(run, agent_name, output_data)
        for spec in specs:
            await _upsert_asset(session, run, spec)
        await session.commit()


def serialize_repository_asset(asset: RepositoryAsset, project_name: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if asset.metadata_json:
        try:
            metadata = json.loads(asset.metadata_json)
        except json.JSONDecodeError:
            metadata = {}

    file_size = metadata.get("file_size")
    if not file_size and asset.file_path and not _looks_like_remote_url(asset.file_path) and not _looks_like_public_path(asset.file_path):
        try:
            file_size = os.path.getsize(asset.file_path)
        except OSError:
            file_size = None

    return {
        "id": asset.id,
        "user_id": asset.user_id,
        "project_id": asset.project_id,
        "project_name": project_name,
        "pipeline_run_id": asset.pipeline_run_id,
        "asset_key": asset.asset_key,
        "asset_type": asset.asset_type,
        "source_agent": metadata.get("source_agent") or asset.asset_key.split(".", 1)[0],
        "title": asset.title,
        "description": asset.description,
        "mime_type": asset.mime_type,
        "file_path": asset.file_path,
        "file_url": _repository_file_url(asset.file_path),
        "file_size": file_size,
        "text_content": asset.text_content,
        "metadata": metadata,
        "duration_ms": asset.duration_ms,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }
