from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.pipeline import PipelineRun
from app.services.background_template_learning import learn_background_template_from_run

# 构建各个agent输入
class PipelineExecutorSupportMixin:
    """Shared helpers for pipeline-style executors.

    Executors are expected to provide these attributes:

    - ``orchestrator``
    - ``prompt_engineer``
    - ``audio_agent``
    - ``video_gen_agent``
    - ``video_editor``
    - ``qa_reviewer``
    - ``db_session_factory``
    """

    @staticmethod
    def _build_shot_script(shot_prompts: list[dict[str, Any]]) -> str:
        return "\n".join(
            str(item.get("script_segment") or "").strip()
            for item in shot_prompts
            if isinstance(item, dict) and str(item.get("script_segment") or "").strip()
        )

    def build_audio_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        prompt_plan = artifacts.get("prompt_plan", {})
        orchestrator_plan = artifacts.get("orchestrator_plan", {})
        shot_prompts = prompt_plan.get("shot_prompts", [])
        shot_script = self._build_shot_script(shot_prompts)
        voiceover_no_audio = input_config.get("voiceover_no_audio", input_config.get("no_audio", False))
        return {
            "script": shot_script or orchestrator_plan.get("script") or input_config.get("script", ""),
            "voice_params": prompt_plan.get("voice_params", {}),
            # voiceover_no_audio controls TTS/subtitle generation only.
            # Falls back to legacy no_audio for backwards compatibility.
            "no_audio": voiceover_no_audio,
            "voiceover_no_audio": voiceover_no_audio,
        }

    def build_video_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        prompt_plan = artifacts.get("prompt_plan", {})
        orchestrator_plan = artifacts.get("orchestrator_plan", {})
        source_images = orchestrator_plan.get("source_images") or orchestrator_plan.get("image_context") or []

        # Build fallback lookup by image_idx / shot_idx for backward compat
        source_by_idx = {}
        for item in source_images:
            if not isinstance(item, dict):
                continue
            for key in ("image_idx", "shot_idx"):
                if item.get(key) is not None:
                    source_by_idx[item[key]] = item
                    break

        shot_prompts = []
        for shot in prompt_plan.get("shot_prompts", []):
            if not isinstance(shot, dict):
                continue
            enriched = dict(shot)
            # Trust director's image_path when already set.
            # Fall back to source_images lookup only when image_path is missing.
            if not enriched.get("image_path"):
                lookup_idx = enriched.get("source_image_idx", enriched.get("shot_idx"))
                source = source_by_idx.get(lookup_idx)
                if source and source.get("image_path"):
                    enriched["image_path"] = source["image_path"]
                    enriched.setdefault("source_image", source)
                    enriched.setdefault("image_content", source.get("image_content", ""))
            shot_prompts.append(enriched)

        video_model_no_audio = input_config.get("video_model_no_audio", input_config.get("no_audio", True))
        return {
            "shot_prompts": shot_prompts,
            "source_images": source_images,
            # video_model_no_audio controls model-generated audio track only.
            # Falls back to legacy no_audio for backwards compatibility.
            "no_audio": video_model_no_audio,
            "video_model_no_audio": video_model_no_audio,
            "generation_model": input_config.get("generation_model"),
            "platform": orchestrator_plan.get("platform") or input_config.get("platform", "generic"),
        }

    def build_editor_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        video_clips = artifacts.get("video_clips", {})
        audio = artifacts.get("audio", {})
        prompt_plan = artifacts.get("prompt_plan", {})
        orchestrator_plan = artifacts.get("orchestrator_plan", {})
        duration_source = prompt_plan.get("shot_prompts") or orchestrator_plan.get("shots", [])
        shot_durations = [
            float(shot["duration_seconds"])
            for shot in duration_source
            if isinstance(shot, dict) and shot.get("duration_seconds") is not None
        ]
        return {
            "video_clips": video_clips.get("video_clips", []),
            "audio_path": audio.get("audio_path", ""),
            "subtitle_path": audio.get("subtitle_path", ""),
            "shot_prompts": prompt_plan.get("shot_prompts", []),
            "duration_mode": input_config.get("duration_mode", "fixed"),
            "shot_durations": shot_durations,
            "transition": input_config.get("transition", "none"),
            "transition_duration": input_config.get("transition_duration", 0.5),
            "bgm_mood": input_config.get("bgm_mood", "none"),
            "bgm_volume": input_config.get("bgm_volume", 0.15),
            "watermark_path": input_config.get("watermark_path"),
            # Forward video_model_no_audio so the editor knows whether to preserve
            # the model-generated audio track (when no TTS voiceover is present).
            "video_model_no_audio": input_config.get("video_model_no_audio", input_config.get("no_audio", True)),
        }

    def build_qa_input(self, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        return {
            "shot_prompts": artifacts.get("prompt_plan", {}).get("shot_prompts", []),
            "video_clips": artifacts.get("video_clips", {}).get("video_clips", []),
            "audio": artifacts.get("audio", {}),
            "final_video": artifacts.get("final_video", {}),
            "input_config": input_config,
        }

    def get_agent_map(self) -> dict[str, object]:
        agents = {
            "orchestrator": self.orchestrator,
            "prompt_engineer": self.prompt_engineer,
            "audio_subtitle": self.audio_agent,
            "video_generator": self.video_gen_agent,
            "video_editor": self.video_editor,
        }
        requirement_parser = getattr(self, "requirement_parser", None)
        if requirement_parser is not None:
            agents["requirement_parser"] = requirement_parser
        if self.qa_reviewer is not None:
            agents["qa_reviewer"] = self.qa_reviewer
        return agents

    @staticmethod
    def get_agent_to_artifact_key() -> dict[str, str]:
        return {
            "requirement_parser": "parsed_requirement",
            "orchestrator": "orchestrator_plan",
            "prompt_engineer": "prompt_plan",
            "audio_subtitle": "audio",
            "video_generator": "video_clips",
            "video_editor": "final_video",
            "qa_reviewer": "qa_report",
        }

    def build_agent_input(self, agent_name: str, artifacts: dict[str, Any], input_config: dict[str, Any]) -> dict[str, Any]:
        if agent_name == "requirement_parser":
            return input_config
        if agent_name == "orchestrator":
            return input_config
        if agent_name == "prompt_engineer":
            orchestrator_plan = artifacts.get("orchestrator_plan", {})
            # Merge key input_config fields so director has full context even if
            # orchestrator_plan was built by a different path (e.g. replication).
            merged = dict(orchestrator_plan)
            for key in ("platform", "style", "duration_seconds", "target_duration_seconds",
                        "duration_mode", "voice_id", "background_context"):
                if key not in merged and key in input_config:
                    merged[key] = input_config[key]
            # Ensure voice_config is present
            if "voice_config" not in merged:
                merged["voice_config"] = {
                    "voice_id": input_config.get("voice_id", "default"),
                    "speed": 1.0,
                }
            return merged
        if agent_name == "audio_subtitle":
            return self.build_audio_input(artifacts, input_config)
        if agent_name == "video_generator":
            return self.build_video_input(artifacts, input_config)
        if agent_name == "video_editor":
            return self.build_editor_input(artifacts, input_config)
        if agent_name == "qa_reviewer":
            return self.build_qa_input(artifacts, input_config)
        return {}

    async def _maybe_learn_background_template(self, context, input_config: dict[str, Any]) -> None:
        llm = getattr(self.prompt_engineer, "llm", None) or getattr(self.prompt_engineer, "llm_service", None)
        if llm is None:
            return
        await learn_background_template_from_run(
            db_session_factory=self.db_session_factory,
            llm=llm,
            pipeline_run_id=context.pipeline_run_id,
            input_config=input_config,
            artifacts=context.artifacts,
        )

    async def _update_run(self, pipeline_run_id: str, **kwargs: Any) -> None:
        kwargs["updated_at"] = datetime.now(timezone.utc)
        async with self.db_session_factory() as session:
            run = await session.get(PipelineRun, pipeline_run_id)
            if run:
                if run.status == "cancelled" and kwargs.get("status") != "cancelled":
                    return
                for key, value in kwargs.items():
                    setattr(run, key, value)
                await session.commit()
