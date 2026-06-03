from __future__ import annotations

import asyncio
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.llm.qwen_client import QwenClient


@dataclass
class TTSResult:
    audio_path: str
    duration_ms: int
    usage: dict[str, int] | None = None


class TTSService(ABC):
    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        tone: str | None = None,
    ) -> TTSResult:
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

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        tone: str | None = None,
    ) -> TTSResult:
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

        segments = _split_subtitle_blocks(text)
        if not segments:
            segments = [text.strip() or " "]

        # Try ffprobe for real duration, fall back to char estimate
        total_duration_ms = await _probe_audio_duration_ms(audio_path)
        if total_duration_ms is None:
            total_duration_ms = max(len(text), 1) * 150

        srt_content = _build_srt_entries(segments, total_duration_ms)

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
    TONE_HINTS = {
        "informative": "清晰专业、可信赖、有讲解感",
        "narrative": "沉浸叙事、温和、有画面感",
        "exciting": "明快有活力、带一点兴奋和感染力",
        "confident": "自信笃定、节奏稳定、有说服力",
    }

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

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        tone: str | None = None,
    ) -> TTSResult:
        os.makedirs(self.output_dir, exist_ok=True)
        raw_path = os.path.join(self.output_dir, f"tts_{uuid.uuid4().hex[:8]}_raw.wav")
        audio_path = os.path.join(self.output_dir, f"tts_{uuid.uuid4().hex[:8]}.mp3")
        resolved_voice = self._resolve_voice(voice_id)
        instructions = self._build_instructions(tone=tone, speed=speed)
        usage = await self.client.tts(
            text=text,
            voice=resolved_voice,
            output_path=raw_path,
            speed=speed,
            instructions=instructions,
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

    @classmethod
    def _build_instructions(cls, *, tone: str | None, speed: float) -> str:
        tone_value = (tone or "").strip()
        tone_text = cls.TONE_HINTS.get(tone_value.lower(), tone_value) if tone_value else "自然、亲切、有感染力"
        if speed >= 1.15:
            speed_text = "语速偏快，适合短视频种草节奏，但不要抢话。"
        elif speed <= 0.9:
            speed_text = "语速偏慢，停顿更沉稳，适合叙事和品牌质感。"
        else:
            speed_text = "语速自然，停顿清晰，节奏适合普通短视频旁白。"
        return (
            f"请用{tone_text}的表达方式朗读。"
            f"{speed_text}"
            "整体像真实短视频旁白，口语化、吐字清楚、重音自然，避免机械播报感。"
        )

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
        segments = _split_subtitle_blocks(text)
        if not segments:
            segments = [text.strip() or " "]

        # Try ffprobe for real duration, fall back to char estimate
        total_duration_ms = await _probe_audio_duration_ms(audio_path)
        if total_duration_ms is None:
            total_duration_ms = max(len(text), 1) * 150

        lines = _build_srt_entries(segments, total_duration_ms)

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


# ── Shared subtitle helpers ────────────────────────────────────────────────────

_MAX_SUBTITLE_BLOCK_CHARS = 24
_MIN_SUBTITLE_BLOCK_CHARS = 8

_SENTENCE_BREAKS = {"。", "！", "？", "；", "，", "、", "\n"}
_CLAUSE_BREAKS = {"，", "、", "；"}


async def _probe_audio_duration_ms(audio_path: str) -> int | None:
    """Probe audio file duration in milliseconds using ffprobe. Returns None on failure."""
    if not audio_path or not os.path.exists(audio_path):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "csv=p=0", audio_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return int(float(stdout.decode().strip()) * 1000)
    except Exception:
        pass
    return None


def _split_subtitle_blocks(text: str) -> list[str]:
    """Split text into subtitle blocks of 14-24 CJK chars each.

    Splits on sentence-ending punctuation first, then on clause breaks
    for blocks that are still too long. Never produces blocks with ellipsis.
    """
    if not text or not text.strip():
        return []

    # Step 1: Split on major sentence breaks
    raw_segments: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in _SENTENCE_BREAKS:
            raw_segments.append(current)
            current = ""
    if current.strip():
        raw_segments.append(current)

    # Step 2: Merge short segments and split long ones
    result: list[str] = []
    buffer = ""
    for seg in raw_segments:
        stripped = seg.strip()
        if not stripped:
            continue
        candidate = (buffer + stripped).strip()
        if len(candidate) <= _MAX_SUBTITLE_BLOCK_CHARS:
            buffer = candidate
        else:
            if buffer.strip():
                result.append(buffer.strip())
            if len(stripped) > _MAX_SUBTITLE_BLOCK_CHARS:
                sub_blocks = _split_long_block(stripped)
                if sub_blocks:
                    result.extend(sub_blocks[:-1])
                    buffer = sub_blocks[-1]
                else:
                    buffer = stripped
            else:
                buffer = stripped

    if buffer.strip():
        result.append(buffer.strip())

    # Step 3: Final safety split for any remaining long blocks
    final: list[str] = []
    for block in result:
        if len(block) > _MAX_SUBTITLE_BLOCK_CHARS:
            final.extend(_split_long_block(block))
        else:
            final.append(block)

    return [b for b in final if b.strip()]


def _split_long_block(text: str) -> list[str]:
    """Split an over-long block at natural boundaries into readable pieces."""
    parts: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in _SENTENCE_BREAKS and len(current) >= _MIN_SUBTITLE_BLOCK_CHARS:
            parts.append(current)
            current = ""
        elif len(current) >= _MAX_SUBTITLE_BLOCK_CHARS:
            split_point = len(current)
            for clause_char in _CLAUSE_BREAKS:
                search_start = max(_MIN_SUBTITLE_BLOCK_CHARS, len(current) - 8)
                pos = current.rfind(clause_char, search_start, len(current))
                if pos > 0:
                    split_point = pos + 1
                    break
            parts.append(current[:split_point])
            current = current[split_point:]
    if current.strip():
        parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def _build_srt_entries(segments: list[str], total_duration_ms: int) -> list[str]:
    """Build SRT entries with time distributed proportionally by text length."""
    if not segments:
        return []

    total_chars = sum(len(seg) for seg in segments)
    if total_chars <= 0:
        total_chars = len(segments)

    min_duration_ms = 800
    available_ms = max(total_duration_ms, len(segments) * min_duration_ms)

    base_allocations = [min_duration_ms] * len(segments)
    remaining = available_ms - sum(base_allocations)
    char_lengths = [len(seg) for seg in segments]
    total_chars_for_extra = sum(char_lengths) or 1

    lines = []
    cursor_ms = 0
    for idx, (seg, base_ms) in enumerate(zip(segments, base_allocations), start=1):
        extra = int(remaining * char_lengths[idx - 1] / total_chars_for_extra) if remaining > 0 else 0
        seg_duration_ms = base_ms + extra

        start = _format_srt_time(cursor_ms)
        end = _format_srt_time(cursor_ms + seg_duration_ms)
        lines.append(f"{idx}\n{start} --> {end}\n{seg}\n")
        cursor_ms += seg_duration_ms

    # Clamp last entry end to total duration
    if lines:
        last_end = _format_srt_time(total_duration_ms)
        idx_sep = lines[-1].rfind(" --> ")
        nl_sep = lines[-1].index("\n", idx_sep + 5)
        lines[-1] = lines[-1][:idx_sep + 5] + last_end + lines[-1][nl_sep:]

    return lines


def _format_srt_time(ms: int) -> str:
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
