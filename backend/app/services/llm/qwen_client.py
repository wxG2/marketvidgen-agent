from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import httpx

from app.services.llm.errors import QwenClientError as QwenClientError
from app.services.llm.errors import QwenRequestError as QwenRequestError
from app.services.llm.errors import QwenResponseParseError as QwenResponseParseError
from app.services.llm.errors import QwenResponseValidationError as QwenResponseValidationError
from app.services.llm.payloads import (
    extract_json_object,
    file_to_data_url,
    json_schema_response_format,
    messages_contain_images,
    normalize_content,
    tts_url,
    validate_response_schema,
)
from app.services.llm.transport import collect_sse_response, post_chat_completions

logger = logging.getLogger(__name__)


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
                        content = normalize_content(delta.get("content"))
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
            content.append({"type": "image_url", "image_url": {"url": file_to_data_url(image_path)}})
        for video_path in video_paths or []:
            content.append({"type": "video_url", "video_url": {"url": file_to_data_url(video_path)}})

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
                payload["response_format"] = json_schema_response_format(response_schema)

        timeout_seconds = 300 if video_paths else 120
        if use_sse_json:
            payload["modalities"] = ["text"]
            payload["enable_thinking"] = False
            text, usage = await self._collect_sse_response(payload, timeout_seconds=timeout_seconds)
            parsed = extract_json_object(text)
            if response_schema:
                validate_response_schema(parsed, response_schema)
            return parsed, usage

        data = await self._post_chat_completions(payload, timeout_seconds=timeout_seconds)

        message = normalize_content(data["choices"][0]["message"]["content"])
        parsed = extract_json_object(message)
        if response_schema:
            validate_response_schema(parsed, response_schema)
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
            content.append({"type": "image_url", "image_url": {"url": file_to_data_url(image_path)}})
        for video_path in video_paths or []:
            content.append({"type": "video_url", "video_url": {"url": file_to_data_url(video_path)}})

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
        text = normalize_content(data["choices"][0]["message"].get("content", ""))
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
                tts_url(self.base_url),
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
            content.append({"type": "image_url", "image_url": {"url": file_to_data_url(img_path)}})
        for video_path in video_paths or []:
            content.append({"type": "video_url", "video_url": {"url": file_to_data_url(video_path)}})

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
                        "image_url": {"url": file_to_data_url(img_path)},
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
            has_multimodal = bool(video_paths) or messages_contain_images(messages)
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
                response_format = json_schema_response_format(response_schema)

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

            text = normalize_content(data["choices"][0]["message"].get("content", ""))
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw_response": text}
            return parsed, tool_call_log, total_usage

        text = normalize_content((final_assistant_message or {}).get("content", ""))
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = {"raw_response": text}
        return parsed, tool_call_log, total_usage

    def _uses_omni_multimodal(self, *, image_paths: Optional[list[str]], video_paths: Optional[list[str]]) -> bool:
        return "omni" in self.model and bool(image_paths or video_paths)

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        return extract_json_object(text)

    @staticmethod
    def _validate_response_schema(result: dict[str, Any], response_schema: dict[str, Any]) -> None:
        validate_response_schema(result, response_schema)

    async def _collect_sse_response(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        max_retries: int = 2,
    ) -> tuple[str, dict[str, int]]:
        return await collect_sse_response(
            base_url=self.base_url,
            api_key=self.api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    async def _post_chat_completions(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        return await post_chat_completions(
            base_url=self.base_url,
            api_key=self.api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
