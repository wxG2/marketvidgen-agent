"""Build the base/SFT/GRPO ablation table from real evaluator outputs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--sft", required=True)
    parser.add_argument("--grpo", required=True)
    parser.add_argument("--output", default="eval/results.md")
    return parser.parse_args()


def _read_metrics(path: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    payload = json.loads(source.read_text(encoding="utf-8"))
    metrics = payload.get("metrics", {})
    required = {"n", "schema_valid_rate", "first_pass_qa_rate"}
    missing = required - metrics.keys()
    if missing:
        raise ValueError(f"{source} missing metrics: {sorted(missing)}")
    return metrics


def main() -> None:
    args = parse_args()
    rows = [
        ("base", _read_metrics(args.base)),
        ("SFT", _read_metrics(args.sft)),
        ("GRPO", _read_metrics(args.grpo)),
    ]
    lines = [
        "# Base → SFT → GRPO 消融结果",
        "",
        "> 下表仅由 `eval/evaluate.py` 的实际输出生成。",
        "",
        "| 模型 | Schema 合法率 | 首轮 QA 通过率 | n |",
        "|---|---:|---:|---:|",
    ]
    for label, metrics in rows:
        lines.append(
            f"| {label} | {metrics['schema_valid_rate']:.4f} | "
            f"{metrics['first_pass_qa_rate']:.4f} | {metrics['n']} |"
        )
    content = "\n".join(lines) + "\n"

    fixed = Path(args.output)
    if not fixed.is_absolute():
        fixed = PROJECT_ROOT / fixed
    fixed.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamped = fixed.with_name(f"{fixed.stem}_{timestamp}{fixed.suffix}")
    timestamped.write_text(content, encoding="utf-8")
    fixed.write_text(content, encoding="utf-8")
    print(f"timestamped_result={timestamped}")
    print(f"latest_result={fixed}")


if __name__ == "__main__":
    main()
