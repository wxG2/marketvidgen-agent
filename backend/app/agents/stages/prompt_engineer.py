from __future__ import annotations

import logging

from app.agents.core.base import AgentContext, AgentResult, BaseAgent, describe_exception
from app.agents.stages.llm_diagnostics import llm_failure_label, short_error
from app.config import settings
from app.prompts import PROMPT_ENGINEER_SYSTEM_PROMPT
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

# Visual-role → relative duration weight for rhythm-based fallback.
# Weights are normalised against the target total, so they only affect ratios.
_ROLE_DURATION_WEIGHTS: dict[str, float] = {
    "brand_identity": 0.65,   # logo / hook → keep tight
    "hook": 0.65,
    "product_hero": 1.30,     # product showcase → give it room
    "lifestyle_scene": 1.20,  # contextual lifestyle → slightly longer
    "cta_moment": 0.80,       # call-to-action → medium
    "testimonial": 1.00,
    "social_proof": 0.90,
}
_DEFAULT_WEIGHT = 1.0


def _generation_duration_for(duration_seconds: float) -> float:
    """Return the smallest model-supported generation duration that is >= duration_seconds.

    ``duration_seconds`` is the final-cut presentation time chosen by the director.
    The generation model must produce at least this many seconds so the editor can
    trim to the exact target.  If all supported durations are smaller than the
    target (i.e. the shot is longer than the model can produce), return the maximum
    supported duration so the caller can decide whether to fail loudly.
    """
    supported = settings.SEEDANCE_SUPPORTED_DURATIONS
    candidates = [d for d in supported if d >= duration_seconds]
    if candidates:
        return float(min(candidates))
    return float(max(supported))


def _snap_to_half_second(value: float, min_dur: float = 1.0) -> float:
    """Snap *value* to 0.5 s granularity and enforce *min_dur*."""
    snapped = round(float(value) * 2) / 2
    return max(min_dur, snapped)


def _duration_range_label(duration_seconds: float) -> str:
    """Return a human-readable pacing bucket label for *duration_seconds*."""
    if duration_seconds < 2.0:
        return "1-2s"
    elif duration_seconds < 6.0:
        return "2-6s"
    else:
        return "6-10s"


def _rhythmic_durations(
    visual_roles: list[str],
    target: float,
    min_dur: float = 1.0,
) -> list[float]:
    """Distribute *target* seconds across shots proportionally by visual role.

    Each shot gets at least *min_dur* seconds. The returned list always sums
    to exactly *target* (within floating-point precision).
    """
    n = len(visual_roles)
    if n == 0:
        return []
    weights = [_ROLE_DURATION_WEIGHTS.get(r, _DEFAULT_WEIGHT) for r in visual_roles]
    total_weight = sum(weights)
    raw = [target * w / total_weight for w in weights]
    # Enforce minimum per shot
    clamped = [max(min_dur, r) for r in raw]
    # Re-scale to match target exactly
    scale = target / sum(clamped) if sum(clamped) > 0 else 1.0
    durations = [round(d * scale, 1) for d in clamped]
    # Absorb floating-point residue into the longest shot
    diff = round(target - sum(durations), 1)
    if abs(diff) >= 0.05:
        longest = max(range(n), key=lambda i: durations[i])
        durations[longest] = round(durations[longest] + diff, 1)
    return durations


def _normalize_total_duration(
    shot_prompts: list[dict],
    target_duration: float,
) -> list[dict]:
    """Adjust shot durations so their sum equals *target_duration*.

    Only the final shot (or the longest, if the final cannot absorb the delta)
    is modified so the director's intended pacing is preserved.
    """
    if not shot_prompts or target_duration <= 0:
        return shot_prompts

    total = sum(s.get("duration_seconds", 0.0) for s in shot_prompts)
    delta = round(target_duration - total, 2)
    if abs(delta) < 0.05:
        return shot_prompts

    result = [dict(s) for s in shot_prompts]

    # Try last shot first
    last = result[-1]
    new_dur = _snap_to_half_second(last["duration_seconds"] + delta)
    if new_dur >= 1.0:
        last["duration_seconds"] = new_dur
        last["generation_duration_seconds"] = _generation_duration_for(new_dur)
        return result

    # Fall back to longest shot
    longest_idx = max(range(len(result)), key=lambda i: result[i].get("duration_seconds", 0.0))
    new_dur = _snap_to_half_second(result[longest_idx]["duration_seconds"] + delta)
    if new_dur >= 1.0:
        result[longest_idx]["duration_seconds"] = new_dur
        result[longest_idx]["generation_duration_seconds"] = _generation_duration_for(new_dur)
        return result

    # Proportional fallback (shouldn't normally reach here)
    scale = target_duration / total if total > 0 else 1.0
    for s in result:
        s["duration_seconds"] = _snap_to_half_second(s["duration_seconds"] * scale)
        s["generation_duration_seconds"] = _generation_duration_for(s["duration_seconds"])
    return result


