"""Pure rewards aligned with marketvidgen-agent ShotProfile inputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from schema.remix_plan import RemixPlan

WEIGHTS: dict[str, float] = {
    "grounding": 0.30,
    "slot": 0.20,
    "duration": 0.15,
    "order": 0.10,
    "diversity": 0.10,
    "constraints": 0.15,
}


def validate_schema(text: str) -> tuple[bool, RemixPlan | None]:
    if not isinstance(text, str):
        return False, None
    try:
        return True, RemixPlan.model_validate(json.loads(text.strip()))
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
        return False, None


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _shot_key(item: Mapping[str, Any]) -> tuple[str, int] | None:
    video_id = str(item.get("video_id", item.get("source_video_id", ""))).strip()
    value = item.get("shot_idx", item.get("source_shot_idx"))
    try:
        shot_idx = int(value)
    except (TypeError, ValueError):
        return None
    return (video_id, shot_idx) if video_id and shot_idx >= 0 else None


def _pool_by_shot(candidate_pool: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], Mapping[str, Any]]:
    pool: dict[tuple[str, int], Mapping[str, Any]] = {}
    for item in candidate_pool:
        if isinstance(item, Mapping) and (key := _shot_key(item)) is not None:
            pool[key] = item
    return pool


def _slot_index(slot: Any) -> int | None:
    value = _field(slot, "segment_idx", _field(slot, "expected_order"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def score_grounding(plan: RemixPlan, candidate_pool: Sequence[Mapping[str, Any]]) -> float:
    used = [(segment.source_video_id, segment.source_shot_idx) for segment in plan.segments]
    if not used:
        return 0.0
    valid = set(_pool_by_shot(candidate_pool))
    return len(set(used) & valid) / len(used)


def score_slot_coverage(plan: RemixPlan, abstract_slots: Sequence[Mapping[str, Any]]) -> float:
    required = {
        index
        for slot in abstract_slots
        if bool(_field(slot, "required", False))
        for index in [_slot_index(slot)]
        if index is not None
    }
    if not required:
        return 1.0
    filled = {segment.segment_idx for segment in plan.segments}
    return len(required & filled) / len(required)


def score_duration(
    plan: RemixPlan,
    candidate_pool: Sequence[Mapping[str, Any]],
    target: float,
    tol: float,
) -> float:
    target = float(target)
    tol = max(0.0, float(tol))
    if target <= 0.0 or not plan.segments:
        return 0.0

    total = sum(segment.end_seconds - segment.start_seconds for segment in plan.segments)
    lower = target * (1.0 - tol)
    upper = target * (1.0 + tol)
    if lower <= total <= upper:
        duration_fit = 1.0
    else:
        boundary = lower if total < lower else upper
        duration_fit = max(0.0, 1.0 - abs(total - boundary) / (0.5 * target))

    pool = _pool_by_shot(candidate_pool)
    legal = 0
    for segment in plan.segments:
        source = pool.get((segment.source_video_id, segment.source_shot_idx))
        if source is None:
            continue
        source_start = float(source.get("start_seconds", 0.0))
        source_end = float(source.get("end_seconds", source_start))
        if math.isclose(segment.start_seconds, source_start, abs_tol=1e-3) and math.isclose(
            segment.end_seconds, source_end, abs_tol=1e-3
        ):
            legal += 1
    return duration_fit * (legal / len(plan.segments))


def score_order(plan: RemixPlan, abstract_slots: Sequence[Mapping[str, Any]]) -> float:
    expected = [
        index
        for slot in sorted(abstract_slots, key=lambda item: int(_field(item, "expected_order", 0)))
        for index in [_slot_index(slot)]
        if index is not None
    ]
    if not expected:
        return 1.0
    actual = [segment.segment_idx for segment in plan.segments]
    first_position = {index: position for position, index in enumerate(actual)}
    present_ratio = sum(index in first_position for index in expected) / len(expected)
    expected_set = set(expected)
    recognized_ratio = sum(index in expected_set for index in actual) / len(actual)

    pairs = 0
    violations = 0
    for left_pos, left in enumerate(expected[:-1]):
        for right in expected[left_pos + 1 :]:
            pairs += 1
            if (
                left not in first_position
                or right not in first_position
                or first_position[left] >= first_position[right]
            ):
                violations += 1
    pair_score = 1.0 if pairs == 0 else 1.0 - violations / pairs
    return max(0.0, min(1.0, pair_score * present_ratio * recognized_ratio))


def score_diversity(plan: RemixPlan) -> float:
    """Reward contribution from multiple source videos."""

    used = [segment.source_video_id for segment in plan.segments]
    if not used:
        return 0.0
    return min(1.0, len(set(used)) / len(used))


def score_constraints(
    plan: RemixPlan,
    user_constraints: Mapping[str, Any],
    candidate_pool: Sequence[Mapping[str, Any]],
) -> float:
    pool = _pool_by_shot(candidate_pool)
    selected = [
        pool[key]
        for segment in plan.segments
        for key in [(segment.source_video_id, segment.source_shot_idx)]
        if key in pool
    ]
    checks: list[bool] = []

    required_scenes = user_constraints.get("must_include_scenes") or []
    if required_scenes:
        tags = {
            str(tag)
            for item in selected
            for tag in (item.get("scene_tags") or [item.get("emotion_tag")])
            if tag
        }
        checks.append(set(map(str, required_scenes)).issubset(tags))

    bgm_mood = user_constraints.get("bgm_mood", user_constraints.get("music_mood"))
    if bgm_mood:
        checks.append(plan.audio_design.bgm_mood == str(bgm_mood))

    if user_constraints.get("add_voiceover"):
        checks.append(all(bool(segment.voiceover) for segment in plan.segments))

    if user_constraints.get("include_source_audio") is True:
        checks.append(plan.audio_design.strategy in {"source_audio", "mix"})
    elif user_constraints.get("include_source_audio") is False:
        checks.append(plan.audio_design.strategy in {"bgm_only", "silent"})

    return sum(checks) / len(checks) if checks else 1.0


def score_plan(
    text: str,
    candidate_pool: Sequence[Mapping[str, Any]],
    abstract_slots: Sequence[Mapping[str, Any]],
    user_constraints: Mapping[str, Any],
) -> tuple[float, dict[str, float]]:
    schema_ok, plan = validate_schema(text)
    if not schema_ok or plan is None:
        return 0.0, {"schema": 0.0}

    slot_score = score_slot_coverage(plan, abstract_slots)
    target = user_constraints.get(
        "target_duration_seconds",
        user_constraints.get("target_duration", plan.target_duration_seconds or 0.0),
    )
    breakdown = {
        "grounding": score_grounding(plan, candidate_pool),
        "slot": slot_score,
        "duration": score_duration(
            plan,
            candidate_pool,
            float(target),
            float(user_constraints.get("tol", 0.0)),
        ),
        "order": score_order(plan, abstract_slots),
        "diversity": score_diversity(plan) if slot_score > 0.0 else 0.0,
        "constraints": score_constraints(plan, user_constraints, candidate_pool),
    }
    total = sum(WEIGHTS[name] * breakdown[name] for name in WEIGHTS)
    return max(0.0, min(1.0, total)), {"schema": 1.0, **breakdown}
