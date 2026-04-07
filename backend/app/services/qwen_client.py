from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class QwenClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

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
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._file_to_data_url(image_path)},
                }
            )
        for video_path in video_paths or []:
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": self._file_to_data_url(video_path)},
                }
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if response_schema:
            if video_paths:
                # qwen3-omni-flash does not support json_schema response_format
                # when video_url is in the request; fall back to json_object mode
                # and embed schema instructions in the system prompt instead.
                payload["response_format"] = {"type": "json_object"}
                schema_hint = json.dumps(response_schema.get("schema", {}), ensure_ascii=False)
                payload["messages"][0]["content"] = (
                    payload["messages"][0]["content"]
                    + f"\n\n请严格按照以下 JSON schema 格式返回，只输出 JSON，不要有其他文字：\n{schema_hint}"
                )
            else:
                payload["response_format"] = self._json_schema_response_format(response_schema)

        timeout_seconds = 300 if video_paths else 120
        data = await self._post_chat_completions(payload, timeout_seconds=timeout_seconds)

        message = data["choices"][0]["message"]["content"]
        parsed = json.loads(message)
        usage = data.get("usage", {})
        return parsed, {
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
            content.append({
                "type": "image_url",
                "image_url": {"url": self._file_to_data_url(img_path)},
            })
        for video_path in video_paths or []:
            content.append({
                "type": "video_url",
                "video_url": {"url": self._file_to_data_url(video_path)},
            })

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
            finish_reason = choice.get("finish_reason", "stop")

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
            if video_paths:
                # Same restriction as chat_json: json_schema + video_url → 400
                response_format: dict[str, Any] = {"type": "json_object"}
                schema_hint = json.dumps(response_schema.get("schema", {}), ensure_ascii=False)
                # For video_url requests, add an explicit final synthesis turn.
                # This is more reliable than only mutating the initial user turn after several tool rounds.
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

            text = data["choices"][0]["message"].get("content", "")
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw_response": text}
            return parsed, tool_call_log, total_usage

        text = (final_assistant_message or {}).get("content", "")
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = {"raw_response": text}
        return parsed, tool_call_log, total_usage

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
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError, httpx.WriteError) as exc:
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
                retry_delay = 1.2 * (attempt + 1)
                logger.warning(
                    "Qwen chat request failed (attempt %s/%s): %r. Retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    last_exc,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)

        raise RuntimeError(f"Qwen chat request failed after retries: {last_exc!r}") from last_exc
