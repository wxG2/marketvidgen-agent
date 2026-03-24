from __future__ import annotations

import logging
import math
from pathlib import Path

from sqlalchemy import select

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.config import settings
from app.models.material import Material
from app.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


def _snap_to_supported(value: float, supported: list[int]) -> int:
    """Round a duration to the nearest model-supported value."""
    return min(supported, key=lambda s: abs(s - value))


def _allocate_shot_durations(target_total: int, num_shots: int, supported: list[int]) -> list[int]:
    """Allocate per-shot durations from *supported* values so the sum equals *target_total*.

    Strategy: start by assigning the supported duration closest to the even split,
    then greedily adjust individual shots to close the gap to the target.
    """
    if not supported or num_shots == 0:
        return []

    min_d, max_d = min(supported), max(supported)
    # Clamp target to feasible range
    clamped_total = max(num_shots * min_d, min(num_shots * max_d, target_total))

    even = clamped_total / num_shots
    durations = [_snap_to_supported(even, supported)] * num_shots
    current_sum = sum(durations)

    # Greedily adjust to match clamped_total
    sorted_sup = sorted(supported)
    for _ in range(num_shots * len(supported)):
        diff = clamped_total - current_sum
        if diff == 0:
            break
        if diff > 0:
            # Need more time — try to increase the first shot that can go up
            for i in range(num_shots):
                candidates = [s for s in sorted_sup if s > durations[i]]
                if candidates:
                    step = candidates[0] - durations[i]
                    if step <= diff:
                        current_sum += step
                        durations[i] = candidates[0]
                        break
            else:
                break
        else:
            # Need less time — try to decrease the first shot that can go down
            for i in range(num_shots):
                candidates = [s for s in sorted_sup if s < durations[i]]
                if candidates:
                    step = durations[i] - candidates[-1]
                    if step <= -diff:
                        current_sum -= step
                        durations[i] = candidates[-1]
                        break
            else:
                break

    return durations


