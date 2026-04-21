from __future__ import annotations

import re
from typing import Any

from app.config import settings

REQUIREMENT_PARSER_SYSTEM_PROMPT = (
    "You are a video production requirement analyst. "
    "Given the user's free-form message and session context, extract structured video parameters. "
    "Separate the user's creative intent (what the video should communicate) from meta-instructions "
    "(like '帮我做' / 'please create' / 'generate a video of'). "
    "Infer missing parameters from context or leave them null. "
    "Output only valid JSON matching the provided schema."
)

_PLATFORM_ALIASES: dict[str, list[str]] = {
    "xiaohongshu": ["小红书", "rednote", "xiaohongshu", "红书"],
    "douyin": ["抖音", "tiktok", "tik tok", "douyin"],
    "bilibili": ["b站", "哔哩", "bilibili"],
    "generic": ["横版", "通用", "generic", "横屏"],
}

_STYLE_ALIASES: dict[str, list[str]] = {
    "cinematic": ["电影感", "电影", "cinematic", "大片", "史诗"],
    "lifestyle": ["生活方式", "生活感", "种草", "自然", "lifestyle"],
    "vlog": ["vlog", "探店", "日常"],
    "commercial": ["商业", "广告", "营销", "带货", "commercial"],
    "documentary": ["纪录片", "documentary", "纪实"],
}

_BGM_ALIASES: dict[str, list[str]] = {
    "none": ["无配乐", "不要配乐", "不要bgm", "bgm不要", "无bgm", "bgm=none"],
    "upbeat": ["活力", "欢快", "节奏感", "upbeat", "轻快"],
    "calm": ["轻松", "舒缓", "安静", "calm", "平静", "温柔"],
    "cinematic": ["史诗", "震撼", "大气", "cinematic"],
    "energetic": ["energetic", "激情", "热血"],
}


def looks_like_meta_instruction(text: str) -> bool:
    """Detect short creation requests that should not become voiceover copy."""
    normalized = str(text or "").strip().lower()
    if not normalized or len(normalized) > 180:
        return False

    subject_markers = ("素材", "这些图", "这些图片", "参考图", "这个视频", "reference", "asset")
    action_markers = (
        "帮我",
        "请",
        "根据",
        "生成",
        "制作",
        "做一个",
        "设计方案",
        "营销视频",
        "generate",
        "create",
        "make",
    )
    return any(marker in normalized for marker in subject_markers) and any(
        marker in normalized for marker in action_markers
    )


def fast_infer_platform(text: str, fallback: str | None = None) -> str | None:
    lowered = str(text or "").lower()
    for platform, aliases in _PLATFORM_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return platform
    return fallback if fallback in settings.PLATFORM_RESOLUTIONS else None


def fast_infer_style(text: str) -> str | None:
    lowered = str(text or "").lower()
    for style, aliases in _STYLE_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return style
    return None


def fast_infer_bgm(text: str) -> str | None:
    lowered = str(text or "").lower()
    for mood, aliases in _BGM_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return mood
    return None


def fast_infer_duration(text: str) -> int | None:
    lowered = str(text or "").lower()
    minute_match = re.search(r"(\d+(?:\.\d+)?)\s*(分钟|minute|minutes|min)", lowered)
    if minute_match:
        return max(5, min(int(float(minute_match.group(1)) * 60), 300))
    second_match = re.search(r"(\d+(?:\.\d+)?)\s*(秒|s|sec|second|seconds)", lowered)
    if second_match:
        return max(5, min(int(float(second_match.group(1))), 300))
    return None


def needs_llm_requirement_parsing(message: str) -> bool:
    if not message or len(message) < 10:
        return False
    return len(message) >= 30


def build_requirement_schema() -> dict[str, Any]:
    return {
        "name": "parsed_requirement",
        "schema": {
            "type": "object",
            "properties": {
                "user_intent": {
                    "type": "string",
                    "description": "Clean creative brief: what the video should communicate, without meta-instructions like '帮我做' or 'generate a video'.",
                },
                "script": {
                    "type": "string",
                    "description": "Verbatim voiceover/narration script if the user explicitly provided one. Empty string if the user only gave a brief/request.",
                },
                "platform": {
                    "type": "string",
                    "enum": ["xiaohongshu", "douyin", "bilibili", "generic"],
                    "description": "Target publishing platform inferred from the user message.",
                },
                "duration_seconds": {
                    "type": "integer",
                    "description": "Target video duration in seconds, inferred from the user message.",
                },
                "style": {
                    "type": "string",
                    "enum": ["commercial", "lifestyle", "cinematic", "vlog", "documentary"],
                },
                "bgm_mood": {
                    "type": "string",
                    "enum": ["none", "upbeat", "calm", "cinematic", "energetic"],
                },
                "voice_id": {
                    "type": "string",
                    "description": "Preferred TTS voice if mentioned (Cherry, Serena, Ethan, Chelsie, Vivian, Maia, Kai, Bella, Ryan). Leave empty if not specified.",
                },
            },
            "required": ["user_intent", "script"],
        },
    }


