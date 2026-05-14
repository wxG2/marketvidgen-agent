from __future__ import annotations

import json
import asyncio
import re
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.security import (
    get_auto_chat_session_for_user,
    get_background_template_for_user,
    get_current_user,
    get_material_for_user,
    get_pipeline_run_for_user,
    get_project_for_user,
    get_social_account_for_user,
)
from app.core.config import settings
from app.db.session import async_session, get_db
from app.models.auto_chat import (
    AutoChatMessage,
    AutoChatSession,
    AutoSessionMaterialSelection,
    AutoSessionReferenceVideoSelection,
)
from app.models.material import Material
from app.models.material_selection import MaterialSelection
from app.models.pipeline import AgentExecution, PipelineRun
from app.models.social_account import SocialAccount
from app.models.user import User
from app.models.video_delivery import VideoDelivery
from app.models.video_upload import VideoUpload
from app.schemas.auto_chat import (
    AutoChatMessageCreateRequest,
    AutoChatMessagePayload,
    AutoChatMessageResponse,
    AutoChatMessageUpdateRequest,
    AutoChatSessionDetailResponse,
    AutoChatSessionState,
    AutoChatSessionSummaryResponse,
    AutoChatSessionUpdateRequest,
)
from app.schemas.material import MaterialSelectionResponse
from app.schemas.material import MaterialSelectRequest
from app.schemas.pipeline import (
    AgentExecutionResponse,
    PipelineDeliveryResponse,
    PipelineRunResponse,
    PipelineUsageResponse,
    VideoDeliveryResponse,
)
from app.schemas.social_account import (
    PublishDraftCreateRequest,
    PublishDraftResponse,
    SocialAccountResponse,
)
from app.schemas.video import VideoUploadResponse
from app.services.social_accounts import serialize_social_account
from app.services.usage_service import UsageRecorder
from app.services.video_delivery import (
    build_douyin_publish_draft,
    build_platform_preview_cards,
    serialize_delivery,
    upsert_publish_draft_record,
)

router = APIRouter(tags=["auto-sessions"])

INTRO_MESSAGE = (
    "把素材、参考视频和脚本都放在这里就可以直接一键生成。"
    "左侧会按会话保留历史记录，切换后会恢复该会话的素材、参考视频、方案确认和生成状态。"
)
CHAT_STREAM_TIMEOUT_SECONDS = 180
CHAT_STREAM_HEARTBEAT_SECONDS = 10
CHAT_ERROR_LOG_PATH = Path(settings.GENERATED_DIR) / "orchestrator_chat_error_log.md"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _compact_excerpt(content: str | None) -> str | None:
    if not content:
        return None
    normalized = " ".join(content.split())
    return normalized[:48] if normalized else None


