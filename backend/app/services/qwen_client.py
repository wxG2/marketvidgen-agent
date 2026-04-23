from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class QwenClientError(RuntimeError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class QwenRequestError(QwenClientError):
    def __init__(self, message: str):
        super().__init__(message, code="llm_request_failed")


class QwenResponseParseError(QwenClientError):
    def __init__(self, message: str):
        super().__init__(message, code="llm_parse_failed")


class QwenResponseValidationError(QwenClientError):
    def __init__(self, message: str):
        super().__init__(message, code="llm_validation_failed")


class QwenClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def chat_stream_text(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        timeout_seconds: float = 180,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="ignore")[:800]
                    raise RuntimeError(
                        f"Qwen streaming chat request failed ({response.status_code}): body={detail!r}"
                    ) from exc

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if data == "[DONE]":
                        break

                    try:
                        payload_chunk = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed Qwen stream chunk: %s", data[:200])
                        continue

                    for choice in payload_chunk.get("choices") or []:
                        finish_reason = choice.get("finish_reason")
                        if finish_reason == "length":
                            logger.warning("Qwen stream stopped early: finish_reason=length (output token limit reached)")
                            yield "\n\n[输出已达到模型单次长度上限，请发送【继续】以获取后续内容。]"
                        delta = choice.get("delta") or {}
                        content = self._normalize_content(delta.get("content"))
                        if content:
                            yield content

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_paths: Optional[list[str]] = None,
        video_paths: Optional[list[str]] = None,
        response_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image_path in image_paths or []:
            content.append({"type": "image_url", "image_url": {"url": self._file_to_data_url(image_path)}})
        for video_path in video_paths or []:
            content.append({"type": "video_url", "video_url": {"url": self._file_to_data_url(video_path)}})

        use_sse_json = self._uses_omni_multimodal(image_paths=image_paths, video_paths=video_paths)
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if response_schema:
            if use_sse_json:
                # qwen3-omni-flash does not support json_schema response_format
                # with multimodal requests. Use SSE and embed schema instructions instead.
                schema_hint = json.dumps(response_schema.get("schema", {}), ensure_ascii=False)
                payload["messages"][0]["content"] = (
                    payload["messages"][0]["content"]
                    + f"\n\n请严格按照以下 JSON schema 格式返回，只输出 JSON，不要有其他文字：\n{schema_hint}"
                )
            else:
                payload["response_format"] = self._json_schema_response_format(response_schema)

        timeout_seconds = 300 if video_paths else 120
        if use_sse_json:
            payload["modalities"] = ["text"]
            payload["enable_thinking"] = False
            text, usage = await self._collect_sse_response(payload, timeout_seconds=timeout_seconds)
            parsed = self._extract_json_object(text)
            if response_schema:
                self._validate_response_schema(parsed, response_schema)
            return parsed, usage

        data = await self._post_chat_completions(payload, timeout_seconds=timeout_seconds)

        message = self._normalize_content(data["choices"][0]["message"]["content"])
        parsed = self._extract_json_object(message)
        if response_schema:
            self._validate_response_schema(parsed, response_schema)
        usage = data.get("usage", {})
        return parsed, {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }

    async def chat_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_paths: Optional[list[str]] = None,
        video_paths: Optional[list[str]] = None,
        temperature: float = 0.5,
    ) -> tuple[str, dict[str, int]]:
        """Plain-text (non-JSON) multimodal call. Safe to use with video_url."""
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image_path in image_paths or []:
            content.append({"type": "image_url", "image_url": {"url": self._file_to_data_url(image_path)}})
        for video_path in video_paths or []:
            content.append({"type": "video_url", "video_url": {"url": self._file_to_data_url(video_path)}})

        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        timeout_seconds = 120
        data = await self._post_chat_completions(payload, timeout_seconds=timeout_seconds)
        text = self._normalize_content(data["choices"][0]["message"].get("content", ""))
        usage = data.get("usage", {})
        return text, {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }

    async def tts(
        self,
        *,
        text: str,
        voice: str,
        output_path: str,
        speed: float = 1.0,
        model: Optional[str] = None,
    ) -> dict[str, int]:
        payload = {
            "model": model or self.model,
            "input": {
                "text": text,
                "voice": voice,
                "language_type": "Chinese",
            },
        }

        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                self._tts_url(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Qwen TTS request failed ({response.status_code}): {response.text[:500]}"
                )
            data = response.json()
            audio_url = (((data.get("output") or {}).get("audio") or {}).get("url"))
            if not audio_url:
                raise RuntimeError(f"Qwen TTS did not return audio url: {data}")

            audio_response = await client.get(audio_url)
            if audio_response.status_code >= 400:
                raise RuntimeError(
                    f"Qwen TTS audio download failed ({audio_response.status_code}): {audio_response.text[:500]}"
                )
            Path(output_path).write_bytes(audio_response.content)

        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", 0) or 0)
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens or int(usage.get("characters", 0) or 0)

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    async def chat_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict], Awaitable[tuple[str, list[str]]]],
        image_paths: Optional[list[str]] = None,
        video_paths: Optional[list[str]] = None,
        response_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.2,
        max_tool_rounds: int = 5,
    ) -> tuple[dict[str, Any], list[dict], dict[str, int]]:
        """Multi-turn chat with function calling support.

        Args:
            tool_executor: async callback (tool_name, args) -> (text_result, image_paths).
                Returns a text description and optional list of image file paths
                that will be added as visual context in subsequent turns.
            response_schema: if provided, the final turn uses json_schema response format.

        Returns:
            (parsed_json, tool_call_log, aggregated_usage)
        """
        # Build initial user content
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for img_path in image_paths or []:
            content.append({"type": "image_url", "image_url": {"url": self._file_to_data_url(img_path)}})
        for video_path in video_paths or []:
            content.append({"type": "video_url", "video_url": {"url": self._file_to_data_url(video_path)}})

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        tool_call_log: list[dict] = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        final_assistant_message: dict[str, Any] | None = None
        for _round_idx in range(max_tool_rounds):
            payload: dict[str, Any] = {
                "model": self.model,
                "temperature": temperature,
                "messages": messages,
            }
            payload["tools"] = tools

            data = await self._post_chat_completions(
                payload,
                timeout_seconds=300 if video_paths else 180,
            )

            usage = data.get("usage", {})
            total_usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
            total_usage["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
            total_usage["total_tokens"] += int(usage.get("total_tokens", 0) or 0)

            choice = data["choices"][0]
            message = choice["message"]

            # Check for tool calls
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                final_assistant_message = message
                break

            # Process tool calls
            messages.append(message)  # assistant message with tool_calls

            for tc in tool_calls:
                func = tc["function"]
                tool_name = func["name"]
                try:
                    tool_args = json.loads(func["arguments"])
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                logger.info(f"LLM tool call: {tool_name}({tool_args})")
                tool_call_log.append({"tool": tool_name, "args": tool_args})

                # Execute the tool
                text_result, result_images = await tool_executor(tool_name, tool_args)

                # Build tool response content with text and images
                tool_content: list[dict[str, Any]] = [{"type": "text", "text": text_result}]
                for img_path in result_images:
                    tool_content.append({
                        "type": "image_url",
                        "image_url": {"url": self._file_to_data_url(img_path)},
                    })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_content,
                })

        if response_schema:
            # json_schema response_format is incompatible with multimodal content
            # (image_url or video_url) in DashScope qwen3-omni-flash.
            # Fall back to json_object + schema hint whenever any message contains images.
            has_multimodal = bool(video_paths) or self._messages_contain_images(messages)
            if has_multimodal:
                response_format: dict[str, Any] = {"type": "json_object"}
                schema_hint = json.dumps(response_schema.get("schema", {}), ensure_ascii=False)
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "工具调用已经结束。现在请基于以上全部上下文输出最终结构化结果。"
                                "只输出 JSON，不要有任何额外解释。\n\n"
                                f"必须遵循以下 JSON schema：\n{schema_hint}"
                            ),
                        }
                    ],
                })
            else:
                response_format = self._json_schema_response_format(response_schema)

            final_payload: dict[str, Any] = {
                "model": self.model,
                "temperature": temperature,
                "messages": messages,
                "response_format": response_format,
            }
            data = await self._post_chat_completions(
                final_payload,
                timeout_seconds=300 if video_paths else 180,
            )

            usage = data.get("usage", {})
            total_usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
            total_usage["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
            total_usage["total_tokens"] += int(usage.get("total_tokens", 0) or 0)

            text = self._normalize_content(data["choices"][0]["message"].get("content", ""))
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw_response": text}
            return parsed, tool_call_log, total_usage

        text = self._normalize_content((final_assistant_message or {}).get("content", ""))
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = {"raw_response": text}
        return parsed, tool_call_log, total_usage

    @staticmethod
    def _normalize_content(content: Any) -> str:
        """将 LLM 返回的 content 统一为 str（兼容 str 和多模态 list 格式）。"""
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

    def _uses_omni_multimodal(self, *, image_paths: Optional[list[str]], video_paths: Optional[list[str]]) -> bool:
        return "omni" in self.model and bool(image_paths or video_paths)

    async def _collect_sse_response(
        self,
        payload: dict[str, Any],
        *,
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
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
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
                                content = self._normalize_content(delta.get("content"))
                                if content:
                                    chunks.append(content)

                return "".join(chunks), usage
            except (httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.WriteError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                partial_text = "".join(chunks)
                if partial_text:
                    try:
                        self._extract_json_object(partial_text)
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

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
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

    @staticmethod
    def _validate_response_schema(result: dict[str, Any], response_schema: dict[str, Any]) -> None:
        schema = response_schema.get("schema") or {}
        required = schema.get("required") or []
        missing = [field for field in required if field not in result]
        if missing:
            raise QwenResponseValidationError(f"Qwen 返回缺少必填字段：{', '.join(missing)}")

    @staticmethod
    def _messages_contain_images(messages: list[dict[str, Any]]) -> bool:
        """Return True if any message in the list contains image_url content blocks."""
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in {"image_url", "video_url"}:
                        return True
        return False

    @staticmethod
    def _file_to_data_url(file_path: str) -> str:
        path = Path(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _tts_url(self) -> str:
        if self.base_url.endswith("/compatible-mode/v1"):
            return self.base_url.replace(
                "/compatible-mode/v1",
                "/api/v1/services/aigc/multimodal-generation/generation",
            )
        if self.base_url.endswith("/api/v1"):
            return f"{self.base_url}/services/aigc/multimodal-generation/generation"

        parsed = urlparse(self.base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return f"{origin}/api/v1/services/aigc/multimodal-generation/generation"

    @staticmethod
    def _json_schema_response_format(response_schema: dict[str, Any]) -> dict[str, Any]:
        """Build json_schema response_format with strict mode enabled.

        DashScope docs recommend strict=true so models follow schema constraints
        more reliably and reduce malformed structured output.
        """
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.get("name", "structured_output"),
                "strict": False,
                "schema": response_schema["schema"],
            },
        }

    async def _post_chat_completions(
        self,
        payload: dict[str, Any],
        *,
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
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.WriteError) as exc:
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
                retry_delay = 2.0 * (2 ** attempt)  # 2s, 4s, 8s
                logger.warning(
                    "Qwen chat request failed (attempt %s/%s): %r. Retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    last_exc,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)

        raise RuntimeError(f"Qwen chat request failed after retries: {last_exc!r}") from last_exc
