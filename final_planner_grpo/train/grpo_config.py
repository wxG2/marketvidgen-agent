"""Version-checked GRPOConfig factory for smoke and full profiles."""

from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml
from trl import GRPOConfig

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "configs"

CUSTOM_KEYS = {
    "model_name_or_path",
    "train_split",
    "eval_split",
    "max_train_samples",
    "max_eval_samples",
    "max_prompt_length",
    "resume_from_checkpoint",
}


def resolve_config_path(profile_or_path: str | Path) -> Path:
    value = Path(profile_or_path)
    if str(profile_or_path) in {"smoke", "full"}:
        value = CONFIG_DIR / f"{profile_or_path}.yaml"
    elif not value.is_absolute():
        cwd_candidate = Path.cwd() / value
        value = cwd_candidate if cwd_candidate.exists() else PROJECT_ROOT / value
    if not value.is_file():
        raise FileNotFoundError(f"config not found: {value}")
    return value.resolve()


def load_raw_config(profile_or_path: str | Path) -> dict[str, Any]:
    path = resolve_config_path(profile_or_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"config must contain a YAML mapping: {path}")
    if not raw.get("model_name_or_path"):
        raise ValueError("model_name_or_path is required")
    if not raw.get("output_dir"):
        raise ValueError("output_dir is required")
    return raw


def _validate_hard_constraints(raw: dict[str, Any]) -> None:
    if raw.get("remove_unused_columns") is not False:
        raise ValueError("remove_unused_columns must be false so reward metadata is forwarded")
    if raw.get("sync_ref_model") is not False:
        raise ValueError("sync_ref_model must be false when using PEFT/LoRA")

    batch_size = int(raw.get("per_device_train_batch_size", 1))
    generations = int(raw.get("num_generations", 1))
    accumulation = int(raw.get("gradient_accumulation_steps", 1))
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    if generations <= 0 or (batch_size * world_size * accumulation) % generations != 0:
        raise ValueError(
            "per_device_train_batch_size * WORLD_SIZE * gradient_accumulation_steps "
            "must be divisible by num_generations"
        )

    if raw.get("reward_weights") != [1.0, 0.0, 0.0]:
        raise ValueError(
            "reward_weights must be [1.0, 0.0, 0.0]: only remix_reward may optimize"
        )


def make_config(profile: str | Path) -> GRPOConfig:
    """Instantiate a TRL 0.28 GRPOConfig from a named profile or YAML path."""

    raw = load_raw_config(profile)
    _validate_hard_constraints(raw)

    valid_fields = {field.name for field in fields(GRPOConfig)}
    unknown = set(raw) - valid_fields - CUSTOM_KEYS
    if unknown:
        raise ValueError(
            "config keys are unsupported by installed GRPOConfig; "
            f"verify trl==0.28.0: {sorted(unknown)}"
        )

    trainer_values = {key: value for key, value in raw.items() if key in valid_fields}
    output_dir = Path(str(trainer_values["output_dir"]))
    if not output_dir.is_absolute():
        trainer_values["output_dir"] = str(PROJECT_ROOT / output_dir)
    return GRPOConfig(**trainer_values)
