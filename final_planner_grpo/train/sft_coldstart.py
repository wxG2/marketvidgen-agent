"""Optional LoRA SFT cold start using reference remix plans."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transformers import AutoTokenizer  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

from data.build_dataset import build  # noqa: E402
from train.train_grpo import make_lora_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model_name_or_path", default="Qwen/Qwen2-0.5B-Instruct")
    parser.add_argument("--output_dir", default="outputs/sft-coldstart")
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--batch_size", type=int, default=2)
    parser.add_argument("--gradient_accumulation_steps", type=int, default=2)
    parser.add_argument("--learning_rate", type=float, default=2e-5)
    parser.add_argument("--max_length", type=int, default=1536)
    return parser.parse_args()


def _add_assistant_reference(example: dict) -> dict:
    messages = list(example["prompt"])
    messages.append(
        {
            "role": "assistant",
            "content": json.dumps(example["reference_plan"], ensure_ascii=False),
        }
    )
    return {"messages": messages}


def main() -> None:
    args = parse_args()
    dataset = build("train").map(_add_assistant_reference)
    keep = {"messages"}
    dataset = dataset.remove_columns([name for name in dataset.column_names if name not in keep])

    tokenizer = AutoTokenizer.from_pretrained(args.model_name_or_path, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    requested = {
        "output_dir": str(output_dir),
        "num_train_epochs": args.epochs,
        "per_device_train_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "learning_rate": args.learning_rate,
        "max_length": args.max_length,
        "logging_steps": 1,
        "save_strategy": "epoch",
        "report_to": "none",
    }
    valid_fields = {field.name for field in fields(SFTConfig)}
    unsupported = set(requested) - valid_fields
    if unsupported:
        raise ValueError(
            f"installed SFTConfig does not support {sorted(unsupported)}; require trl==0.28.0"
        )
    sft_args = SFTConfig(**requested)

    trainer = SFTTrainer(
        model=args.model_name_or_path,
        args=sft_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=make_lora_config(),
    )
    trainer.train()
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))


if __name__ == "__main__":
    main()