class OrchestratorAgent(BaseAgent):
    """System entry point: parses user intent, resolves images, decomposes script into shots."""

    name = "orchestrator"

    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def execute(self, context: AgentContext, input_data: dict) -> AgentResult:
        if await context.is_cancelled():
            return AgentResult(success=False, output_data={}, error="Pipeline cancelled")

        script: str = input_data["script"]
        image_ids: list[str] = input_data["image_ids"]
        platform: str = input_data.get("platform", "generic")
        duration_seconds: int = input_data.get("duration_seconds", 30)
        duration_mode: str = input_data.get("duration_mode", "fixed")
        style: str = input_data.get("style", "commercial")
        voice_id: str = input_data.get("voice_id", "default")

        supported_durations: list[int] = settings.SEEDANCE_SUPPORTED_DURATIONS

        # Resolve image paths from DB
        image_paths = await self._resolve_images(context, image_ids)
        if not image_paths:
            return AgentResult(success=False, output_data={}, error="No valid images found for the given IDs")

        # Preprocess images for platform resolution
        target_res = settings.PLATFORM_RESOLUTIONS.get(platform)
        if target_res:
            from app.services.media_utils import preprocess_image_for_platform
            processed = []
            for p in image_paths:
                try:
                    processed.append(
                        preprocess_image_for_platform(p, target_res[0], target_res[1], settings.GENERATED_DIR)
                    )
                except Exception as exc:
                    logger.warning(f"Image preprocess failed for {p}: {exc}, using original")
                    processed.append(p)
            image_paths = processed

        num_shots = len(image_paths)
        min_d, max_d = min(supported_durations), max(supported_durations)

        # ── Feasibility check (fixed mode only) ──
        if duration_mode == "fixed":
            min_total = num_shots * min_d
            max_total = num_shots * max_d
            if duration_seconds < min_total:
                return AgentResult(
                    success=False, output_data={},
                    error=(
                        f"目标时长 {duration_seconds}s 不可行：{num_shots} 张素材至少需要 {min_total}s"
                        f"（每个镜头最短 {min_d}s）。"
                        f"建议：减少素材数量到 {duration_seconds // min_d} 张以内，"
                        f"或将目标时长增加到 {min_total}s 以上。"
                    ),
                )
            if duration_seconds > max_total:
                return AgentResult(
                    success=False, output_data={},
                    error=(
                        f"目标时长 {duration_seconds}s 不可行：{num_shots} 张素材最多支持 {max_total}s"
                        f"（每个镜头最长 {max_d}s）。"
                        f"建议：增加素材数量到至少 {math.ceil(duration_seconds / max_d)} 张，"
                        f"或将目标时长减少到 {max_total}s 以内。"
                    ),
                )

        # ── Script length vs duration check (fixed mode) ──
        if duration_mode == "fixed":
            # Estimate TTS duration: ~4 Chinese characters per second
            cn_chars = sum(1 for c in script if '\u4e00' <= c <= '\u9fff')
            other_chars = len(script) - cn_chars
            estimated_audio_s = cn_chars / 4.0 + other_chars / 8.0
            if estimated_audio_s > duration_seconds * 1.8:
                max_chars = int(duration_seconds * 4)
                return AgentResult(
                    success=False, output_data={},
                    error=(
                        f"脚本约 {len(script)} 字，预计口播需要约 {int(estimated_audio_s)}s，"
                        f"但目标视频仅 {duration_seconds}s，差距过大无法对齐。\n"
                        f"建议：缩短脚本到约 {max_chars} 字以内，"
                        f"或将视频时长增加到 {int(estimated_audio_s) + 1}s 以上。"
                    ),
                )

        # ── Determine per-shot durations ──
        # Decision order: user target duration → model supported durations → shot decomposition
        if duration_mode == "fixed":
            # Allocate supported durations that sum to user's target
            per_shot_durations = _allocate_shot_durations(duration_seconds, num_shots, supported_durations)
        else:
            # Auto mode: use default supported duration, LLM may adjust later
            per_shot_durations = [settings.SEEDANCE_DURATION] * num_shots

        schema = {
            "name": "orchestrator_plan",
            "schema": {
                "type": "object",
                "properties": {
                    "video_type": {"type": "string"},
                    "voice_speed": {"type": "number"},
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_idx": {"type": "integer"},
                                "script_segment": {"type": "string"},
                                "duration_seconds": {"type": "number"},
                            },
                            "required": ["shot_idx", "script_segment", "duration_seconds"],
                        },
                    },
                },
                "required": ["video_type", "voice_speed", "shots"],
            },
        }

        supported_str = "/".join(f"{d}s" for d in sorted(supported_durations))
        if duration_mode == "fixed":
            duration_instruction = (
                f"Total video duration MUST be exactly {duration_seconds}s. "
                f"Each shot duration MUST be one of: {supported_str}. "
                f"Pre-allocated durations: {per_shot_durations}. "
                f"You may rearrange which shots get which duration but the total must remain {duration_seconds}s."
            )
        else:
            duration_instruction = (
                f"You may freely decide each shot's duration. "
                f"Each shot duration MUST be one of: {supported_str}. "
                f"Choose durations that best fit the narrative rhythm."
            )

        prompt = (
            f"Script:\n{script}\n\n"
            f"Platform: {platform}\nStyle: {style}\n"
            f"Duration constraint: {duration_instruction}\n"
            f"Please split the story into exactly {num_shots} shots."
        )

        try:
            llm_output, usage = await self.llm.generate_structured(
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                user_prompt=prompt,
                schema=schema,
                image_paths=image_paths,
            )
            if await context.is_cancelled():
                return AgentResult(success=False, output_data={}, error="Pipeline cancelled")
            planned_shots = llm_output.get("shots", [])
            if len(planned_shots) != num_shots:
                raise ValueError("Model returned unexpected shot count")
            shots = []
            for i, (image_path, shot) in enumerate(zip(image_paths, planned_shots)):
                raw_dur = float(shot.get("duration_seconds", per_shot_durations[i]))
                if duration_mode == "fixed":
                    # Always clamp to pre-allocated durations to guarantee total
                    clamped_dur = per_shot_durations[i]
                else:
                    # Auto mode: snap LLM's choice to nearest supported value
                    clamped_dur = _snap_to_supported(raw_dur, supported_durations)
                shots.append({
                    "shot_idx": i,
                    "image_path": image_path,
                    "script_segment": shot["script_segment"],
                    "duration_seconds": clamped_dur,
                })
            video_type = llm_output.get("video_type", self._detect_video_type(script))
            voice_speed = float(llm_output.get("voice_speed", 1.0))
            usage_records = [{
                "provider": "qwen",
                "model_name": getattr(self.llm, "client", None).model if getattr(self.llm, "client", None) else "mock",
                "operation": "orchestrate",
                **usage,
            }]
        except Exception:
            video_type = self._detect_video_type(script)
            sentences = self._split_script(script, num_shots)
            shots = []
            for i, (image_path, sentence) in enumerate(zip(image_paths, sentences)):
                shots.append({
                    "shot_idx": i,
                    "image_path": image_path,
                    "script_segment": sentence,
                    "duration_seconds": per_shot_durations[i],
                })
            voice_speed = 1.0
            usage_records = []

        actual_total = sum(s["duration_seconds"] for s in shots)
        logger.info(
            f"Orchestrator: mode={duration_mode}, target={duration_seconds}s, "
            f"actual_total={actual_total}s, shots={[s['duration_seconds'] for s in shots]}"
        )

        output = {
            "shots": shots,
            "video_type": video_type,
            "platform": platform,
            "duration_seconds": int(actual_total),
            "style": style,
            "voice_config": {
                "voice_id": voice_id,
                "speed": voice_speed,
            },
            "script": script,
        }

        return AgentResult(success=True, output_data=output, usage_records=usage_records)

    async def _resolve_images(self, context: AgentContext, image_ids: list[str]) -> list[str]:
        async with context.db_session_factory() as session:
            result = await session.execute(
                select(Material).where(Material.id.in_(image_ids))
            )
            materials = result.scalars().all()
            # Preserve the order of image_ids (SQL IN doesn't guarantee order)
            id_to_material = {m.id: m for m in materials}
            root = Path(settings.MATERIALS_ROOT)
            paths = []
            for img_id in image_ids:
                m = id_to_material.get(img_id)
                if m and m.file_path:
                    paths.append(str((root / m.file_path).resolve()))
                elif m:
                    logger.warning(f"Material {img_id} has no file_path, skipped")
            return paths

    def _detect_video_type(self, script: str) -> str:
        if any(w in script for w in ["功能", "特点", "产品", "介绍", "参数"]):
            return "product_demo"
        if any(w in script for w in ["品牌", "故事", "理念", "创始"]):
            return "brand_story"
        if any(w in script for w in ["优惠", "折扣", "促销", "限时", "抢购"]):
            return "promotion"
        return "commercial"

    def _split_script(self, script: str, num_parts: int) -> list[str]:
        """Split script into roughly equal segments by sentence boundaries."""
        delimiters = ["。", "！", "？", "；", "\n"]
        sentences = []
        current = ""
        for char in script:
            current += char
            if char in delimiters and current.strip():
                sentences.append(current.strip())
                current = ""
        if current.strip():
            sentences.append(current.strip())

        if not sentences:
            sentences = [script]

        # Distribute sentences across parts
        if len(sentences) <= num_parts:
            # Pad with empty or duplicate last
            result = sentences[:]
            while len(result) < num_parts:
                result.append(sentences[-1])
            return result

        # Merge sentences into num_parts groups
        per_part = math.ceil(len(sentences) / num_parts)
        result = []
        for i in range(0, len(sentences), per_part):
            group = "".join(sentences[i:i + per_part])
            result.append(group)

        # Trim to num_parts
        while len(result) > num_parts:
            result[-2] += result[-1]
            result.pop()

        return result