def _safe_json_loads(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_chat_reply_content(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return ""

    normalized = text
    for _ in range(4):
        try:
            parsed = json.loads(normalized)
        except (json.JSONDecodeError, TypeError):
            break

        if isinstance(parsed, dict):
            reply = parsed.get("reply")
            if isinstance(reply, str) and reply.strip():
                normalized = reply.strip()
                continue
        if isinstance(parsed, list) and parsed:
            first = parsed[0]
            if isinstance(first, dict):
                reply = first.get("reply")
                if isinstance(reply, str) and reply.strip():
                    normalized = reply.strip()
                    continue
            if isinstance(first, str) and first.strip():
                normalized = first.strip()
                continue
        if isinstance(parsed, str) and parsed.strip():
            normalized = parsed.strip()
            continue
        break

    reply_match = re.search(r'"reply"\s*:\s*"((?:\\.|[^"])*)"', normalized, re.DOTALL)
    if reply_match:
        extracted = reply_match.group(1)
        normalized = extracted

    return normalized.replace("\\n", "\n").replace("\\t", "\t").strip()


async def _append_chat_error_log(
    *,
    project_id: str,
    session_id: str,
    user_id: str,
    title: str,
    detail: str,
    user_message: str | None = None,
) -> None:
    timestamp = _utcnow().isoformat()
    lines = [
        f"## {title}",
        f"- timestamp: {timestamp}",
        f"- project_id: {project_id}",
        f"- session_id: {session_id}",
        f"- user_id: {user_id}",
        f"- detail: {detail.strip() or 'unknown'}",
    ]
    if user_message and user_message.strip():
        lines.append(f"- user_message: {user_message.strip()}")
    lines.append("")

    content = "\n".join(lines)

    def _write() -> None:
        CHAT_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CHAT_ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(content)

    await asyncio.to_thread(_write)


def _message_payload_from_record(message: AutoChatMessage) -> AutoChatMessagePayload | None:
    payload = _safe_json_loads(message.payload_json)
    if not payload:
        return None
    return AutoChatMessagePayload.model_validate(payload)


def _message_to_response(message: AutoChatMessage) -> AutoChatMessageResponse:
    return AutoChatMessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        title=message.title,
        content=message.content,
        payload=_message_payload_from_record(message),
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


def _extract_publish_draft(payload: AutoChatMessagePayload | None) -> PublishDraftResponse | None:
    if not payload or payload.publishDraft is None:
        return None
    return payload.publishDraft


def _selection_to_response(selection: AutoSessionMaterialSelection, material: Material | None) -> MaterialSelectionResponse:
    material_payload = None
    if material is not None:
        material_payload = {
            "id": material.id,
            "category": material.category,
            "filename": material.filename,
            "media_type": material.media_type,
            "file_size": material.file_size,
            "width": material.width,
            "height": material.height,
            "thumbnail_url": f"/api/materials/{material.id}/thumbnail",
        }
    return MaterialSelectionResponse(
        id=selection.id,
        material_id=selection.material_id,
        category=material.category if material else "",
        sort_order=selection.sort_order,
        material=material_payload,
    )


def _execution_to_response(execution: AgentExecution) -> AgentExecutionResponse:
    return AgentExecutionResponse(
        id=execution.id,
        agent_name=execution.agent_name,
        status=execution.status,
        attempt_number=execution.attempt_number,
        input_data=json.loads(execution.input_data) if execution.input_data else None,
        output_data=json.loads(execution.output_data) if execution.output_data else None,
        duration_ms=execution.duration_ms,
        error_message=execution.error_message,
        progress_text=execution.progress_text,
        created_at=execution.created_at,
        completed_at=execution.completed_at,
    )


def _run_to_response(run: PipelineRun) -> PipelineRunResponse:
    return PipelineRunResponse.model_validate(run)


def _derive_status_preview(session: AutoChatSession, latest_message: AutoChatMessage | None, run: PipelineRun | None) -> str:
    if run:
        status_map = {
            "pending": "准备执行",
            "running": "生成中",
            "completed": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
            "waiting_confirmation": "等待确认方案",
            "waiting_prompt_review": "等待确认镜头方案",
            "waiting_remix_confirmation": "等待确认混剪方案",
        }
        return status_map.get(run.status, run.status)
    if latest_message and latest_message.role == "assistant":
        return latest_message.title or session.status_preview
    return session.status_preview or "等待发送"


def _derive_title(current_title: str, role: str, content: str, reference_video_name: str | None = None) -> str:
    if current_title not in {"新会话", "默认会话"}:
        return current_title
    if role == "user":
        excerpt = _compact_excerpt(content)
        if excerpt:
            return excerpt[:24]
    if reference_video_name:
        return reference_video_name[:24]
    return current_title


def _unique_video_ids(video_ids: list[str] | None) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for video_id in video_ids or []:
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        unique.append(video_id)
        if len(unique) > 20:
            raise HTTPException(status_code=400, detail="Reference video selection is limited to 20 videos")
    return unique


async def _validate_reference_videos(
    db: AsyncSession,
    *,
    project_id: str,
    video_ids: list[str],
) -> list[VideoUpload]:
    uploads: list[VideoUpload] = []
    for video_id in _unique_video_ids(video_ids):
        upload = await db.get(VideoUpload, video_id)
        if not upload or upload.project_id != project_id:
            raise HTTPException(status_code=400, detail="Reference video does not belong to this project")
        uploads.append(upload)
    return uploads


async def _replace_session_reference_videos(
    db: AsyncSession,
    session: AutoChatSession,
    uploads: list[VideoUpload],
) -> None:
    await db.execute(
        delete(AutoSessionReferenceVideoSelection)
        .where(AutoSessionReferenceVideoSelection.session_id == session.id)
    )
    for index, upload in enumerate(uploads):
        db.add(
            AutoSessionReferenceVideoSelection(
                session_id=session.id,
                video_upload_id=upload.id,
                sort_order=index,
            )
        )
    session.reference_video_id = uploads[0].id if uploads else None


async def _load_session_reference_videos(
    db: AsyncSession,
    session: AutoChatSession,
) -> list[VideoUpload]:
    result = await db.execute(
        select(AutoSessionReferenceVideoSelection)
        .where(AutoSessionReferenceVideoSelection.session_id == session.id)
        .order_by(
            AutoSessionReferenceVideoSelection.sort_order.asc(),
            AutoSessionReferenceVideoSelection.created_at.asc(),
        )
    )
    videos: list[VideoUpload] = []
    for selection in result.scalars().all():
        upload = await db.get(VideoUpload, selection.video_upload_id)
        if upload and upload.project_id == session.project_id:
            videos.append(upload)
    if not videos and session.reference_video_id:
        upload = await db.get(VideoUpload, session.reference_video_id)
        if upload and upload.project_id == session.project_id:
            videos.append(upload)
    return videos


async def _build_session_summary(
    db: AsyncSession,
    session: AutoChatSession,
) -> AutoChatSessionSummaryResponse:
    latest_message = (
        await db.execute(
            select(AutoChatMessage)
            .where(AutoChatMessage.session_id == session.id)
            .order_by(AutoChatMessage.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    run = await db.get(PipelineRun, session.current_run_id) if session.current_run_id else None
    reference_videos = await _load_session_reference_videos(db, session)
    reference_video_name = None
    if len(reference_videos) == 1:
        reference_video_name = reference_videos[0].filename
    elif len(reference_videos) > 1:
        reference_video_name = f"{reference_videos[0].filename} 等 {len(reference_videos)} 个"
    return AutoChatSessionSummaryResponse(
        id=session.id,
        project_id=session.project_id,
        title=session.title,
        status_preview=_derive_status_preview(session, latest_message, run),
        latest_message_excerpt=_compact_excerpt(latest_message.content if latest_message else None),
        latest_message_role=latest_message.role if latest_message else None,
        reference_video_name=reference_video_name,
        current_run_id=session.current_run_id,
        current_run_status=run.status if run else None,
        last_activity_at=session.last_activity_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


async def _ensure_intro_message(db: AsyncSession, session: AutoChatSession) -> None:
    existing_intro = (
        await db.execute(
            select(AutoChatMessage.id)
            .where(AutoChatMessage.session_id == session.id)
            .limit(1)
        )
    ).first()
    if existing_intro:
        return
    db.add(
        AutoChatMessage(
            session_id=session.id,
            role="assistant",
            title="capy 工作台",
            content=INTRO_MESSAGE,
        )
    )


async def _backfill_project_materials(db: AsyncSession, session: AutoChatSession) -> None:
    existing = (
        await db.execute(
            select(AutoSessionMaterialSelection.id)
            .where(AutoSessionMaterialSelection.session_id == session.id)
            .limit(1)
        )
    ).first()
    if existing:
        return
    result = await db.execute(
        select(MaterialSelection)
        .where(MaterialSelection.project_id == session.project_id)
        .order_by(MaterialSelection.sort_order.asc())
    )
    project_selections = result.scalars().all()
    for sel in project_selections:
        db.add(
            AutoSessionMaterialSelection(
                session_id=session.id,
                material_id=sel.material_id,
                sort_order=sel.sort_order,
            )
        )


async def _bootstrap_messages_from_history(db: AsyncSession, session: AutoChatSession) -> None:
    has_messages = (
        await db.execute(
            select(AutoChatMessage.id)
            .where(AutoChatMessage.session_id == session.id)
            .limit(1)
        )
    ).first()
    if has_messages:
        return
    await _ensure_intro_message(db, session)
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.session_id == session.id)
        .order_by(PipelineRun.created_at.asc())
    )
    for run in result.scalars().all():
        input_config = _safe_json_loads(run.input_config) or {}
        script = (input_config.get("script") or "").strip()
        reference_video = await db.get(VideoUpload, input_config.get("reference_video_id")) if input_config.get("reference_video_id") else None
        image_count = len(input_config.get("image_ids") or [])
        user_parts = []
        if reference_video:
            user_parts.append(f"参考视频：{reference_video.filename}")
        if script:
            user_parts.append(script)
        elif image_count:
            user_parts.append(f"历史生成请求，使用了 {image_count} 个素材。")
        if user_parts:
            db.add(
                AutoChatMessage(
                    session_id=session.id,
                    role="user",
                    title="历史请求",
                    content="\n".join(user_parts),
                    created_at=run.created_at,
                    updated_at=run.created_at,
                )
            )
        assistant_parts = [f"历史任务状态：{run.status}"]
        if run.error_message:
            assistant_parts.append(run.error_message)
        if run.final_video_path:
            assistant_parts.append("该历史会话已有成片，可在下方继续查看。")
        db.add(
            AutoChatMessage(
                session_id=session.id,
                role="assistant",
                title="历史状态",
                content="\n".join(assistant_parts),
                created_at=run.updated_at,
                updated_at=run.updated_at,
            )
        )


async def _ensure_default_session(db: AsyncSession, user: User, project_id: str) -> list[AutoChatSession]:
    result = await db.execute(
        select(AutoChatSession)
        .where(AutoChatSession.user_id == user.id, AutoChatSession.project_id == project_id)
        .order_by(AutoChatSession.last_activity_at.desc(), AutoChatSession.created_at.desc())
    )
    sessions = list(result.scalars().all())
    if sessions:
        return sessions

    session = AutoChatSession(
        user_id=user.id,
        project_id=project_id,
        title="默认会话",
        status_preview="等待发送",
        last_activity_at=_utcnow(),
    )
    db.add(session)
    await db.flush()

    await db.execute(
        PipelineRun.__table__.update()
        .where(
            PipelineRun.project_id == project_id,
            PipelineRun.user_id == user.id,
            PipelineRun.session_id.is_(None),
        )
        .values(session_id=session.id)
    )
    await db.execute(
        VideoUpload.__table__.update()
        .where(
            VideoUpload.project_id == project_id,
            VideoUpload.session_id.is_(None),
        )
        .values(session_id=session.id)
    )

    latest_run = (
        await db.execute(
            select(PipelineRun)
            .where(PipelineRun.project_id == project_id, PipelineRun.user_id == user.id, PipelineRun.session_id == session.id)
            .order_by(PipelineRun.updated_at.desc())
            .limit(1)
        )
    ).scalars().first()
    latest_video = (
        await db.execute(
            select(VideoUpload)
            .where(VideoUpload.project_id == project_id, VideoUpload.session_id == session.id)
            .order_by(VideoUpload.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if latest_run:
        session.current_run_id = latest_run.id
        session.status_preview = _derive_status_preview(session, None, latest_run)
        session.last_activity_at = latest_run.updated_at
    if latest_video:
        await _replace_session_reference_videos(db, session, [latest_video])
        if session.title == "默认会话" and not latest_run:
            session.title = latest_video.filename[:24]

    await _backfill_project_materials(db, session)
    await _bootstrap_messages_from_history(db, session)
    await db.commit()
    await db.refresh(session)
    return [session]


async def _session_detail(
    db: AsyncSession,
    session: AutoChatSession,
) -> AutoChatSessionDetailResponse:
    summary = await _build_session_summary(db, session)
    message_result = await db.execute(
        select(AutoChatMessage)
        .where(AutoChatMessage.session_id == session.id)
        .order_by(AutoChatMessage.created_at.asc(), AutoChatMessage.id.asc())
    )
    messages = [_message_to_response(item) for item in message_result.scalars().all()]

    material_result = await db.execute(
        select(AutoSessionMaterialSelection)
        .where(AutoSessionMaterialSelection.session_id == session.id)
        .order_by(AutoSessionMaterialSelection.sort_order.asc(), AutoSessionMaterialSelection.created_at.asc())
    )
    selections = []
    selection_items = []
    for item in material_result.scalars().all():
        material = await db.get(Material, item.material_id)
        if material and material.user_id != session.user_id:
            continue
        selections.append(_selection_to_response(item, material))
        if material:
            selection_items.append(
                {
                    "id": material.id,
                    "category": material.category,
                    "filename": material.filename,
                    "media_type": material.media_type,
                    "file_size": material.file_size,
                    "width": material.width,
                    "height": material.height,
                    "thumbnail_url": f"/api/materials/{material.id}/thumbnail",
                }
            )

    reference_videos = await _load_session_reference_videos(db, session)
    reference_video = reference_videos[0] if reference_videos else None
    run = await db.get(PipelineRun, session.current_run_id) if session.current_run_id else None

    execution_responses: list[AgentExecutionResponse] = []
    delivery_info = None
    usage_summary = None
    connected_accounts_rows = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.user_id == session.user_id, SocialAccount.platform == "douyin")
        .order_by(SocialAccount.is_default.desc(), SocialAccount.updated_at.desc())
    )
    connected_accounts = [SocialAccountResponse(**serialize_social_account(item)) for item in connected_accounts_rows.scalars().all()]
    recommended_account = connected_accounts[0] if connected_accounts else None
    latest_publish_draft = None
    if run is not None:
        exec_result = await db.execute(
            select(AgentExecution)
            .where(AgentExecution.pipeline_run_id == run.id)
            .order_by(AgentExecution.created_at.asc())
        )
        execution_responses = [_execution_to_response(item) for item in exec_result.scalars().all()]
        delivery_rows = await db.execute(
            select(VideoDelivery)
            .where(VideoDelivery.user_id == session.user_id, VideoDelivery.pipeline_run_id == run.id)
            .order_by(VideoDelivery.created_at.desc())
        )
        delivery_items = [VideoDeliveryResponse(**serialize_delivery(item)) for item in delivery_rows.scalars().all()]
        for record in delivery_items:
            if record.platform == "douyin" and record.action_type == "publish" and record.draft_payload:
                latest_publish_draft = PublishDraftResponse(**record.draft_payload)
                break
        if latest_publish_draft is None:
            for message in reversed(messages):
                draft = _extract_publish_draft(message.payload)
                if draft and draft.pipeline_run_id == run.id:
                    latest_publish_draft = draft
                    break
        delivery_info = PipelineDeliveryResponse(
            previews=[item for item in build_platform_preview_cards(run)],
            records=delivery_items,
            connected_social_accounts=connected_accounts,
            recommended_publish_account=recommended_account,
            latest_publish_draft=latest_publish_draft,
        )
        usage_summary = await UsageRecorder(async_session).get_run_summary(run.id)

    return AutoChatSessionDetailResponse(
        session=summary,
        state=AutoChatSessionState(
            draft_script=session.draft_script,
            background_template_id=session.background_template_id,
            reference_video_id=session.reference_video_id,
            reference_video_ids=[item.id for item in reference_videos],
            video_platform=session.video_platform,
            video_no_audio=session.video_no_audio,
            video_model_no_audio=session.video_no_audio,
            voiceover_no_audio=session.voiceover_no_audio,
            duration_mode=session.duration_mode,
            video_transition=session.video_transition,
            bgm_mood=session.bgm_mood,
            watermark_id=session.watermark_id,
            current_run_id=session.current_run_id,
        ),
        messages=messages,
        selected_materials=selections,
        selected_material_items=selection_items,
        reference_video=VideoUploadResponse.model_validate(reference_video) if reference_video else None,
        reference_videos=[VideoUploadResponse.model_validate(item) for item in reference_videos],
        current_run=_run_to_response(run) if run else None,
        agent_executions=execution_responses,
        delivery_info=delivery_info,
        usage_summary=PipelineUsageResponse(**usage_summary) if usage_summary else None,
        connected_social_accounts=connected_accounts,
        recommended_publish_account=recommended_account,
        latest_publish_draft=latest_publish_draft,
    )


async def _load_recent_messages(db: AsyncSession, session_id: str, limit: int = 5) -> list[dict[str, str]]:
    result = await db.execute(
        select(AutoChatMessage)
        .where(AutoChatMessage.session_id == session_id)
        .order_by(AutoChatMessage.created_at.desc(), AutoChatMessage.id.desc())
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))
    return [
        {
            "role": message.role,
            "content": message.content,
        }
        for message in messages
    ]


async def _load_session_context(
    db: AsyncSession,
    session: AutoChatSession,
    user: User,
) -> dict:
    material_result = await db.execute(
        select(AutoSessionMaterialSelection)
        .where(AutoSessionMaterialSelection.session_id == session.id)
        .order_by(AutoSessionMaterialSelection.sort_order.asc(), AutoSessionMaterialSelection.created_at.asc())
    )
    selected_materials = [
        {
            "id": item.id,
            "material_id": item.material_id,
            "sort_order": item.sort_order,
        }
        for item in material_result.scalars().all()
    ]
    reference_videos = await _load_session_reference_videos(db, session)
    return {
        "project_id": session.project_id,
        "session_id": session.id,
        "user_id": user.id,
        "reference_video_id": reference_videos[0].id if reference_videos else session.reference_video_id,
        "reference_video_ids": [item.id for item in reference_videos],
        "background_template_id": session.background_template_id,
        "draft_script": session.draft_script,
        "platform": session.video_platform or "generic",
        "video_no_audio": session.video_no_audio,
        "video_model_no_audio": session.video_no_audio,
        "voiceover_no_audio": session.voiceover_no_audio,
        "duration_mode": session.duration_mode or "fixed",
        "video_transition": session.video_transition or "none",
        "bgm_mood": session.bgm_mood or "none",
        "watermark_id": session.watermark_id,
        "selected_materials": selected_materials,
    }


@router.get("/api/projects/{project_id}/auto-sessions", response_model=list[AutoChatSessionSummaryResponse])
async def list_auto_sessions(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    await _ensure_default_session(db, user, project_id)
    result = await db.execute(
        select(AutoChatSession)
        .where(AutoChatSession.user_id == user.id, AutoChatSession.project_id == project_id)
        .order_by(AutoChatSession.last_activity_at.desc(), AutoChatSession.created_at.desc())
    )
    sessions = result.scalars().all()
    return [await _build_session_summary(db, session) for session in sessions]


@router.post("/api/projects/{project_id}/auto-sessions", response_model=AutoChatSessionDetailResponse)
async def create_auto_session(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = AutoChatSession(
        user_id=user.id,
        project_id=project_id,
        title="新会话",
        status_preview="等待发送",
        last_activity_at=_utcnow(),
    )
    db.add(session)
    await db.flush()
    await _ensure_intro_message(db, session)
    await db.commit()
    await db.refresh(session)
    return await _session_detail(db, session)


@router.get("/api/projects/{project_id}/auto-sessions/{session_id}", response_model=AutoChatSessionDetailResponse)
async def get_auto_session_detail(
    project_id: str,
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    return await _session_detail(db, session)


@router.patch("/api/projects/{project_id}/auto-sessions/{session_id}", response_model=AutoChatSessionDetailResponse)
async def update_auto_session(
    project_id: str,
    session_id: str,
    req: AutoChatSessionUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)

    if req.background_template_id:
        await get_background_template_for_user(db, user.id, req.background_template_id)
    if req.watermark_id:
        await get_material_for_user(db, user.id, req.watermark_id)
    if req.current_run_id:
        run = await get_pipeline_run_for_user(db, user.id, req.current_run_id)
        if run.project_id != project_id or run.session_id != session.id:
            raise HTTPException(status_code=400, detail="Pipeline run does not belong to this session")
    if req.reference_video_ids is not None:
        uploads = await _validate_reference_videos(
            db,
            project_id=project_id,
            video_ids=req.reference_video_ids,
        )
        await _replace_session_reference_videos(db, session, uploads)
    elif req.reference_video_id:
        uploads = await _validate_reference_videos(
            db,
            project_id=project_id,
            video_ids=[req.reference_video_id],
        )
        await _replace_session_reference_videos(db, session, uploads)

    for field in (
        "title",
        "status_preview",
        "draft_script",
        "background_template_id",
        "video_platform",
        "video_no_audio",
        "video_model_no_audio",
        "voiceover_no_audio",
        "duration_mode",
        "video_transition",
        "bgm_mood",
        "watermark_id",
        "current_run_id",
    ):
        value = getattr(req, field)
        if value is not None:
            if field == "video_model_no_audio":
                session.video_no_audio = value
            else:
                setattr(session, field, value)
    session.last_activity_at = req.last_activity_at or _utcnow()
    await db.commit()
    await db.refresh(session)
    return await _session_detail(db, session)


@router.post("/api/projects/{project_id}/auto-sessions/{session_id}/messages", response_model=AutoChatMessageResponse)
async def append_auto_session_message(
    project_id: str,
    session_id: str,
    req: AutoChatMessageCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    reference_video = await db.get(VideoUpload, session.reference_video_id) if session.reference_video_id else None
    message = AutoChatMessage(
        session_id=session.id,
        role=req.role,
        title=req.title,
        content=req.content,
        payload_json=req.payload.model_dump_json(exclude_none=True) if req.payload else None,
    )
    db.add(message)
    session.title = _derive_title(session.title, req.role, req.content, reference_video.filename if reference_video else None)
    session.status_preview = req.title or session.status_preview
    session.last_activity_at = _utcnow()
    await db.commit()
    await db.refresh(message)
    return _message_to_response(message)


@router.post("/api/projects/{project_id}/auto-sessions/{session_id}/chat")
async def chat_with_agent(
    project_id: str,
    session_id: str,
    req: AutoChatMessageCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    orchestrator_agent = getattr(request.app.state, "orchestrator_agent", None)
    if orchestrator_agent is None:
        raise HTTPException(status_code=503, detail="Orchestrator agent is not ready")

    reference_video = await db.get(VideoUpload, session.reference_video_id) if session.reference_video_id else None
    user_message = AutoChatMessage(
        session_id=session.id,
        role="user",
        title=req.title,
        content=req.content,
        payload_json=req.payload.model_dump_json(exclude_none=True) if req.payload else None,
    )
    db.add(user_message)
    session.title = _derive_title(session.title, "user", req.content, reference_video.filename if reference_video else None)
    session.status_preview = req.title or session.status_preview
    session.last_activity_at = _utcnow()
    await db.commit()

    history = await _load_recent_messages(db, session.id)
    session_context = await _load_session_context(db, session, user)
    session_context["generation_model"] = req.generation_model
    if req.video_model_no_audio is not None:
        session_context["video_model_no_audio"] = req.video_model_no_audio
        session_context["video_no_audio"] = req.video_model_no_audio
    if req.voiceover_no_audio is not None:
        session_context["voiceover_no_audio"] = req.voiceover_no_audio
    session_context["skip_video_generation"] = req.skip_video_generation

    async def event_generator():
        collected_text: list[str] = []
        stream = orchestrator_agent.chat_stream(history, session_context)

        async def _persist_assistant_message(content: str) -> dict | None:
            normalized_content = _normalize_chat_reply_content(content)
            if not normalized_content:
                return None
            async with async_session() as save_db:
                assistant_message = AutoChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content=normalized_content,
                )
                save_db.add(assistant_message)
                save_session = await save_db.get(AutoChatSession, session.id)
                if save_session is not None:
                    save_session.status_preview = "已回复"
                    save_session.last_activity_at = _utcnow()
                await save_db.commit()
                await save_db.refresh(assistant_message)
                return _message_to_response(assistant_message).model_dump(mode="json")

        next_event_task: asyncio.Task | None = None
        try:
            while True:
                wait_seconds = 0
                next_event_task = asyncio.create_task(stream.__anext__())
                try:
                    while True:
                        try:
                            event = await asyncio.wait_for(
                                asyncio.shield(next_event_task),
                                timeout=CHAT_STREAM_HEARTBEAT_SECONDS,
                            )
                            break
                        except asyncio.TimeoutError:
                            wait_seconds += CHAT_STREAM_HEARTBEAT_SECONDS
                            if wait_seconds >= CHAT_STREAM_TIMEOUT_SECONDS:
                                next_event_task.cancel()
                                with suppress(asyncio.CancelledError, Exception):
                                    await next_event_task
                                next_event_task = None
                                raise
                            yield {
                                "event": "status",
                                "data": json.dumps(
                                    {"content": "请求仍在处理中，正在等待模型或工具返回。"},
                                    ensure_ascii=False,
                                ),
                            }
                except StopAsyncIteration:
                    assistant_payload = await _persist_assistant_message("".join(collected_text))
                    yield {"event": "done", "data": json.dumps({"assistant_message": assistant_payload}, ensure_ascii=False)}
                    return
                except asyncio.TimeoutError:
                    timeout_message = f"对话在 {CHAT_STREAM_TIMEOUT_SECONDS}s 内未返回结果，已自动断开，请稍后重试。"
                    await _append_chat_error_log(
                        project_id=project_id,
                        session_id=session.id,
                        user_id=user.id,
                        title="Chat Timeout",
                        detail=timeout_message,
                        user_message=req.content,
                    )
                    assistant_payload = await _persist_assistant_message(timeout_message)
                    yield {"event": "text", "data": json.dumps({"content": timeout_message}, ensure_ascii=False)}
                    yield {"event": "done", "data": json.dumps({"assistant_message": assistant_payload}, ensure_ascii=False)}
                    return

                if event.type == "text":
                    collected_text.append(event.content)
                    yield {"event": "text", "data": json.dumps({"content": event.content}, ensure_ascii=False)}
                elif event.type == "status":
                    yield {"event": "status", "data": json.dumps({"content": event.content}, ensure_ascii=False)}
                elif event.type == "tool_call":
                    yield {
                        "event": "tool_call",
                        "data": json.dumps(
                            {"name": event.tool_name, "args": event.tool_args},
                            ensure_ascii=False,
                        ),
                    }
                elif event.type == "tool_result":
                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {"name": event.tool_name, "result": event.tool_result},
                            ensure_ascii=False,
                        ),
                    }
                elif event.type == "done":
                    assistant_payload = await _persist_assistant_message("".join(collected_text))
                    yield {"event": "done", "data": json.dumps({"assistant_message": assistant_payload}, ensure_ascii=False)}
                    return
        except asyncio.CancelledError:
            # Client disconnected — clean up pending __anext__ task and close the generator
            if next_event_task is not None and not next_event_task.done():
                next_event_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await next_event_task
            with suppress(Exception):
                await stream.aclose()
            raise
        except Exception as exc:
            error_message = f"Chat stream error: {exc}"
            await _append_chat_error_log(
                project_id=project_id,
                session_id=session.id,
                user_id=user.id,
                title="Chat Error",
                detail=error_message,
                user_message=req.content,
            )
            assistant_payload = await _persist_assistant_message("对话出现异常，已自动断开，请稍后重试。")
            yield {"event": "text", "data": json.dumps({"content": "对话出现异常，已自动断开，请稍后重试。"}, ensure_ascii=False)}
            yield {"event": "done", "data": json.dumps({"assistant_message": assistant_payload}, ensure_ascii=False)}
            return

    return EventSourceResponse(event_generator())


@router.patch("/api/projects/{project_id}/auto-sessions/{session_id}/messages/{message_id}", response_model=AutoChatMessageResponse)
async def update_auto_session_message(
    project_id: str,
    session_id: str,
    message_id: str,
    req: AutoChatMessageUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    message = await db.get(AutoChatMessage, message_id)
    if not message or message.session_id != session.id:
        raise HTTPException(status_code=404, detail="Auto chat message not found")
    if req.title is not None:
        message.title = req.title
    if req.content is not None:
        message.content = req.content
    if req.payload is not None:
        message.payload_json = req.payload.model_dump_json(exclude_none=True)
    session.last_activity_at = _utcnow()
    await db.commit()
    await db.refresh(message)
    return _message_to_response(message)


@router.get("/api/projects/{project_id}/auto-sessions/{session_id}/materials", response_model=list[MaterialSelectionResponse])
async def list_auto_session_materials(
    project_id: str,
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    result = await db.execute(
        select(AutoSessionMaterialSelection)
        .where(AutoSessionMaterialSelection.session_id == session.id)
        .order_by(AutoSessionMaterialSelection.sort_order.asc(), AutoSessionMaterialSelection.created_at.asc())
    )
    items = []
    for selection in result.scalars().all():
        material = await db.get(Material, selection.material_id)
        if material and material.user_id != user.id:
            continue
        items.append(_selection_to_response(selection, material))
    return items


@router.post("/api/projects/{project_id}/auto-sessions/{session_id}/materials", response_model=MaterialSelectionResponse)
async def select_auto_session_material(
    project_id: str,
    session_id: str,
    data: MaterialSelectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    material_id = data.material_id
    if not material_id:
        raise HTTPException(status_code=400, detail="material_id is required")
    material = await get_material_for_user(db, user.id, material_id)
    existing = (
        await db.execute(
            select(AutoSessionMaterialSelection)
            .where(
                AutoSessionMaterialSelection.session_id == session.id,
                AutoSessionMaterialSelection.material_id == material_id,
            )
            .limit(1)
        )
    ).scalars().first()
    if existing:
        return _selection_to_response(existing, material)
    selection = AutoSessionMaterialSelection(
        session_id=session.id,
        material_id=material_id,
        sort_order=data.sort_order,
    )
    db.add(selection)
    session.last_activity_at = _utcnow()
    await db.commit()
    await db.refresh(selection)
    return _selection_to_response(selection, material)


@router.delete("/api/projects/{project_id}/auto-sessions/{session_id}/materials/{material_id}")
async def deselect_auto_session_material(
    project_id: str,
    session_id: str,
    material_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    selection = (
        await db.execute(
            select(AutoSessionMaterialSelection)
            .where(
                AutoSessionMaterialSelection.session_id == session.id,
                AutoSessionMaterialSelection.material_id == material_id,
            )
            .limit(1)
        )
    ).scalars().first()
    if selection:
        await db.delete(selection)
        session.last_activity_at = _utcnow()
        await db.commit()
    return {"ok": True}


@router.post("/api/projects/{project_id}/auto-sessions/{session_id}/publish-drafts", response_model=PublishDraftResponse)
async def create_publish_draft(
    project_id: str,
    session_id: str,
    req: PublishDraftCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_for_user(db, user.id, project_id)
    session = await get_auto_chat_session_for_user(db, user.id, project_id, session_id)
    if req.platform != "douyin":
        raise HTTPException(status_code=400, detail="当前仅支持生成抖音发布草稿")
    if not session.current_run_id:
        raise HTTPException(status_code=400, detail="当前会话还没有可发布的成片")

    run = await get_pipeline_run_for_user(db, user.id, session.current_run_id)
    if run.project_id != project_id or not run.final_video_path:
        raise HTTPException(status_code=400, detail="当前会话还没有已完成成片")

    social_account = None
    if req.social_account_id:
        social_account = await get_social_account_for_user(db, user.id, req.social_account_id)
    else:
        result = await db.execute(
            select(SocialAccount)
            .where(SocialAccount.user_id == user.id, SocialAccount.platform == "douyin")
            .order_by(SocialAccount.is_default.desc(), SocialAccount.updated_at.desc())
            .limit(1)
        )
        social_account = result.scalars().first()

    draft = build_douyin_publish_draft(run, social_account=social_account)
    record = await upsert_publish_draft_record(
        db,
        user_id=user.id,
        project_id=project_id,
        run=run,
        social_account=social_account,
        draft=draft,
    )
    draft["delivery_record_id"] = record.id

    existing_messages = (
        await db.execute(
            select(AutoChatMessage)
            .where(AutoChatMessage.session_id == session.id, AutoChatMessage.role == "assistant")
            .order_by(AutoChatMessage.created_at.desc())
        )
    ).scalars().all()
    target_message = None
    for message in existing_messages:
        payload = _message_payload_from_record(message)
        publish_draft = _extract_publish_draft(payload)
        if publish_draft and publish_draft.pipeline_run_id == run.id:
            target_message = message
            break

    content = (
        "我已经根据这条成片和当前会话内容，自动整理出一份抖音发布草稿。"
        "你可以直接确认发布，或者先修改标题、文案和话题标签。"
    )
    payload = AutoChatMessagePayload(publishDraft=PublishDraftResponse(**draft))
    if target_message is None:
        db.add(
            AutoChatMessage(
                session_id=session.id,
                role="assistant",
                title="抖音发布草稿",
                content=content,
                payload_json=payload.model_dump_json(exclude_none=True),
            )
        )
    else:
        target_message.title = "抖音发布草稿"
        target_message.content = content
        target_message.payload_json = payload.model_dump_json(exclude_none=True)
    session.last_activity_at = _utcnow()
    await db.commit()
    return PublishDraftResponse(**draft)
