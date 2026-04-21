from __future__ import annotations

from app.agents.core.base import AgentContext, AgentResult, BaseAgent
from app.services.tts_service import TTSService


class AudioSubtitleAgent(BaseAgent):
    """Produces the audio layer: TTS speech + aligned subtitles."""

    name = "audio_subtitle"

    def __init__(self, tts_service: TTSService):
        self.tts = tts_service

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        script: str = str(input_data.get("script") or "").strip()
        voiceover_no_audio = input_data.get("voiceover_no_audio", input_data.get("no_audio", False))
        if voiceover_no_audio:
            await context.report_progress("audio_subtitle: 已跳过（voiceover_no_audio=true，不生成配音）", agent_name=self.name)
            return AgentResult(
                success=True,
                output_data={
                    "audio_path": "",
                    "subtitle_path": "",
                    "duration_ms": 0,
                    "skipped": True,
                    "skip_reason": "voiceover_no_audio",
                },
            )
        if not script:
            await context.report_progress("audio_subtitle: 已跳过（script 为空）", agent_name=self.name)
            return AgentResult(
                success=True,
                output_data={
                    "audio_path": "",
                    "subtitle_path": "",
                    "duration_ms": 0,
                    "skipped": True,
                    "skip_reason": "empty_script",
                },
            )

        voice_params: dict = input_data.get("voice_params", {})

        voice_id = voice_params.get("voice_id", "default")
        speed = voice_params.get("speed", 1.0)

        # Generate speech audio
        tts_result = await self.tts.synthesize(text=script, voice_id=voice_id, speed=speed)
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        # Generate subtitles aligned to audio
        subtitle_path = await self.tts.generate_subtitles(text=script, audio_path=tts_result.audio_path)
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        output = {
            "audio_path": tts_result.audio_path,
            "subtitle_path": subtitle_path,
            "duration_ms": tts_result.duration_ms,
        }

        usage_records = []
        if tts_result.usage:
            usage_records.append({
                "provider": "qwen",
                "model_name": "qwen-tts",
                "operation": "tts",
                **tts_result.usage,
            })

        return AgentResult(success=True, output_data=output, usage_records=usage_records)
