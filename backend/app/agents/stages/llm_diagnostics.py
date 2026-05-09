from __future__ import annotations

from app.agents.core.base import describe_exception
from app.services.llm.qwen_client import (
    QwenRequestError,
    QwenResponseParseError,
    QwenResponseValidationError,
)


def llm_failure_label(exc: BaseException) -> str:
    if isinstance(exc, QwenRequestError) or getattr(exc, "code", "") == "llm_request_failed":
        return "Qwen 请求失败"
    if isinstance(exc, QwenResponseParseError) or getattr(exc, "code", "") == "llm_parse_failed":
        return "Qwen 返回解析失败"
    if isinstance(exc, QwenResponseValidationError) or getattr(exc, "code", "") == "llm_validation_failed":
        return "Qwen 返回校验失败"
    return "Qwen 调用失败"


def short_error(exc: BaseException, *, limit: int = 160) -> str:
    message = describe_exception(exc).replace("\n", " ").strip()
    if len(message) <= limit:
        return message
    return message[: limit - 1].rstrip() + "..."
