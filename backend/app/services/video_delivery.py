from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.pipeline import PipelineRun
from app.models.social_account import SocialAccount
from app.models.video_delivery import VideoDelivery
from app.services.social_accounts import ensure_active_douyin_account, serialize_social_account


def _parse_input_config(run: PipelineRun) -> dict[str, Any]:
    try:
        return json.loads(run.input_config or "{}")
    except json.JSONDecodeError:
        return {}


def derive_delivery_title(run: PipelineRun) -> str:
    config = _parse_input_config(run)
    script = (config.get("script") or "").strip()
    template_name = (config.get("background_template_name") or "").strip()
    if script:
        first_line = script.splitlines()[0].strip()
        return first_line[:28] or "capy 成片"
    if template_name:
        return f"{template_name} 成片"
    return "capy 成片"


def build_platform_preview_cards(run: PipelineRun) -> list[dict[str, Any]]:
    config = _parse_input_config(run)
    script = (config.get("script") or "").strip()
    background_template_name = (config.get("background_template_name") or "").strip()
    style = (config.get("style") or "commercial").strip()
    title = derive_delivery_title(run)
    summary = script[:72] if script else "这是一条已完成生成的视频成片，可继续用于多平台分发。"
    context_hint = f"角色模板：{background_template_name}" if background_template_name else "未绑定角色模板"
    return [
        {
            "platform": "douyin",
            "label": "抖音卡片预览",
            "aspect_ratio": "9:16",
            "recommended_resolution": "1080 x 1920",
            "cover_title": title[:20],
            "headline": "更适合竖屏刷到即看懂",
            "caption": summary[:38],
            "layout_hint": f"前三秒直接给结论，强化口播节奏与字幕冲击力，当前风格：{style}",
            "safe_zone_tip": "顶部和底部保留安全区，避免被抖音标题栏、互动区遮挡。",
            "context_hint": context_hint,
            "primary_action": "一键发布到抖音",
        },
        {
            "platform": "youtube",
            "label": "YouTube 卡片预览",
            "aspect_ratio": "16:9",
            "recommended_resolution": "1920 x 1080",
            "cover_title": title[:36],
            "headline": "更适合横屏封面和频道分发",
            "caption": summary[:60],
            "layout_hint": "封面标题建议更完整，适合保留更多画面信息与品牌识别元素。",
            "safe_zone_tip": "注意左右边缘和右下角时长角标，避免关键信息被裁切。",
            "context_hint": context_hint,
            "primary_action": "保存到视频仓库",
        },
    ]


def serialize_delivery(record: VideoDelivery) -> dict[str, Any]:
    payload = None
    if record.response_json:
        try:
            payload = json.loads(record.response_json)
        except json.JSONDecodeError:
            payload = None
    draft_payload = None
    if record.draft_payload_json:
        try:
            draft_payload = json.loads(record.draft_payload_json)
        except json.JSONDecodeError:
            draft_payload = None
    return {
        "id": record.id,
        "user_id": record.user_id,
        "project_id": record.project_id,
        "pipeline_run_id": record.pipeline_run_id,
        "action_type": record.action_type,
        "platform": record.platform,
        "status": record.status,
        "social_account_id": record.social_account_id,
        "title": record.title,
        "description": record.description,
        "draft_payload": draft_payload,
        "saved_video_path": record.saved_video_path,
        "external_id": record.external_id,
        "external_url": record.external_url,
        "external_status": record.external_status,
        "response_payload": payload,
        "platform_error_code": record.platform_error_code,
        "error_message": record.error_message,
        "submitted_at": record.submitted_at,
        "published_at": record.published_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value).strip("-_")
    return cleaned[:32] or "video"


