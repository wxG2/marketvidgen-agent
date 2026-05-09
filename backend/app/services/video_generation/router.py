from __future__ import annotations

from app.services.video_generation.base import GenerationStatus, GenerationTask, VideoGenerator


class VideoGeneratorRouter(VideoGenerator):
    """Route generation calls to the configured backend by per-run model choice."""

    def __init__(
        self,
        *,
        default_model: str,
        seedance_15: VideoGenerator | None = None,
        seedance_20: VideoGenerator | None = None,
        kling: VideoGenerator | None = None,
        fallback: VideoGenerator | None = None,
        kling_model_name: str = "kling-v3",
        seedance_15_model_name: str = "doubao-seedance-1-5-pro-251215",
        seedance_20_model_name: str = "doubao-seedance-2-0-260128",
    ) -> None:
        self.default_model = default_model or "seedance1.5-pro"
        self._generators: dict[str, VideoGenerator] = {}
        self._task_generators: dict[str, VideoGenerator] = {}
        self._fallback = fallback
        self._aliases: dict[str, str] = {}

        if seedance_15 is not None:
            self._register(
                "seedance1.5-pro",
                seedance_15,
                aliases=["seedance", "seedance1.5", "seedance-1.5-pro", seedance_15_model_name],
            )
        if seedance_20 is not None:
            self._register(
                "seedance2.0",
                seedance_20,
                aliases=["seedance2", "seedance-2.0", seedance_20_model_name],
            )
        if kling is not None:
            self._register("kling", kling, aliases=["wavespeed", "kling3", kling_model_name])

    @staticmethod
    def _normalize_model(value: str | None) -> str:
        return (value or "").strip().lower().replace("_", "-")

    def _register(self, key: str, generator: VideoGenerator, aliases: list[str]) -> None:
        normalized_key = self._normalize_model(key)
        self._generators[normalized_key] = generator
        self._aliases[normalized_key] = normalized_key
        for alias in aliases:
            normalized_alias = self._normalize_model(alias)
            if normalized_alias:
                self._aliases[normalized_alias] = normalized_key

    def _select_generator(self, generation_model: str | None) -> tuple[str, VideoGenerator]:
        requested = self._normalize_model(generation_model or self.default_model)
        canonical = self._aliases.get(requested)
        if canonical and canonical in self._generators:
            return canonical, self._generators[canonical]
        if not generation_model and self._fallback is not None:
            return "mock", self._fallback
        available = ", ".join(sorted(self._generators)) or "none"
        raise RuntimeError(
            f"Video generation model '{generation_model or self.default_model}' is not configured. "
            f"Available models: {available}."
        )

    async def generate(
        self,
        image_path: str,
        prompt: str,
        duration: int = 5,
        no_audio: bool = True,
        generation_model: str | None = None,
        platform: str = "generic",
    ) -> GenerationTask:
        canonical, generator = self._select_generator(generation_model)
        task = await generator.generate(
            image_path=image_path,
            prompt=prompt,
            duration=duration,
            no_audio=no_audio,
            generation_model=canonical,
            platform=platform,
        )
        self._task_generators[task.task_id] = generator
        return task

    async def poll_status(self, task_id: str) -> GenerationStatus:
        generator = self._task_generators.get(task_id)
        if generator is None:
            return GenerationStatus(task_id=task_id, status="failed", error="Task generator not found")
        return await generator.poll_status(task_id)
