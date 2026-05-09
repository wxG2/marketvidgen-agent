from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.services.llm.errors import QwenResponseParseError, QwenResponseValidationError


def normalize_content(content: Any) -> str:
    """Normalize LLM content into a string across text and multimodal list formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content) if content is not None else ""


def extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    for idx, char in enumerate(raw):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise QwenResponseParseError("Qwen 返回内容中没有可解析的 JSON 对象")


def validate_response_schema(result: dict[str, Any], response_schema: dict[str, Any]) -> None:
    schema = response_schema.get("schema") or {}
    required = schema.get("required") or []
    missing = [field for field in required if field not in result]
    if missing:
        raise QwenResponseValidationError(f"Qwen 返回缺少必填字段：{', '.join(missing)}")


def messages_contain_images(messages: list[dict[str, Any]]) -> bool:
    """Return True if any message contains image_url or video_url content blocks."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"image_url", "video_url"}:
                    return True
    return False


def file_to_data_url(file_path: str) -> str:
    path = Path(file_path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def tts_url(base_url: str) -> str:
    if base_url.endswith("/compatible-mode/v1"):
        return base_url.replace(
            "/compatible-mode/v1",
            "/api/v1/services/aigc/multimodal-generation/generation",
        )
    if base_url.endswith("/api/v1"):
        return f"{base_url}/services/aigc/multimodal-generation/generation"

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return f"{origin}/api/v1/services/aigc/multimodal-generation/generation"


def json_schema_response_format(response_schema: dict[str, Any]) -> dict[str, Any]:
    """Build json_schema response_format with strict mode disabled for compatibility."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_schema.get("name", "structured_output"),
            "strict": False,
            "schema": response_schema["schema"],
        },
    }
