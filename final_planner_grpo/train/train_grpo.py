"""Assemble and run LoRA GRPO training for the Final Planner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from peft import LoraConfig, TaskType  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402
from trl import GRPOTrainer  # noqa: E402

from data.build_dataset import build  # noqa: E402
from rewards.reward_fn import first_pass_qa_metric, remix_reward, schema_metric  # noqa: E402
from train.grpo_config import load_raw_config, make_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        required=True,
        help="Path to configs/smoke.yaml or configs/full.yaml",
    )
    return parser.parse_args()


def make_lora_config() -> LoraConfig:
    return LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )


def validate_prompt_lengths(dataset, tokenizer, max_prompt_length: int) -> None:
    """Enforce the plan's prompt budget outside GRPOConfig (removed in TRL 0.28)."""

    if max_prompt_length <= 0:
        raise ValueError("max_prompt_length must be positive")
    overlong: list[tuple[int, int]] = []
    for index in range(len(dataset)):
        token_ids = tokenizer.apply_chat_template(
            dataset[index]["prompt"],
            add_generation_prompt=True,
            tokenize=True,
        )
        if len(token_ids) > max_prompt_length:
            overlong.append((index, len(token_ids)))
    if overlong:
        raise ValueError(
            f"prompt token budget exceeded (max={max_prompt_length}): {overlong}. "
            "Shorten the dataset prompt; do not silently truncate scene constraints."
        )


def main() -> None:
    args = parse_args()
    raw = load_raw_config(args.config)
    training_args = make_config(args.config)

    train_dataset = build(
        str(raw.get("train_split", "train")),
        max_samples=raw.get("max_train_samples"),
    )
    eval_dataset = build(
        str(raw.get("eval_split", "eval")),
        max_samples=raw.get("max_eval_samples"),
    )

    model_name = str(raw["model_name_or_path"])
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    validate_prompt_lengths(
        train_dataset,
        tokenizer,
        int(raw.get("max_prompt_length", 1024)),
    )
    validate_prompt_lengths(
        eval_dataset,
        tokenizer,
        int(raw.get("max_prompt_length", 1024)),
    )

    trainer = GRPOTrainer(
        model=model_name,
        reward_funcs=[remix_reward, schema_metric, first_pass_qa_metric],
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        processing_class=tokenizer,
        peft_config=make_lora_config(),
    )
    trainer.train(resume_from_checkpoint=raw.get("resume_from_checkpoint"))
    trainer.save_model(training_args.output_dir)
    tokenizer.save_pretrained(training_args.output_dir)


if __name__ == "__main__":
    main()
