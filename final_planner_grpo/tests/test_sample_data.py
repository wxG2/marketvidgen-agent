"""Integrity checks for the smoke/eval fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from data.build_dataset import _load_rows, _render_user_prompt
from rewards.verifier import score_plan

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_prompts.jsonl"


def test_sample_file_has_eight_train_and_nonempty_eval_rows() -> None:
    rows = [json.loads(line) for line in DATA_FILE.read_text().splitlines() if line.strip()]
    assert sum(row["split"] == "train" for row in rows) == 8
    assert sum(row["split"] == "eval" for row in rows) > 0


def test_every_reference_plan_passes_first_round_qa_gates() -> None:
    rows = [json.loads(line) for line in DATA_FILE.read_text().splitlines() if line.strip()]
    for row in rows:
        total, breakdown = score_plan(
            json.dumps(row["reference_plan"]),
            row["candidate_pool"],
            row["abstract_slots"],
            row["user_constraints"],
        )
        assert breakdown["schema"] == 1.0
        assert breakdown["grounding"] == 1.0
        assert breakdown["slot"] == 1.0
        assert breakdown["duration"] >= 0.8
        assert breakdown["constraints"] == 1.0
        assert total > 0.85


def test_rendered_prompts_mirror_production_final_plan_payload() -> None:
    for row in _load_rows():
        prompt = _render_user_prompt(row)
        assert '"profiles"' in prompt
        assert '"source_video_id"' in prompt
        assert '"source_shot_idx"' in prompt
        assert '"start_seconds"' in prompt
        assert '"end_seconds"' in prompt
        assert '"segments"' in prompt