def _as_str(value: object) -> str:
    if value is None:
        return ""
    return str(value) if not isinstance(value, str) else value


class PromptEngineerAgent(BaseAgent):
    """Director agent: creates the full shot plan, narration, timing and Seedance visual prompts."""

    name = "prompt_engineer"

    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        await context.report_progress("导演 Agent 开始创作分镜方案。", agent_name=self.name)

        # ── Input parsing ──────────────────────────────────────────────────
        source_images: list[dict] = (
            input_data.get("source_images")
            or input_data.get("image_context")
            or []
        )
        # Replication path: pre-existing shots from ReplicationPlannerAgent
        existing_shots: list[dict] | None = input_data.get("shots") if input_data.get("shots") else None

        creative_brief: str = _as_str(input_data.get("creative_brief") or input_data.get("user_request")).strip()
        explicit_script: str = _as_str(input_data.get("explicit_script") or input_data.get("script")).strip()
        platform: str = _as_str(input_data.get("platform") or "generic").strip()
        style: str = _as_str(input_data.get("style") or "commercial").strip()
        video_type: str = _as_str(input_data.get("video_type") or "commercial").strip()
        background_context: str = _as_str(input_data.get("background_context")).strip()
        target_duration_raw = input_data.get("target_duration_seconds") or input_data.get("duration_seconds")
        try:
            target_duration: float | None = float(target_duration_raw) if target_duration_raw is not None else None
        except (TypeError, ValueError):
            target_duration = None
        duration_mode: str = _as_str(input_data.get("duration_mode") or "fixed").strip()
        voice_config: dict = input_data.get("voice_config") or {}

        # ── Build image list and paths for multimodal call ─────────────────
        if existing_shots:
            # Replication path: each shot carries its own reference frame
            llm_image_paths = [s["image_path"] for s in existing_shots if s.get("image_path")]
        else:
            llm_image_paths = [img["image_path"] for img in source_images if img.get("image_path")]

        num_images = len(llm_image_paths)
        if num_images == 0:
            return AgentResult(success=False, output_data={}, error="No image paths available for director agent")

        # ── JSON schema ────────────────────────────────────────────────────
        schema = {
            "name": "director_plan",
            "schema": {
                "type": "object",
                "properties": {
                    "director_summary": {"type": "string"},
                    "creative_concept": {"type": "string"},
                    "pacing_strategy": {"type": "string"},
                    "narration_script": {"type": "string"},
                    "voice_design": {
                        "type": "object",
                        "properties": {
                            "voice_id": {"type": "string"},
                            "speed": {"type": "number"},
                            "tone": {"type": "string"},
                        },
                        "required": ["voice_id", "speed", "tone"],
                    },
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_idx": {"type": "integer"},
                                "source_image_idx": {"type": "integer"},
                                "shot_purpose": {"type": "string"},
                                "script_segment": {"type": "string"},
                                "duration_seconds": {"type": "number"},
                                "camera_movement": {"type": "string"},
                                "video_prompt": {"type": "string"},
                            },
                            "required": [
                                "shot_idx", "source_image_idx",
                                "script_segment", "duration_seconds", "video_prompt",
                            ],
                        },
                    },
                },
                "required": ["shots", "voice_design"],
            },
        }

        # ── User prompt ────────────────────────────────────────────────────
        min_gen = settings.VIDEO_GEN_MIN_DURATION_SECONDS
        max_gen = settings.VIDEO_GEN_MAX_DURATION_SECONDS

        if existing_shots:
            # Replication path: present shots as hard constraints
            shot_constraint_lines = []
            for s in existing_shots:
                line = (
                    f"镜头{s['shot_idx']}: script=\"{s.get('script_segment', '')}\"  "
                    f"duration={s.get('duration_seconds', settings.SEEDANCE_DURATION)}s"
                )
                if s.get("visual_design"):
                    line += f"\n  visual_design: {s['visual_design']}"
                shot_constraint_lines.append(line)
            constraint_block = (
                "\n\n## 复刻约束（强制，不得忽略或重排）\n" + "\n".join(shot_constraint_lines)
            )
            image_section = f"参考帧共 {num_images} 帧，按镜头顺序附上。"
        else:
            # Normal path: image inventory with role context
            image_lines = []
            for i, img in enumerate(source_images):
                content = _as_str(img.get("image_content") or img.get("summary")).strip()
                role = _as_str(img.get("visual_role") or img.get("marketing_angle")).strip()
                subjects = ", ".join(img.get("key_subjects") or [])
                line = f"素材 {i}: {content or '(未描述)'}"
                if role:
                    line += f" | 营销角色: {role}"
                if subjects:
                    line += f" | 关键元素: {subjects}"
                image_lines.append(line)
            constraint_block = ""
            image_section = f"素材库（{num_images} 张，按顺序附上）:\n" + "\n".join(image_lines)

        duration_hint = (
            f"- 目标总时长: {target_duration}s（所有镜头 duration_seconds 之和必须等于此值，允许 ±0.5s 误差）\n"
            f"- 时长分配策略（{duration_mode} 模式）：根据素材营销角色决定每镜头时长节奏\n"
            f"  * hook/brand_identity 镜头: 建议 1-2s（极短切入，吸睛）\n"
            f"  * product_hero/lifestyle_scene 镜头: 建议 2-6s，产品细节展示可到 6-10s\n"
            f"  * cta_moment 镜头: 建议 2-4s\n"
            f"  * 每镜头 duration_seconds 优先使用整数秒（1、2、3…），必要时可用 0.5s 粒度（如 1.5、2.5）\n"
            f"  * 单镜头不得超过 {int(max_gen)}s；如内容需要更长，必须拆分成多个镜头\n"
        ) if target_duration else (
            f"- 目标时长未指定，请根据内容需求合理分配\n"
            f"  * 每镜头优先 2-5s，优先整数秒；单镜头不得超过 {int(max_gen)}s\n"
        )

        user_prompt = (
            f"## 创作需求\n{creative_brief or '(用户未提供明确要求)'}\n\n"
            + (f"## 参考脚本\n{explicit_script}\n\n" if explicit_script else "")
            + f"## {image_section}\n\n"
            + (f"## 背景信息\n{background_context}\n\n" if background_context else "")
            + f"## 约束\n"
            + f"- 平台: {platform} | 风格: {style} | 视频类型: {video_type}\n"
            + duration_hint
            + constraint_block
        )

        # ── LLM call ───────────────────────────────────────────────────────
        usage_records = []
        try:
            await context.report_progress("调用 LLM 生成导演分镜方案。", agent_name=self.name)
            llm_output, usage = await self.llm.generate_structured(
                system_prompt=PROMPT_ENGINEER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=schema,
                image_paths=llm_image_paths,
            )
            if await context.is_cancelled():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

            llm_shots: list[dict] = llm_output.get("shots", [])
            voice_design: dict = llm_output.get("voice_design") or {}
            director_summary = _as_str(llm_output.get("director_summary")).strip()
            creative_concept = _as_str(llm_output.get("creative_concept")).strip()
            pacing_strategy = _as_str(llm_output.get("pacing_strategy")).strip()
            narration_script = _as_str(llm_output.get("narration_script")).strip()

            usage_records.append({
                "provider": "qwen",
                "model_name": getattr(self.llm, "client", None).model if getattr(self.llm, "client", None) else "mock",
                "operation": "prompt_engineer",
                **usage,
            })
        except Exception as exc:
            logger.warning(
                "PromptEngineer director LLM call failed: %s",
                describe_exception(exc),
                exc_info=True,
            )
            await context.report_progress(
                f"{llm_failure_label(exc)}，已使用本地导演提示词兜底。错误: {short_error(exc)}",
                agent_name=self.name,
            )
            llm_shots = []
            voice_design = {}
            director_summary = ""
            creative_concept = ""
            pacing_strategy = ""
            narration_script = ""

        # ── Build shot_prompts ─────────────────────────────────────────────
        shot_prompts = self._build_shot_prompts(
            llm_shots=llm_shots,
            source_images=source_images,
            existing_shots=existing_shots,
            style=style,
            video_type=video_type,
            target_duration=target_duration,
        )

        # ── Normalize total duration ───────────────────────────────────────
        if target_duration:
            shot_prompts = _normalize_total_duration(shot_prompts, target_duration)

        # ── Voice params ───────────────────────────────────────────────────
        voice_params = self._build_voice_params(voice_design, video_type, style, voice_config)

        output = {
            "shot_prompts": shot_prompts,
            "voice_params": voice_params,
            "director_summary": director_summary,
            "creative_concept": creative_concept,
            "pacing_strategy": pacing_strategy,
            "narration_script": narration_script,
            "voice_design": voice_design,
        }
        return AgentResult(success=True, output_data=output, usage_records=usage_records)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _build_shot_prompts(
        self,
        llm_shots: list[dict],
        source_images: list[dict],
        existing_shots: list[dict] | None,
        style: str,
        video_type: str,
        target_duration: float | None = None,
    ) -> list[dict]:
        """Assemble shot_prompts from LLM output with fallbacks.

        Each shot in the returned list contains:
        - ``duration_seconds``: final-cut presentation time (float, director's choice)
        - ``generation_duration_seconds``: clamped duration sent to the video model
        """
        llm_map = {
            s["shot_idx"]: s
            for s in llm_shots
            if isinstance(s, dict) and "shot_idx" in s
        }

        if existing_shots:
            base_shots = existing_shots
        elif source_images:
            base_shots = [{"shot_idx": i, "source_image_idx": i} for i in range(len(source_images))]
        else:
            base_shots = []

        # Rhythm-based fallback durations (used when LLM gives no duration)
        visual_roles = []
        for base in base_shots:
            src_idx = int(base.get("source_image_idx", base.get("shot_idx", 0)))
            if source_images and src_idx < len(source_images):
                role = _as_str(source_images[src_idx].get("visual_role")).strip()
            else:
                role = _as_str(base.get("visual_role")).strip()
            visual_roles.append(role)

        fallback_dur: list[float] = []
        if target_duration and base_shots:
            fallback_dur = _rhythmic_durations(visual_roles, target_duration)
        default_per_shot = float(settings.SEEDANCE_DURATION)

        shot_prompts = []
        for i, base in enumerate(base_shots):
            idx = base["shot_idx"]
            llm_shot = llm_map.get(idx, {})

            src_idx = int(llm_shot.get("source_image_idx", base.get("source_image_idx", idx)))
            src_idx = max(0, min(src_idx, len(source_images) - 1)) if source_images else idx

            if source_images and src_idx < len(source_images):
                source_img = source_images[src_idx]
                image_path = source_img["image_path"]
                image_content = _as_str(source_img.get("image_content") or source_img.get("summary"))
            else:
                image_path = _as_str(base.get("image_path"))
                image_content = _as_str(base.get("image_content"))
                source_img = base

            script_segment = _as_str(
                llm_shot.get("script_segment") or base.get("script_segment")
            ).strip()

            # Duration: prefer LLM output, then existing shot, then rhythm fallback, then default
            llm_dur_raw = llm_shot.get("duration_seconds") or base.get("duration_seconds")
            if llm_dur_raw is not None:
                try:
                    duration_seconds = _snap_to_half_second(float(llm_dur_raw))
                except (TypeError, ValueError):
                    duration_seconds = fallback_dur[i] if i < len(fallback_dur) else default_per_shot
            else:
                duration_seconds = fallback_dur[i] if i < len(fallback_dur) else default_per_shot

            generation_duration_seconds = _generation_duration_for(duration_seconds)

            video_prompt = _as_str(
                llm_shot.get("video_prompt") or base.get("visual_design")
            ).strip()
            if not video_prompt:
                video_prompt = self._fallback_prompt(
                    shot_idx=idx,
                    image_content=image_content or script_segment,
                    style=style,
                    video_type=video_type,
                    duration=duration_seconds,
                )

            shot_prompts.append({
                "shot_idx": idx,
                "source_image_idx": src_idx,
                "image_path": image_path,
                "image_content": image_content,
                "source_image": source_img,
                "shot_purpose": _as_str(llm_shot.get("shot_purpose")).strip(),
                "script_segment": script_segment,
                "duration_seconds": duration_seconds,
                "duration_range_label": _duration_range_label(duration_seconds),
                "generation_duration_seconds": generation_duration_seconds,
                "camera_movement": _as_str(llm_shot.get("camera_movement")).strip(),
                "video_prompt": video_prompt,
            })

        # Extra shots LLM inserted beyond the base set
        base_indices = {b["shot_idx"] for b in base_shots}
        for llm_shot in llm_shots:
            idx = llm_shot.get("shot_idx")
            if idx not in base_indices:
                src_idx = int(llm_shot.get("source_image_idx", 0))
                src_idx = max(0, min(src_idx, len(source_images) - 1)) if source_images else 0
                source_img = source_images[src_idx] if source_images else {}
                try:
                    duration_seconds = _snap_to_half_second(
                        float(llm_shot.get("duration_seconds") or default_per_shot)
                    )
                except (TypeError, ValueError):
                    duration_seconds = default_per_shot
                generation_duration_seconds = _generation_duration_for(duration_seconds)
                video_prompt = _as_str(llm_shot.get("video_prompt")).strip() or self._fallback_prompt(
                    shot_idx=idx,
                    image_content=_as_str(source_img.get("image_content")),
                    style=style,
                    video_type=video_type,
                    duration=duration_seconds,
                )
                shot_prompts.append({
                    "shot_idx": idx,
                    "source_image_idx": src_idx,
                    "image_path": source_img.get("image_path", ""),
                    "image_content": _as_str(source_img.get("image_content")),
                    "source_image": source_img,
                    "shot_purpose": _as_str(llm_shot.get("shot_purpose")).strip(),
                    "script_segment": _as_str(llm_shot.get("script_segment")).strip(),
                    "duration_seconds": duration_seconds,
                    "duration_range_label": _duration_range_label(duration_seconds),
                    "generation_duration_seconds": generation_duration_seconds,
                    "camera_movement": _as_str(llm_shot.get("camera_movement")).strip(),
                    "video_prompt": video_prompt,
                })

        return shot_prompts

    def _build_voice_params(
        self,
        voice_design: dict,
        video_type: str,
        style: str,
        voice_config: dict,
    ) -> dict:
        """Merge LLM voice design with presets and user overrides."""
        presets = {
            "product_demo": {"voice_id": "Cherry", "speed": 1.0, "tone": "informative"},
            "brand_story": {"voice_id": "Ethan", "speed": 0.9, "tone": "narrative"},
            "promotion": {"voice_id": "Vivian", "speed": 1.1, "tone": "exciting"},
            "commercial": {"voice_id": "Cherry", "speed": 1.0, "tone": "confident"},
        }
        preset = dict(presets.get(video_type, presets["commercial"]))
        if voice_design.get("voice_id"):
            preset["voice_id"] = voice_design["voice_id"]
        if voice_design.get("speed"):
            preset["speed"] = float(voice_design["speed"])
        if voice_design.get("tone"):
            preset["tone"] = voice_design["tone"]
        if voice_config.get("voice_id") and voice_config["voice_id"] != "default":
            preset["voice_id"] = voice_config["voice_id"]
        if voice_config.get("speed"):
            preset["speed"] = float(voice_config["speed"])
        return preset

    def _fallback_prompt(
        self,
        shot_idx: int,
        image_content: str,
        style: str,
        video_type: str,
        duration: float,
    ) -> str:
        style_descriptors = {
            "commercial": "专业商业质感，构图干净，光线明亮",
            "lifestyle": "温暖生活美学，自然光，亲切氛围",
            "cinematic": "电影质感，戏剧光效，浅景深",
            "vlog": "真实手持感，自然运动，创作者视角",
            "documentary": "观察式纪录质感，真实光线，克制运镜",
        }
        motions = ["缓慢推镜", "平稳横移", "轻柔下拉", "跟拍运动", "固定中景", "缓缓拉远"]
        motion = motions[shot_idx % len(motions)]
        style_desc = style_descriptors.get(style, style_descriptors["commercial"])
        return (
            f"{motion}，{duration}秒。"
            f"画面主体：{image_content or '图片中的主体'}。"
            f"{style_desc}，运动平滑自然。"
        )
