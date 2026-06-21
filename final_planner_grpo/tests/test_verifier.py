"""Acceptance tests for the seven-dimensional verifier."""

from __future__ import annotations

import json

import pytest

from rewards.reward_fn import first_pass_qa_metric, remix_reward, schema_metric
from rewards.verifier import score_plan, validate_schema
from tests.fixtures import (
    ABSTRACT_SLOTS,
    CANDIDATE_POOL,
    GOOD_PLAN,
    GOOD_PLAN_DICT,
    HALLUCINATED_PLAN,
    INVALID_JSON,
    MISSING_REQUIRED_SLOT_PLAN,
    USER_CONSTRAINTS,
)


def test_good_plan_scores_above_acceptance_threshold() -> None:
    total, breakdown = score_plan(
        GOOD_PLAN, CANDIDATE_POOL, ABSTRACT_SLOTS, USER_CONSTRAINTS
    )
    assert total > 0.85
    assert breakdown == {
        "schema": 1.0,
        "grounding": 1.0,
        "slot": 1.0,
        "duration": 1.0,
        "order": 1.0,
        "diversity": 1.0,
        "constraints": 1.0,
    }


def test_hallucinated_scene_has_zero_grounding() -> None:
    _, breakdown = score_plan(
        HALLUCINATED_PLAN, CANDIDATE_POOL, ABSTRACT_SLOTS, USER_CONSTRAINTS
    )
    assert breakdown["schema"] == 1.0
    assert breakdown["grounding"] == 0.0


def test_invalid_json_triggers_hard_schema_gate() -> None:
    total, breakdown = score_plan(
        INVALID_JSON, CANDIDATE_POOL, ABSTRACT_SLOTS, USER_CONSTRAINTS
    )
    assert total == 0.0
    assert breakdown == {"schema": 0.0}


def test_missing_required_slot_reduces_coverage() -> None:
    _, breakdown = score_plan(
        MISSING_REQUIRED_SLOT_PLAN,
        CANDIDATE_POOL,
        ABSTRACT_SLOTS,
        USER_CONSTRAINTS,
    )
    assert breakdown["slot"] < 1.0


def test_reversed_slots_reduce_order_score() -> None:
    payload = {**GOOD_PLAN_DICT, "segments": list(reversed(GOOD_PLAN_DICT["segments"]))}
    _, breakdown = score_plan(
        json.dumps(payload), CANDIDATE_POOL, ABSTRACT_SLOTS, USER_CONSTRAINTS
    )
    assert breakdown["order"] < 1.0


def test_source_overrun_reduces_duration_score() -> None:
    payload = json.loads(GOOD_PLAN)
    payload["segments"][0]["end_seconds"] = 4.5
    _, breakdown = score_plan(
        json.dumps(payload), CANDIDATE_POOL, ABSTRACT_SLOTS, USER_CONSTRAINTS
    )
    assert breakdown["duration"] < 1.0


def test_reusing_one_source_video_reduces_diversity() -> None:
    payload = json.loads(GOOD_PLAN)
    payload["segments"][1]["source_video_id"] = "video-a"
    payload["segments"][1]["source_shot_idx"] = 1
    payload["segments"][1]["start_seconds"] = 4.0
    payload["segments"][1]["end_seconds"] = 8.0
    _, breakdown = score_plan(
        json.dumps(payload), CANDIDATE_POOL, ABSTRACT_SLOTS, USER_CONSTRAINTS
    )
    assert breakdown["slot"] == 1.0
    assert breakdown["diversity"] == pytest.approx(1 / 2)


def test_unmet_scene_reduces_constraint_score() -> None:
    constraints = {**USER_CONSTRAINTS, "must_include_scenes": ["not_present"]}
    _, breakdown = score_plan(GOOD_PLAN, CANDIDATE_POOL, ABSTRACT_SLOTS, constraints)
    assert breakdown["constraints"] < 1.0


def test_schema_rejects_non_cut_terminal_segment() -> None:
    payload = json.loads(GOOD_PLAN)
    payload["segments"][-1]["transition_to_next"] = "fade"
    payload["segments"][-1]["transition_duration"] = 0.35
    assert validate_schema(json.dumps(payload)) == (False, None)


def test_reward_wrapper_handles_standard_and_conversational_completions() -> None:
    standard = remix_reward(
        prompts=["prompt"],
        completions=[GOOD_PLAN],
        candidate_pool=[CANDIDATE_POOL],
        user_constraints=[USER_CONSTRAINTS],
        abstract_slots=[ABSTRACT_SLOTS],
    )
    conversational = remix_reward(
        prompts=[[{"role": "user", "content": "prompt"}]],
        completions=[[{"role": "assistant", "content": GOOD_PLAN}]],
        candidate_pool=[CANDIDATE_POOL],
        user_constraints=[USER_CONSTRAINTS],
        abstract_slots=[ABSTRACT_SLOTS],
    )
    assert standard == pytest.approx([1.0])
    assert conversational == pytest.approx([1.0])


def test_zero_weight_training_metrics_match_acceptance_gates() -> None:
    common = {
        "prompts": ["prompt", "prompt"],
        "completions": [GOOD_PLAN, INVALID_JSON],
        "candidate_pool": [CANDIDATE_POOL, CANDIDATE_POOL],
        "user_constraints": [USER_CONSTRAINTS, USER_CONSTRAINTS],
        "abstract_slots": [ABSTRACT_SLOTS, ABSTRACT_SLOTS],
    }
    assert schema_metric(**common) == [1.0, 0.0]
    assert first_pass_qa_metric(**common) == [1.0, 0.0]


def test_reward_wrapper_rejects_misaligned_batches() -> None:
    with pytest.raises(ValueError, match="matching batch lengths"):
        remix_reward(
            prompts=["one", "two"],
            completions=[GOOD_PLAN],
            candidate_pool=[CANDIDATE_POOL, CANDIDATE_POOL],
            user_constraints=[USER_CONSTRAINTS, USER_CONSTRAINTS],
            abstract_slots=[ABSTRACT_SLOTS, ABSTRACT_SLOTS],
        )