def build_requirement_prompt(input_data: dict[str, Any], raw_message: str, image_count: int) -> str:
    return (
        f"User message: {raw_message or '(empty)'}\n\n"
        f"Current session defaults:\n"
        f"- platform: {input_data.get('platform') or 'generic'}\n"
        f"- duration_seconds: {input_data.get('duration_seconds') or 30}\n"
        f"- style: {input_data.get('style') or 'commercial'}\n"
        f"- bgm_mood: {input_data.get('bgm_mood') or 'none'}\n"
        f"- voice_id: {input_data.get('voice_id') or 'default'}\n"
        f"- attached materials: {image_count} image(s)\n\n"
        "Extract the structured video requirement from the user message. "
        "For fields not mentioned by the user, omit them (use session defaults). "
        "The 'user_intent' should be a clean creative brief suitable as a production directive. "
        "The 'script' should only be populated if the user explicitly wrote voiceover copy."
    )


def merge_requirement_fields(input_data: dict[str, Any], llm_result: dict[str, Any] | None) -> dict[str, Any]:
    llm_result = llm_result or {}
    raw_message = str(input_data.get("user_request") or input_data.get("script") or "").strip()
    session_platform = str(input_data.get("platform") or "generic")
    session_duration = _coerce_duration(input_data.get("duration_seconds", 30), 30)
    session_style = str(input_data.get("style") or "commercial")
    session_bgm = str(input_data.get("bgm_mood") or "none")
    session_voice = str(input_data.get("voice_id") or "default")

    fast_platform = fast_infer_platform(raw_message)
    fast_style = fast_infer_style(raw_message)
    fast_bgm = fast_infer_bgm(raw_message)
    fast_duration = fast_infer_duration(raw_message)

    def _pick(llm_key: str, fast_val: Any, session_val: Any) -> Any:
        llm_val = llm_result.get(llm_key)
        if llm_val not in (None, "", 0):
            return llm_val
        if fast_val is not None:
            return fast_val
        return session_val

    parsed_platform = _pick("platform", fast_platform, session_platform)
    if parsed_platform not in settings.PLATFORM_RESOLUTIONS:
        parsed_platform = session_platform if session_platform in settings.PLATFORM_RESOLUTIONS else "generic"

    parsed_duration = _coerce_duration(_pick("duration_seconds", fast_duration, session_duration), session_duration)
    parsed_style = str(_pick("style", fast_style, session_style) or "commercial")
    parsed_bgm = str(_pick("bgm_mood", fast_bgm, session_bgm) or "none")
    parsed_voice = str(llm_result.get("voice_id") or session_voice)

    explicit_script = str(llm_result.get("script") or "").strip()
    raw_script = str(input_data.get("script") or input_data.get("explicit_script") or "").strip()
    if not explicit_script and raw_script and not looks_like_meta_instruction(raw_script):
        explicit_script = raw_script

    user_intent = str(llm_result.get("user_intent") or "").strip() or raw_message

    return {
        "user_intent": user_intent,
        "explicit_script": explicit_script,
        "platform": parsed_platform,
        "duration_seconds": parsed_duration,
        "style": parsed_style,
        "bgm_mood": parsed_bgm,
        "voice_id": parsed_voice,
        "image_ids": input_data.get("image_ids") or [],
        "duration_mode": input_data.get("duration_mode", "fixed"),
        "generation_model": input_data.get("generation_model", "seedance1.5-pro"),
        "no_audio": _coerce_bool(input_data.get("no_audio", True), True),
        "video_model_no_audio": _coerce_bool(
            input_data.get("video_model_no_audio", input_data.get("no_audio", True)),
            True,
        ),
        "voiceover_no_audio": _coerce_bool(
            input_data.get("voiceover_no_audio", input_data.get("no_audio", False)),
            False,
        ),
        "transition": input_data.get("transition", "none"),
        "transition_duration": input_data.get("transition_duration", 0.5),
        "bgm_volume": input_data.get("bgm_volume", 0.15),
        "background_template_id": input_data.get("background_template_id"),
        "watermark_image_id": input_data.get("watermark_image_id"),
        "background_context": input_data.get("background_context", ""),
        "reference_video_id": input_data.get("reference_video_id"),
    }


def _coerce_duration(value: object, fallback: int) -> int:
    try:
        duration = int(float(str(value).strip()))
    except (TypeError, ValueError):
        duration = int(fallback)
    return max(5, min(duration, 300))


def _coerce_bool(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return fallback
