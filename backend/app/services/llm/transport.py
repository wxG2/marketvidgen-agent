from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.services.llm.errors import QwenRequestError, QwenResponseParseError
from app.services.llm.payloads import extract_json_object, normalize_content

logger = logging.getLogger(__name__)


async def collect_sse_response(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    max_retries: int = 2,
) -> tuple[str, dict[str, int]]:
    stream_payload = dict(payload)
    stream_payload["stream"] = True
    stream_payload["stream_options"] = {"include_usage": True}

    timeout = httpx.Timeout(
        connect=30.0,
        write=timeout_seconds,
        read=timeout_seconds,
        pool=30.0,
    )
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        chunks: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(stream_payload, ensure_ascii=False).encode("utf-8"),
                ) as response:
                    if hasattr(response, "raise_for_status"):
                        response.raise_for_status()
                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed Qwen SSE chunk: %s", data[:200])
                            continue

                        event_usage = event.get("usage") or {}
                        if event_usage:
                            usage = {
                                "prompt_tokens": int(event_usage.get("prompt_tokens", 0) or 0),
                                "completion_tokens": int(event_usage.get("completion_tokens", 0) or 0),
                                "total_tokens": int(event_usage.get("total_tokens", 0) or 0),
                            }

                        for choice in event.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = normalize_content(delta.get("content"))
                            if content:
                                chunks.append(content)

            return "".join(chunks), usage
        except (
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.WriteError,
            httpx.HTTPStatusError,
        ) as exc:
            last_exc = exc
            partial_text = "".join(chunks)
            if partial_text:
                try:
                    extract_json_object(partial_text)
                    return partial_text, usage
                except QwenResponseParseError:
                    pass

        if attempt < max_retries:
            retry_delay = 2.0 * (2 ** attempt)
            logger.warning(
                "Qwen SSE request failed (attempt %s/%s): %r. Retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                last_exc,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)

    raise QwenRequestError(f"Qwen SSE request failed after retries: {last_exc!r}")


async def post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    max_retries: int = 2,
) -> dict[str, Any]:
    timeout = httpx.Timeout(
        connect=30.0,
        write=timeout_seconds,
        read=timeout_seconds,
        pool=30.0,
    )
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                )
            response.raise_for_status()
            return response.json()
        except (
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.WriteError,
        ) as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {429, 500, 502, 503, 504}:
                last_exc = exc
            else:
                detail = ""
                if exc.response is not None:
                    try:
                        body = exc.response.json()
                        error_obj = body.get("error") if isinstance(body, dict) else None
                        if isinstance(error_obj, dict):
                            err_code = error_obj.get("code")
                            err_message = error_obj.get("message")
                            detail = f" code={err_code!r} message={err_message!r}"
                        else:
                            detail = f" body={exc.response.text[:800]!r}"
                    except Exception:
                        detail = f" body={exc.response.text[:800]!r}"
                raise RuntimeError(
                    f"Qwen chat request failed ({status_code}):{detail}"
                ) from exc

        if attempt < max_retries:
            retry_delay = 2.0 * (2 ** attempt)
            logger.warning(
                "Qwen chat request failed (attempt %s/%s): %r. Retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                last_exc,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)

    raise RuntimeError(f"Qwen chat request failed after retries: {last_exc!r}") from last_exc
