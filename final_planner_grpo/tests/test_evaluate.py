"""Metric acceptance tests that do not require model inference."""

from __future__ import annotations

from eval.evaluate import compute_metrics
from tests.fixtures import (
    ABSTRACT_SLOTS,
    CANDIDATE_POOL,
    GOOD_PLAN,
    INVALID_JSON,
    USER_CONSTRAINTS,
)


def _row() -> dict:
    return {
        "candidate_pool": CANDIDATE_POOL,
        "abstract_slots": ABSTRACT_SLOTS,
        "user_constraints": USER_CONSTRAINTS,
    }


def test_metrics_are_computed_from_verifier_outputs() -> None:
    metrics, records = compute_metrics([GOOD_PLAN, INVALID_JSON], [_row(), _row()])
    assert metrics == {
        "n": 2,
        "schema_valid_rate": 0.5,
        "first_pass_qa_rate": 0.5,
    }
    assert records[0]["first_pass_qa"] is True
    assert records[1]["reward"] == 0.0
