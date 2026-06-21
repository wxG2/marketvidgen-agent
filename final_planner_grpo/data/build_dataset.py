"""Build finite Hugging Face datasets for GRPO and evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from datasets import Dataset

DATA_FILE = Path(__file__).with_name("sample_prompts.jsonl")
REQUIRED_COLUMNS = {
    "prompt",
    "candidate_pool",
    "user_constraints",
    "abstract_slots",
}

SYSTEM_PROMPT = (
    "你是短视频混剪导演。你会收到多个源视频的 ShotProfile 列表，只能选择已有镜头。"
    "source_video_id/source_shot_idx/start_seconds/end_seconds 必须与输入完全一致。"
    "只输出严格 JSON，不要输出 Markdown、解释或代码围栏。"
)

PROMPT_OUTPUT_CONTRACT = {
    "title": "string",
    "concept": "string",
    "target_duration_seconds": 8.0,
    "source_videos": [
        {"video_id": "string", "duration_seconds": 8.0, "total_shots": 2, "analysis_summary": "string"}
    ],
    "segments": [
        {
            "segment_idx": 0,
            "source_video_id": "string",
            "source_shot_idx": 0,
            "start_seconds": 0.0,
            "end_seconds": 5.0,
            "description": "string",
            "emotion_tag": "string",
            "voiceover": "string|null",
            "role": "intro|buildup|highlight|climax|outro",
            "quality_score": 8.0,
            "transition_to_next": "cut|fade|dissolve|slideright|slideup",
            "transition_duration": 0.35,
            "reference_keyframe_path": "string",
        }
    ],
    "audio_design": {
        "strategy": "source_audio|bgm_only|mix|silent",
        "bgm_source": "uploaded|generated|library|none",
        "bgm_mood": "string",
        "bgm_volume": 0.25,
        "voice_id": "default",
        "voice_speed": 1.0,
        "voice_tone": "string",
        "narration_notes": "string",
    },
    "analysis_report": "string",
    "requires_more_material": False,
}


def _render_user_prompt(row: dict[str, Any]) -> str:
    profiles_by_video: dict[str, dict[str, Any]] = {}
    for candidate in row["candidate_pool"]:
        video_id = str(candidate["video_id"])
        profile = profiles_by_video.setdefault(
            video_id,
            {
                "video_id": video_id,
                "video_path": "",
                "duration_seconds": 0.0,
                "total_shots": 0,
                "shots": [],
            },
        )
        shot = {key: value for key, value in candidate.items() if key != "scene_tags"}
        profile["shots"].append(shot)
        profile["duration_seconds"] = max(profile["duration_seconds"], float(candidate["end_seconds"]))
        profile["total_shots"] = len(profile["shots"])
    constraints = row["user_constraints"]
    payload = {
        "profiles": list(profiles_by_video.values()),
        "preferences": {
            "script": row["user_request"],
            "duration_seconds": constraints["target_duration_seconds"],
            "remix_config": {
                "target_duration_seconds": constraints["target_duration_seconds"],
                "bgm_mood": constraints.get("bgm_mood") or "none",
                "add_voiceover": bool(constraints.get("add_voiceover")),
                "include_source_audio": bool(constraints.get("include_source_audio")),
            },
            "bgm_context": constraints.get("bgm_context")
            or {"bgm_source": "none", "bgm_material_id": None, "bgm_filename": "", "bgm_duration_seconds": None},
        },
    }
    return "\n".join(
        [
            "输入：" + json.dumps(payload, ensure_ascii=False),
            "输出 JSON Schema：" + json.dumps(PROMPT_OUTPUT_CONTRACT, ensure_ascii=False),
            "每个 segment 必须精确引用一个输入 ShotProfile；最后一段必须使用 cut 且 transition_duration=0。",
        ]
    )


def _load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with DATA_FILE.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = {
                "split",
                "user_request",
                "candidate_pool",
                "user_constraints",
                "abstract_slots",
                "reference_plan",
            } - row.keys()
            if missing:
                raise ValueError(f"{DATA_FILE}:{line_number} missing fields: {sorted(missing)}")
            rows.append(row)
    return rows


def build(split: Literal["train", "eval"], max_samples: int | None = None) -> "Dataset":
    """Return an in-memory Dataset; GRPOTrainer does not support IterableDataset."""

    from datasets import Dataset

    if split not in {"train", "eval"}:
        raise ValueError("split must be 'train' or 'eval'")
    if max_samples is not None and max_samples <= 0:
        raise ValueError("max_samples must be positive")

    selected: list[dict[str, Any]] = []
    for row in _load_rows():
        if row["split"] != split:
            continue
        selected.append(
            {
                "prompt": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _render_user_prompt(row)},
                ],
                "candidate_pool": row["candidate_pool"],
                "user_constraints": row["user_constraints"],
                "abstract_slots": row["abstract_slots"],
                "reference_plan": row["reference_plan"],
            }
        )

    if max_samples is not None:
        selected = selected[:max_samples]
    if not selected:
        raise ValueError(f"no samples found for split={split!r}")

    dataset = Dataset.from_list(selected)
    missing_columns = REQUIRED_COLUMNS - set(dataset.column_names)
    if missing_columns:
        raise RuntimeError(f"dataset is missing required columns: {sorted(missing_columns)}")
    return dataset


if __name__ == "__main__":
    for dataset_split in ("train", "eval"):
        dataset = build(dataset_split)
        print(f"{dataset_split}: n={len(dataset)}, columns={dataset.column_names}")
