"""TRL-compatible wrappers around the pure remix-plan verifier."""

from __future__ import annotations

from typing import Any

from .verifier import score_plan


def _completion_text(completion: Any) -> str:
    if isinstance(completion, str):
        return completion
    if isinstance(completion, list) and completion:
        last = completion[-1]
        if isinstance(last, dict):
            return str(last.get("content", ""))
    if isinstance(completion, dict):
        return str(completion.get("content", ""))
    return ""


def _score_batch(
    prompts: list[Any],
    completions: list[Any],
    candidate_pool: list[list[dict[str, Any]]],
    user_constraints: list[dict[str, Any]],
    abstract_slots: list[list[dict[str, Any]]],
    **kwargs: Any,
) -> list[tuple[float, dict[str, float]]]:
    lengths = {
        len(prompts),
        len(completions),
        len(candidate_pool),
        len(user_constraints),
        len(abstract_slots),
    }
    del kwargs
    if len(lengths) != 1:
        raise ValueError("reward inputs must have matching batch lengths")

    scores: list[tuple[float, dict[str, float]]] = []
    for completion, pool, constraints, slots in zip(
        completions,
        candidate_pool,
        user_constraints,
        abstract_slots,
        strict=True,
    ):
        scores.append(score_plan(_completion_text(completion), pool, slots, constraints))
    return scores


def remix_reward(
    prompts: list[Any],
    completions: list[Any],
    candidate_pool: list[list[dict[str, Any]]],
    user_constraints: list[dict[str, Any]],
    abstract_slots: list[list[dict[str, Any]]],
    **kwargs: Any,
) -> list[float]:
    """Return one schema-gated optimization reward per completion."""

    return [
        total
        for total, _ in _score_batch(
            prompts,
            completions,
            candidate_pool,
            user_constraints,
            abstract_slots,
            **kwargs,
        )
    ]


def schema_metric(
    prompts: list[Any],
    completions: list[Any],
    candidate_pool: list[list[dict[str, Any]]],
    user_constraints: list[dict[str, Any]],
    abstract_slots: list[list[dict[str, Any]]],
    **kwargs: Any,
) -> list[float]:
    """Zero-weight GRPO metric for schema-validity curves."""

    return [
        breakdown["schema"]
        for _, breakdown in _score_batch(
            prompts,
            completions,
            candidate_pool,
            user_constraints,
            abstract_slots,
            **kwargs,
        )
    ]


def first_pass_qa_metric(
    prompts: list[Any],
    completions: list[Any],
    candidate_pool: list[list[dict[str, Any]]],
    user_constraints: list[dict[str, Any]],
    abstract_slots: list[list[dict[str, Any]]],
    **kwargs: Any,
) -> list[float]:
    """Zero-weight GRPO metric matching eval/evaluate.py's QA gate."""

    values: list[float] = []
    for _, breakdown in _score_batch(
        prompts,
        completions,
        candidate_pool,
        user_constraints,
        abstract_slots,
        **kwargs,
    ):
        passed = (
            breakdown.get("schema") == 1.0
            and breakdown.get("grounding") == 1.0
            and breakdown.get("slot") == 1.0
            and breakdown.get("duration", 0.0) >= 0.8
            and breakdown.get("constraints") == 1.0
        )
        values.append(float(passed))
    return values
