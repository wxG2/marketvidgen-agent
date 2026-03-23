from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx


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

        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.get("name", "structured_output"),
                    "schema": response_schema["schema"],
                },
            }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

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
            response.raise_for_status()
            data = response.json()
            audio_url = (((data.get("output") or {}).get("audio") or {}).get("url"))
            if not audio_url:
                raise RuntimeError(f"Qwen TTS did not return audio url: {data}")

            audio_response = await client.get(audio_url)
            audio_response.raise_for_status()
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
