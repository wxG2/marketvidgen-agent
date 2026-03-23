from __future__ import annotations

import asyncio
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.services.qwen_client import QwenClient


@dataclass
class TTSResult:
    audio_path: str
    duration_ms: int
    usage: dict[str, int] | None = None


class TTSService(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> TTSResult:
        """Generate speech audio from text. Returns audio file path and duration."""
        ...

    @abstractmethod
    async def generate_subtitles(self, text: str, audio_path: str) -> str:
        """Generate SRT subtitle file aligned to audio. Returns subtitle file path."""
        ...


class MockTTSService(TTSService):
    """Mock TTS that creates placeholder files after a simulated delay."""

    def __init__(self, output_dir: str = "./data/generated"):
        self.output_dir = output_dir

    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> TTSResult:
        await asyncio.sleep(2)

        os.makedirs(self.output_dir, exist_ok=True)
        audio_path = os.path.join(self.output_dir, f"tts_{uuid.uuid4().hex[:8]}.wav")

        # Estimate duration: ~150ms per Chinese character, ~80ms per English word
        char_count = len(text)
        duration_ms = int(char_count * 150 / speed)

        # Create a minimal placeholder file
        with open(audio_path, "wb") as f:
            f.write(b"\x00" * 1024)

        return TTSResult(audio_path=audio_path, duration_ms=duration_ms)

    async def generate_subtitles(self, text: str, audio_path: str) -> str:
        await asyncio.sleep(1)

        os.makedirs(self.output_dir, exist_ok=True)
        subtitle_path = os.path.join(self.output_dir, f"sub_{uuid.uuid4().hex[:8]}.srt")

        # Split text into segments and create basic SRT
        segments = [s.strip() for s in text.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").split("\n") if s.strip()]
        srt_content = []
        time_cursor_ms = 0
        segment_duration_ms = 3000

        for i, segment in enumerate(segments, 1):
            start = self._ms_to_srt_time(time_cursor_ms)
            end = self._ms_to_srt_time(time_cursor_ms + segment_duration_ms)
            srt_content.append(f"{i}\n{start} --> {end}\n{segment}\n")
            time_cursor_ms += segment_duration_ms

        with open(subtitle_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_content))

        return subtitle_path

    @staticmethod
    def _ms_to_srt_time(ms: int) -> str:
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


class RealTTSService(TTSService):
    # Valid DashScope CosyVoice / Qwen-TTS voice names
    VALID_VOICES = {
        "Cherry", "Serena", "Ethan", "Chelsie", "Momo", "Vivian", "Moon",
        "Maia", "Kai", "Nofish", "Bella", "Jennifer", "Ryan", "Katerina",
        "Aiden", "Mia", "Mochi", "Bellona", "Vincent", "Bunny", "Neil",
        "Elias", "Arthur", "Nini", "Ebona", "Seren", "Pip", "Stella",
    }
    DEFAULT_VOICE = "Cherry"

    def __init__(self, api_key: str, api_url: str, model: str, output_dir: str = "./data/generated"):
        self.client = QwenClient(api_key=api_key, base_url=api_url, model=model)
        self.output_dir = output_dir

    def _resolve_voice(self, voice_id: str) -> str:
        """Ensure the voice_id is a valid DashScope voice name."""
        if voice_id in self.VALID_VOICES:
            return voice_id
        # Case-insensitive lookup
        for v in self.VALID_VOICES:
            if v.lower() == voice_id.lower():
                return v
        return self.DEFAULT_VOICE

    async def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> TTSResult:
        os.makedirs(self.output_dir, exist_ok=True)
        raw_path = os.path.join(self.output_dir, f"tts_{uuid.uuid4().hex[:8]}_raw.wav")
        audio_path = os.path.join(self.output_dir, f"tts_{uuid.uuid4().hex[:8]}.mp3")
        resolved_voice = self._resolve_voice(voice_id)
        usage = await self.client.tts(
            text=text,
            voice=resolved_voice,
            output_path=raw_path,
            speed=speed,
        )
        # DashScope TTS returns WAV with invalid RIFF size header — browsers can't play it.
        # Convert to MP3 via ffmpeg for browser compatibility and smaller file size.
        duration_ms = await self._convert_to_mp3(raw_path, audio_path)
        if duration_ms <= 0:
            duration_ms = int(max(len(text), 1) * 150 / max(speed, 0.1))
        # Clean up raw WAV
        try:
            os.remove(raw_path)
        except OSError:
            pass
        return TTSResult(audio_path=audio_path, duration_ms=duration_ms, usage=usage)

    @staticmethod
    async def _convert_to_mp3(input_path: str, output_path: str) -> int:
        """Convert audio to MP3 and return duration in ms."""
        import subprocess
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", input_path, "-codec:a", "libmp3lame", "-q:a", "2", output_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg audio conversion failed: {stderr.decode()}")
        # Probe duration
        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "csv=p=0", output_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await probe.communicate()
        try:
            return int(float(stdout.decode().strip()) * 1000)
        except (ValueError, TypeError):
            return 0

    async def generate_subtitles(self, text: str, audio_path: str) -> str:
        os.makedirs(self.output_dir, exist_ok=True)
        subtitle_path = os.path.join(self.output_dir, f"sub_{uuid.uuid4().hex[:8]}.srt")
        segments = [s.strip() for s in text.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").split("\n") if s.strip()]
        if not segments:
            segments = [text.strip() or " "]

        total_duration_ms = max(len(text), 1) * 150
        segment_duration_ms = max(total_duration_ms // len(segments), 1000)
        cursor = 0
        lines = []
        for idx, segment in enumerate(segments, start=1):
            start = self._ms_to_srt_time(cursor)
            end = self._ms_to_srt_time(cursor + segment_duration_ms)
            lines.append(f"{idx}\n{start} --> {end}\n{segment}\n")
            cursor += segment_duration_ms

        with open(subtitle_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return subtitle_path

    @staticmethod
    def _ms_to_srt_time(ms: int) -> str:
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
