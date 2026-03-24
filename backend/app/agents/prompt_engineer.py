from __future__ import annotations

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.prompts import PROMPT_ENGINEER_SYSTEM_PROMPT
from app.services.llm_service import LLMService


class PromptEngineerAgent(BaseAgent):
    """Creative director: generates video prompts per shot and voice design parameters."""

    name = "prompt_engineer"

    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        shots: list[dict] = input_data["shots"]
        style: str = input_data.get("style", "commercial")
        video_type: str = input_data.get("video_type", "commercial")
        platform: str = input_data.get("platform", "generic")

        schema = {
            "name": "prompt_output",
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
                    },
                    "voice_params": {
                        "type": "object",
                        "properties": {
                            "voice_id": {"type": "string"},
                            "speed": {"type": "number"},
                            "tone": {"type": "string"},
                        },
                        "required": ["voice_id", "speed", "tone"],
                    },
                },
                "required": ["shot_prompts", "voice_params"],
            },
        }
        # Build user prompt with shot info (images are passed separately)
        shot_descriptions = []
        image_paths = []
        for shot in shots:
            shot_descriptions.append(
                f"Shot {shot['shot_idx']}: script=\"{shot['script_segment']}\"  duration={shot['duration_seconds']}s"
            )
            image_paths.append(shot["image_path"])

        user_prompt = (
            f"Video type: {video_type} | Style: {style} | Platform: {platform}\n\n"
            + "\n".join(shot_descriptions)
            + "\n\nFor each shot, the corresponding image is attached in the same order. "
            "Observe each image carefully and write a cinematic video prompt that brings it to life."
        )

        usage_records = []
        try:
            llm_output, usage = await self.llm.generate_structured(
                system_prompt=PROMPT_ENGINEER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=schema,
                image_paths=image_paths,
            )
            if await context.is_cancelled():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")
            prompt_map = {item["shot_idx"]: item["video_prompt"] for item in llm_output.get("shot_prompts", [])}
            shot_prompts = []
            for shot in shots:
                shot_prompts.append({
                    "shot_idx": shot["shot_idx"],
                    "image_path": shot["image_path"],
                    "video_prompt": prompt_map.get(
                        shot["shot_idx"],
                        self._build_shot_prompt(
                            shot_idx=shot["shot_idx"],
                            script_segment=shot["script_segment"],
                            style=style,
                            video_type=video_type,
                            duration=shot["duration_seconds"],
                        ),
                    ),
                    "duration_seconds": shot["duration_seconds"],
                    "script_segment": shot["script_segment"],
                })
            voice_params = llm_output.get("voice_params") or self._design_voice(video_type, style, input_data.get("voice_config", {}))
            usage_records.append({
                "provider": "qwen",
                "model_name": getattr(self.llm, "client", None).model if getattr(self.llm, "client", None) else "mock",
                "operation": "prompt_engineer",
                **usage,
            })
        except Exception:
            shot_prompts = []
            for shot in shots:
                prompt_text = self._build_shot_prompt(
                    shot_idx=shot["shot_idx"],
                    script_segment=shot["script_segment"],
                    style=style,
                    video_type=video_type,
                    duration=shot["duration_seconds"],
                )
                shot_prompts.append({
                    "shot_idx": shot["shot_idx"],
                    "image_path": shot["image_path"],
                    "video_prompt": prompt_text,
                    "duration_seconds": shot["duration_seconds"],
                    "script_segment": shot["script_segment"],
                })
            voice_params = self._design_voice(video_type, style, input_data.get("voice_config", {}))

        output = {
            "shot_prompts": shot_prompts,
            "voice_params": voice_params,
        }

        return AgentResult(success=True, output_data=output, usage_records=usage_records)

    def _build_shot_prompt(self, shot_idx: int, script_segment: str, style: str, video_type: str, duration: float) -> str:
        """Build a structured video generation prompt for a single shot."""
        style_descriptors = {
            "commercial": "professional commercial quality, clean composition, bright lighting, 4K resolution",
            "lifestyle": "warm lifestyle aesthetic, natural lighting, candid feel, soft color grading",
            "cinematic": "cinematic look, dramatic lighting, wide aspect ratio, film grain, shallow depth of field",
        }

        motion_options = [
            "smooth dolly forward",
            "gentle pan left to right",
            "slow push-in",
            "elegant tracking shot",
            "subtle zoom out",
            "steady cam follow",
        ]
        motion = motion_options[shot_idx % len(motion_options)]

        style_desc = style_descriptors.get(style, style_descriptors["commercial"])

        prompt = (
            f"A {duration:.0f}-second {motion} shot. "
            f"Scene depicts: {script_segment}. "
            f"Visual style: {style_desc}. "
            f"Smooth natural motion, no abrupt changes, consistent with previous shots."
        )

        return prompt

    def _design_voice(self, video_type: str, style: str, base_config: dict) -> dict:
        """Design TTS voice parameters based on content type.

        Voice IDs must be valid DashScope/CosyVoice names:
        Cherry, Serena, Ethan, Chelsie, Vivian, Maia, Kai, Bella, Ryan, etc.
        """
        voice_presets = {
            "product_demo": {"voice_id": "Cherry", "speed": 1.0, "tone": "informative"},
            "brand_story": {"voice_id": "Ethan", "speed": 0.9, "tone": "narrative"},
            "promotion": {"voice_id": "Vivian", "speed": 1.1, "tone": "exciting"},
            "commercial": {"voice_id": "Cherry", "speed": 1.0, "tone": "confident"},
        }

        preset = voice_presets.get(video_type, voice_presets["commercial"])

        # Allow user override
        if base_config.get("voice_id") and base_config["voice_id"] != "default":
            preset["voice_id"] = base_config["voice_id"]
        if base_config.get("speed"):
            preset["speed"] = base_config["speed"]

        return preset
