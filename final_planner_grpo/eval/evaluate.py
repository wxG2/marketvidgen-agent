"""Evaluate schema validity and first-pass QA success."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rewards.verifier import score_plan  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model_path", required=True, help="Base model ID or local adapter path")
    parser.add_argument("--split", choices=["train", "eval"], default="eval")
    parser.add_argument("--max_samples", type=int)
    parser.add_argument("--max_new_tokens", type=int, default=1024)
    parser.add_argument("--output_path", help="Fixed JSON output path; a timestamped copy is also kept")
    return parser.parse_args()


def _qa_pass(breakdown: dict[str, float]) -> bool:
    return (
        breakdown.get("schema") == 1.0
        and breakdown.get("grounding") == 1.0
        and breakdown.get("slot") == 1.0
        and breakdown.get("duration", 0.0) >= 0.8
        and breakdown.get("constraints") == 1.0
    )


def compute_metrics(
    completions: Sequence[str],
    rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compute metrics strictly against each dataset row's ground-truth constraints."""

    if len(completions) != len(rows):
        raise ValueError("completion count must match dataset row count")
    if not rows:
        raise ValueError("cannot evaluate an empty dataset")

    records: list[dict[str, Any]] = []
    for index, (completion, row) in enumerate(zip(completions, rows, strict=True)):
        reward, breakdown = score_plan(
            completion,
            row["candidate_pool"],
            row["abstract_slots"],
            row["user_constraints"],
        )
        records.append(
            {
                "index": index,
                "completion": completion,
                "schema_ok": breakdown.get("schema") == 1.0,
                "first_pass_qa": _qa_pass(breakdown),
                "reward": reward,
                "breakdown": breakdown,
            }
        )

    n = len(records)
    metrics = {
        "n": n,
        "schema_valid_rate": sum(record["schema_ok"] for record in records) / n,
        "first_pass_qa_rate": sum(record["first_pass_qa"] for record in records) / n,
    }
    return metrics, records


def _load_model_and_tokenizer(model_path: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    local_path = Path(model_path).expanduser()
    is_local_adapter = local_path.is_dir() and (local_path / "adapter_config.json").is_file()
    model_kwargs: dict[str, Any] = {"device_map": "auto"}
    if torch.cuda.is_available():
        model_kwargs["torch_dtype"] = torch.bfloat16

    if is_local_adapter:
        from peft import AutoPeftModelForCausalLM

        model = AutoPeftModelForCausalLM.from_pretrained(model_path, **model_kwargs)
    else:
        model = AutoModelForCausalLM.from_pretrained(model_path, **model_kwargs)

    tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    return model.eval(), tokenizer


def _generate(model: Any, tokenizer: Any, messages: list[dict[str, str]], max_new_tokens: int) -> str:
    import torch

    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    device = next(model.parameters()).device
    inputs = {name: tensor.to(device) for name, tensor in inputs.items()}
    input_length = inputs["input_ids"].shape[1]
    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    return tokenizer.decode(generated[0, input_length:], skip_special_tokens=True).strip()


def _default_output_path(model_path: str, split: str) -> Path:
    model_label = Path(model_path.rstrip("/")).name or "model"
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_label)
    return PROJECT_ROOT / "eval" / "results" / f"{safe_label}_{split}.json"


def write_versioned_json(payload: dict[str, Any], fixed_path: Path) -> tuple[Path, Path]:
    fixed_path = fixed_path.resolve()
    fixed_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamped = fixed_path.with_name(f"{fixed_path.stem}_{timestamp}{fixed_path.suffix}")
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    timestamped.write_text(content, encoding="utf-8")
    fixed_path.write_text(content, encoding="utf-8")
    return timestamped, fixed_path


def main() -> None:
    from data.build_dataset import build

    args = parse_args()
    dataset = build(args.split, max_samples=args.max_samples)
    rows = [dataset[index] for index in range(len(dataset))]
    model, tokenizer = _load_model_and_tokenizer(args.model_path)
    completions = [
        _generate(model, tokenizer, row["prompt"], args.max_new_tokens) for row in rows
    ]
    metrics, records = compute_metrics(completions, rows)

    output_path = (
        Path(args.output_path)
        if args.output_path
        else _default_output_path(args.model_path, args.split)
    )
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    timestamped, fixed = write_versioned_json(
        {
            "model_path": args.model_path,
            "split": args.split,
            "metrics": metrics,
            "samples": records,
        },
        output_path,
    )

    print("| metric | value | n |")
    print("|---|---:|---:|")
    print(f"| schema_valid_rate | {metrics['schema_valid_rate']:.4f} | {metrics['n']} |")
    print(f"| first_pass_qa_rate | {metrics['first_pass_qa_rate']:.4f} | {metrics['n']} |")
    print(f"timestamped_result={timestamped}")
    print(f"latest_result={fixed}")


if __name__ == "__main__":
    main()
