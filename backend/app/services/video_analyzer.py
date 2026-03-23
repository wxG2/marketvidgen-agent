from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class AnalysisResult:
    summary: str
    scene_tags: List[str]
    recommended_categories: List[str]
    raw_response: str = ""


class VideoAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, video_path: str, available_categories: list[str]) -> AnalysisResult:
        ...


class MockVideoAnalyzer(VideoAnalyzer):
    async def analyze(self, video_path: str, available_categories: list[str]) -> AnalysisResult:
        await asyncio.sleep(2)
        cats = available_categories[:5] if available_categories else [
            "环境-大景", "环境-小景", "门头", "室内", "人物"
        ]
        return AnalysisResult(
            summary="该视频展示了一个现代商业空间，包含建筑外观、门头设计、室内环境和人物活动等场景。"
                    "整体风格偏向现代简约，适合商业宣传用途。建议从大景环境入手，逐步过渡到门头特写和室内场景。",
            scene_tags=["商业", "现代", "户外", "室内", "建筑"],
            recommended_categories=cats,
            raw_response=json.dumps({"mock": True, "categories": cats}, ensure_ascii=False),
        )


class Qwen3VLAnalyzer(VideoAnalyzer):
    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_url = api_url

    async def analyze(self, video_path: str, available_categories: list[str]) -> AnalysisResult:
        import httpx
        # TODO: Extract key frames, encode as base64, call Qwen3VL API
        # For now, raise not implemented
        raise NotImplementedError("Qwen3VL integration pending - configure API key and URL")
