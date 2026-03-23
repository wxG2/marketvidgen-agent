from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from app.prompts import GENERIC_PROMPT_GENERATION_SYSTEM_PROMPT
from app.services.qwen_client import QwenClient


class LLMService(ABC):
    @abstractmethod
    async def chat_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        ...

    @abstractmethod
    async def generate_prompts(self, context: dict) -> list[dict]:
        ...

    @abstractmethod
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        image_paths: Optional[list[str]] = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        ...


class MockLLMService(LLMService):
    async def chat_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        response = (
            "根据您的需求，我建议以下视频制作方案：\n\n"
            "1. **开场**：使用大景环境素材，展现整体氛围\n"
            "2. **过渡**：切换到门头特写，突出品牌形象\n"
            "3. **主体**：展示室内环境和产品细节\n"
            "4. **结尾**：回到大景或人物互动场景\n\n"
            "我已准备好为您的每个素材生成对应的视频提示词。"
        )
        for char in response:
            yield char
            await asyncio.sleep(0.02)

    async def generate_prompts(self, context: dict) -> list[dict]:
        await asyncio.sleep(1)
        selections = context.get("selections", [])
        analysis_summary = context.get("analysis_summary", "商业宣传视频")
        user_intent = context.get("user_intent", "")

        prompts = []
        for sel in selections:
            category = sel.get("category", "通用")
            prompt_text = self._generate_prompt_for_category(category, analysis_summary)
            prompts.append({
                "material_selection_id": sel.get("id"),
                "prompt_text": prompt_text,
            })
        return prompts

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        image_paths: Optional[list[str]] = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        await asyncio.sleep(0.2)
        return {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _generate_prompt_for_category(self, category: str, summary: str) -> str:
        templates = {
            "环境-大景": "A cinematic wide-angle establishing shot, smooth dolly movement revealing the grand architecture and surroundings, golden hour lighting, professional commercial quality, 4K",
            "环境-小景": "A detailed close-up shot of the environment details, gentle camera pan, soft natural lighting highlighting textures and atmosphere, cinematic depth of field",
            "门头": "A slow push-in shot focusing on the storefront entrance, emphasizing brand signage and architectural details, warm inviting lighting, commercial promotional style",
            "室内": "An elegant interior tracking shot, smooth gliding camera movement through the space, ambient warm lighting, showcasing design details and atmosphere",
            "人物": "A lifestyle portrait shot with natural movement, soft bokeh background, warm color grading, capturing authentic moments and interactions",
        }
        return templates.get(category, f"A professional cinematic shot related to {category}, smooth camera movement, high quality commercial production, 4K resolution")


class RealLLMService(LLMService):
    def __init__(self, api_key: str, api_url: str, model: str):
        self.client = QwenClient(api_key=api_key, base_url=api_url, model=model)

    async def chat_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        flattened = "\n".join(m.get("content", "") for m in messages)
        yield flattened

    async def generate_prompts(self, context: dict) -> list[dict]:
        schema = {
            "name": "prompt_generation",
            "schema": {
                "type": "object",
                "properties": {
                    "shot_prompts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_idx": {"type": "integer"},
                                "video_prompt": {"type": "string"},
                            },
                            "required": ["shot_idx", "video_prompt"],
                        },
                    }
                },
                "required": ["shot_prompts"],
            },
        }
        user_prompt = json.dumps(context, ensure_ascii=False)
        result, _ = await self.generate_structured(
            system_prompt=GENERIC_PROMPT_GENERATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=schema,
        )
        return result.get("shot_prompts", [])

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        image_paths: Optional[list[str]] = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        return await self.client.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=schema,
            image_paths=image_paths,
        )