async def save_video_to_repository(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    run: PipelineRun,
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> VideoDelivery:
    existing = await db.execute(
        select(VideoDelivery)
        .where(
            VideoDelivery.pipeline_run_id == run.id,
            VideoDelivery.user_id == user_id,
            VideoDelivery.action_type == "save",
            VideoDelivery.platform == "repository",
            VideoDelivery.status == "saved",
        )
        .order_by(VideoDelivery.created_at.desc())
        .limit(1)
    )
    found = existing.scalars().first()
    if found is not None:
        return found

    source_path = Path(run.final_video_path or "")
    if not source_path.exists():
        raise FileNotFoundError("Final video not found")

    safe_title = _slugify(title or derive_delivery_title(run))
    target_dir = Path(settings.VIDEO_REPOSITORY_DIR) / user_id / project_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{run.id[:8]}_{safe_title}{source_path.suffix or '.mp4'}"
    shutil.copy2(source_path, target_path)

    record = VideoDelivery(
        user_id=user_id,
        project_id=project_id,
        pipeline_run_id=run.id,
        action_type="save",
        platform="repository",
        status="saved",
        title=title or derive_delivery_title(run),
        description=description,
        saved_video_path=str(target_path),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


def _dig(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


async def publish_video_to_douyin(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    run: PipelineRun,
    social_account: SocialAccount,
    title: Optional[str] = None,
    description: Optional[str] = None,
    hashtags: Optional[list[str]] = None,
    visibility: str = "public",
    cover_title: Optional[str] = None,
) -> VideoDelivery:
    source_path = Path(run.final_video_path or "")
    if not source_path.exists():
        raise FileNotFoundError("Final video not found")

    account = await ensure_active_douyin_account(db, social_account)
    publish_text_parts = [
        (description or title or derive_delivery_title(run)).strip(),
        " ".join(item for item in (hashtags or []) if item).strip(),
    ]
    publish_text = "\n".join(part for part in publish_text_parts if part).strip()[:120]
    upload_url = f"{settings.DOUYIN_OPEN_BASE_URL.rstrip('/')}/api/douyin/v1/video/upload_video/"
    create_url = f"{settings.DOUYIN_OPEN_BASE_URL.rstrip('/')}/api/douyin/v1/video/create_video/"
    headers = {"access-token": account.access_token}
    params = {"open_id": account.open_id}

    existing = await db.execute(
        select(VideoDelivery)
        .where(
            VideoDelivery.pipeline_run_id == run.id,
            VideoDelivery.user_id == user_id,
            VideoDelivery.action_type == "publish",
            VideoDelivery.platform == "douyin",
            VideoDelivery.social_account_id == account.id,
        )
        .order_by(VideoDelivery.created_at.desc())
        .limit(1)
    )
    record = existing.scalars().first()

    async with httpx.AsyncClient(timeout=180) as client:
        with source_path.open("rb") as video_file:
            upload_response = await client.post(
                upload_url,
                params=params,
                headers=headers,
                files={"video": (source_path.name, video_file, "video/mp4")},
            )
        upload_response.raise_for_status()
        upload_payload = upload_response.json()
        video_id = (
            _dig(upload_payload, "data", "video", "video_id")
            or _dig(upload_payload, "data", "video_id")
            or _dig(upload_payload, "video_id")
        )
        if not video_id:
            raise RuntimeError("抖音上传成功，但未返回 video_id。")

        create_response = await client.post(
            create_url,
            params=params,
            headers={**headers, "Content-Type": "application/json"},
            json={
                "video_id": video_id,
                "text": publish_text,
                "visibility": visibility,
                **({"cover_title": cover_title} if cover_title else {}),
            },
        )
        create_response.raise_for_status()
        create_payload = create_response.json()

    external_id = (
        _dig(create_payload, "data", "item_id")
        or _dig(create_payload, "data", "video_id")
        or str(video_id)
    )

    if record is None:
        record = VideoDelivery(
            user_id=user_id,
            project_id=project_id,
            pipeline_run_id=run.id,
            action_type="publish",
            platform="douyin",
        )
        db.add(record)

    draft_payload = {
        "platform": "douyin",
        "pipeline_run_id": run.id,
        "social_account_id": account.id,
        "account_name": account.display_name or f"抖音账号 {account.open_id[-6:]}",
        "title": title or derive_delivery_title(run),
        "description": description or derive_delivery_title(run),
        "hashtags": hashtags or [],
        "visibility": visibility,
        "cover_title": cover_title,
        "video_source": run.final_video_path,
        "status": "submitted",
    }
    record.social_account_id = account.id
    record.status = "submitted"
    record.title = title or derive_delivery_title(run)
    record.description = publish_text
    record.draft_payload_json = json.dumps(draft_payload, ensure_ascii=False)
    record.external_id = str(external_id)
    record.external_status = "submitted_to_douyin"
    record.platform_error_code = None
    record.error_message = None
    record.submitted_at = datetime.now(timezone.utc)
    record.response_json = json.dumps(
        {"upload": upload_payload, "create": create_payload, "account": serialize_social_account(account)},
        ensure_ascii=False,
    )
    await db.commit()
    await db.refresh(record)
    return record


def _derive_topic(run: PipelineRun) -> str:
    title = derive_delivery_title(run)
    return title.replace("成片", "").strip() or title


def _candidate_tags(run: PipelineRun) -> list[str]:
    config = _parse_input_config(run)
    tags = ["#短视频", "#AI创作"]
    if config.get("platform") == "douyin":
        tags.append("#抖音")
    if config.get("style"):
        tags.append(f"#{str(config['style']).strip()}")
    if config.get("background_template_name"):
        tags.append(f"#{str(config['background_template_name']).strip()}")
    script = (config.get("script") or "").strip()
    for token in re.split(r"[\s，。！？、,.!?:：；;]+", script):
        token = token.strip()
        if 2 <= len(token) <= 8 and not token.startswith("#"):
            tags.append(f"#{token}")
        if len(tags) >= 6:
            break
    deduped = []
    seen = set()
    for tag in tags:
        if tag not in seen:
            deduped.append(tag)
            seen.add(tag)
    return deduped[:6]


def build_douyin_publish_draft(
    run: PipelineRun,
    *,
    social_account: SocialAccount | None,
) -> dict[str, Any]:
    config = _parse_input_config(run)
    script = (config.get("script") or "").strip()
    title = derive_delivery_title(run)[:30]
    topic = _derive_topic(run)
    description_body = script[:88] if script else "这条视频已经由 vidgen 自动完成创作。"
    hashtags = _candidate_tags(run)
    description = description_body
    if hashtags:
        description = f"{description_body}\n{' '.join(hashtags)}"
    return {
        "platform": "douyin",
        "pipeline_run_id": run.id,
        "social_account_id": social_account.id if social_account else None,
        "account_name": social_account.display_name if social_account else None,
        "title": title,
        "description": description[:120],
        "hashtags": hashtags,
        "visibility": "public",
        "cover_title": title[:20],
        "topic": topic,
        "risk_tip": "发布后仍可能进入抖音审核或仅自己可见阶段，建议先检查文案、话题和账号选择。",
        "video_source": run.final_video_path,
        "status": "draft",
    }


async def upsert_publish_draft_record(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    run: PipelineRun,
    social_account: SocialAccount | None,
    draft: dict[str, Any],
) -> VideoDelivery:
    result = await db.execute(
        select(VideoDelivery)
        .where(
            VideoDelivery.pipeline_run_id == run.id,
            VideoDelivery.user_id == user_id,
            VideoDelivery.action_type == "publish",
            VideoDelivery.platform == "douyin",
            VideoDelivery.status == "draft",
        )
        .order_by(VideoDelivery.created_at.desc())
        .limit(1)
    )
    record = result.scalars().first()
    if record is None:
        record = VideoDelivery(
            user_id=user_id,
            project_id=project_id,
            pipeline_run_id=run.id,
            action_type="publish",
            platform="douyin",
            status="draft",
        )
        db.add(record)

    record.social_account_id = social_account.id if social_account else None
    record.title = draft.get("title")
    record.description = draft.get("description")
    record.draft_payload_json = json.dumps(draft, ensure_ascii=False)
    record.external_status = "draft_ready"
    record.platform_error_code = None
    record.error_message = None
    await db.commit()
    await db.refresh(record)
    return record
