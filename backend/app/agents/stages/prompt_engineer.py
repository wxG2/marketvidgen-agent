from __future__ import annotations

import logging

from app.agents.core.base import AgentContext, AgentResult, BaseAgent, describe_exception
from app.agents.stages.llm_diagnostics import llm_failure_label, short_error
from app.agents.stages.prompt_engineer_utils import (
    as_str,
    duration_range_label,
    generation_duration_for,
    normalize_total_duration,
    rhythmic_durations,
    snap_to_half_second,
)
from app.core.config import settings
from app.prompts import PROMPT_ENGINEER_SYSTEM_PROMPT
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

_generation_duration_for = generation_duration_for
_snap_to_half_second = snap_to_half_second
_duration_range_label = duration_range_label
_rhythmic_durations = rhythmic_durations
_normalize_total_duration = normalize_total_duration
_as_str = as_str


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

        creative_brief: str = as_str(input_data.get("creative_brief") or input_data.get("user_request")).strip()
        explicit_script: str = as_str(input_data.get("explicit_script") or input_data.get("script")).strip()
        platform: str = as_str(input_data.get("platform") or "generic").strip()
        style: str = as_str(input_data.get("style") or "commercial").strip()
        video_type: str = as_str(input_data.get("video_type") or "commercial").strip()
        background_context: str = as_str(input_data.get("background_context")).strip()
        target_duration_raw = input_data.get("target_duration_seconds") or input_data.get("duration_seconds")
        try:
            target_duration: float | None = float(target_duration_raw) if target_duration_raw is not None else None
        except (TypeError, ValueError):
            target_duration = None
        duration_mode: str = as_str(input_data.get("duration_mode") or "fixed").strip()
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
                                "sequence_role": {"type": "string"},
                                "sequence_reason": {"type": "string"},
                                "shot_purpose": {"type": "string"},
                                "script_segment": {"type": "string"},
                                "duration_seconds": {"type": "number"},
                                "camera_movement": {"type": "string"},
                                "video_prompt": {"type": "string"},
                            },
                            "required": [
                                "shot_idx", "source_image_idx",
                                "sequence_role", "sequence_reason",
                                "script_segment", "duration_seconds", "video_prompt",
                            ],
                        },
                    },
                },
                "required": ["shots", "voice_design"],
            },
        }

        # ── User prompt ────────────────────────────────────────────────────
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
                content = as_str(img.get("image_content") or img.get("summary")).strip()
                role = as_str(img.get("visual_role") or img.get("marketing_angle")).strip()
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
            + "## 约束\n"
            + f"- 平台: {platform} | 风格: {style} | 视频类型: {video_type}\n"
            + "- 普通生成时，shot_idx 是最终成片时间线位置；source_image_idx 是素材库索引。\n"
            + "- 必须主动根据营销叙事重排素材，不要机械使用 source_image_idx=shot_idx。\n"
            + "- 每个镜头必须给出 sequence_role（hook/problem_or_scene/core_value/proof_or_detail/result_or_lifestyle/cta）和 sequence_reason。\n"
            + "- 每张素材至少使用一次；如需要强调核心卖点，可以重复使用最关键素材。\n"
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
            director_summary = as_str(llm_output.get("director_summary")).strip()
            creative_concept = as_str(llm_output.get("creative_concept")).strip()
            pacing_strategy = as_str(llm_output.get("pacing_strategy")).strip()
            narration_script = as_str(llm_output.get("narration_script")).strip()

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
            shot_prompts = normalize_total_duration(shot_prompts, target_duration)

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
                role = as_str(source_images[src_idx].get("visual_role")).strip()
            else:
                role = as_str(base.get("visual_role")).strip()
            visual_roles.append(role)

        fallback_dur: list[float] = []
        if target_duration and base_shots:
            fallback_dur = rhythmic_durations(visual_roles, target_duration)
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
                image_content = as_str(source_img.get("image_content") or source_img.get("summary"))
            else:
                image_path = as_str(base.get("image_path"))
                image_content = as_str(base.get("image_content"))
                source_img = base

            script_segment = as_str(
                llm_shot.get("script_segment") or base.get("script_segment")
            ).strip()

            # Duration: prefer LLM output, then existing shot, then rhythm fallback, then default
            llm_dur_raw = llm_shot.get("duration_seconds") or base.get("duration_seconds")
            if llm_dur_raw is not None:
                try:
                    duration_seconds = snap_to_half_second(float(llm_dur_raw))
                except (TypeError, ValueError):
                    duration_seconds = fallback_dur[i] if i < len(fallback_dur) else default_per_shot
            else:
                duration_seconds = fallback_dur[i] if i < len(fallback_dur) else default_per_shot

            generation_duration_seconds = generation_duration_for(duration_seconds)

            video_prompt = as_str(
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
                "sequence_role": as_str(
                    llm_shot.get("sequence_role") or self._fallback_sequence_role(i, len(base_shots))
                ).strip(),
                "sequence_reason": as_str(
                    llm_shot.get("sequence_reason") or llm_shot.get("shot_purpose")
                    or self._fallback_sequence_reason(i, len(base_shots), source_img)
                ).strip(),
                "image_path": image_path,
                "image_content": image_content,
                "source_image": source_img,
                "shot_purpose": as_str(llm_shot.get("shot_purpose")).strip(),
                "script_segment": script_segment,
                "duration_seconds": duration_seconds,
                "duration_range_label": duration_range_label(duration_seconds),
                "generation_duration_seconds": generation_duration_seconds,
                "camera_movement": as_str(llm_shot.get("camera_movement")).strip(),
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
                    duration_seconds = snap_to_half_second(
                        float(llm_shot.get("duration_seconds") or default_per_shot)
                    )
                except (TypeError, ValueError):
                    duration_seconds = default_per_shot
                generation_duration_seconds = generation_duration_for(duration_seconds)
                video_prompt = as_str(llm_shot.get("video_prompt")).strip() or self._fallback_prompt(
                    shot_idx=idx,
                    image_content=as_str(source_img.get("image_content")),
                    style=style,
                    video_type=video_type,
                    duration=duration_seconds,
                )
                shot_prompts.append({
                    "shot_idx": idx,
                    "source_image_idx": src_idx,
                    "sequence_role": as_str(
                        llm_shot.get("sequence_role") or self._fallback_sequence_role(len(shot_prompts), len(base_shots) + 1)
                    ).strip(),
                    "sequence_reason": as_str(
                        llm_shot.get("sequence_reason") or llm_shot.get("shot_purpose")
                        or self._fallback_sequence_reason(len(shot_prompts), len(base_shots) + 1, source_img)
                    ).strip(),
                    "image_path": source_img.get("image_path", ""),
                    "image_content": as_str(source_img.get("image_content")),
                    "source_image": source_img,
                    "shot_purpose": as_str(llm_shot.get("shot_purpose")).strip(),
                    "script_segment": as_str(llm_shot.get("script_segment")).strip(),
                    "duration_seconds": duration_seconds,
                    "duration_range_label": duration_range_label(duration_seconds),
                    "generation_duration_seconds": generation_duration_seconds,
                    "camera_movement": as_str(llm_shot.get("camera_movement")).strip(),
                    "video_prompt": video_prompt,
                })

        return shot_prompts

    @staticmethod
    def _fallback_sequence_role(position: int, total: int) -> str:
        if total <= 1:
            return "core_value"
        if position == 0:
            return "hook"
        if position == total - 1:
            return "cta"
        roles = ["problem_or_scene", "core_value", "proof_or_detail", "result_or_lifestyle"]
        return roles[(position - 1) % len(roles)]

    @staticmethod
    def _fallback_sequence_reason(position: int, total: int, source_img: dict) -> str:
        role = PromptEngineerAgent._fallback_sequence_role(position, total)
        content = as_str(source_img.get("image_content") or source_img.get("summary")).strip()
        if role == "hook":
            return "用视觉识别度最高的素材建立开场注意力。"
        if role == "cta":
            return "用可记忆的产品或品牌画面完成收束和转化引导。"
        if content:
            return f"根据素材内容衔接营销叙事：{content[:80]}"
        return "根据素材角色承接上一镜头并推进营销叙事。"

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
