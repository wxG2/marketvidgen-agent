from __future__ import annotations

from app.core.config import settings

# Visual-role -> relative duration weight for rhythm-based fallback.
_ROLE_DURATION_WEIGHTS: dict[str, float] = {
    "brand_identity": 0.65,
    "hook": 0.65,
    "product_hero": 1.30,
    "lifestyle_scene": 1.20,
    "cta_moment": 0.80,
    "testimonial": 1.00,
    "social_proof": 0.90,
}
_DEFAULT_WEIGHT = 1.0


def generation_duration_for(duration_seconds: float) -> float:
    """Return the smallest model-supported generation duration >= duration_seconds."""
    supported = settings.SEEDANCE_SUPPORTED_DURATIONS
    candidates = [d for d in supported if d >= duration_seconds]
    if candidates:
        return float(min(candidates))
    return float(max(supported))


def snap_to_half_second(value: float, min_dur: float = 1.0) -> float:
    """Snap value to 0.5 s granularity and enforce min_dur."""
    snapped = round(float(value) * 2) / 2
    return max(min_dur, snapped)


def duration_range_label(duration_seconds: float) -> str:
    """Return a human-readable pacing bucket label for duration_seconds."""
    if duration_seconds < 2.0:
        return "1-2s"
    if duration_seconds < 6.0:
        return "2-6s"
    return "6-10s"


def rhythmic_durations(
    visual_roles: list[str],
    target: float,
    min_dur: float = 1.0,
) -> list[float]:
    """Distribute target seconds across shots proportionally by visual role."""
    n = len(visual_roles)
    if n == 0:
        return []
    weights = [_ROLE_DURATION_WEIGHTS.get(r, _DEFAULT_WEIGHT) for r in visual_roles]
    total_weight = sum(weights)
    raw = [target * w / total_weight for w in weights]
    clamped = [max(min_dur, r) for r in raw]
    scale = target / sum(clamped) if sum(clamped) > 0 else 1.0
    durations = [round(d * scale, 1) for d in clamped]
    diff = round(target - sum(durations), 1)
    if abs(diff) >= 0.05:
        longest = max(range(n), key=lambda i: durations[i])
        durations[longest] = round(durations[longest] + diff, 1)
    return durations


def normalize_total_duration(
    shot_prompts: list[dict],
    target_duration: float,
) -> list[dict]:
    """Adjust shot durations so their sum equals target_duration."""
    if not shot_prompts or target_duration <= 0:
        return shot_prompts

    total = sum(s.get("duration_seconds", 0.0) for s in shot_prompts)
    delta = round(target_duration - total, 2)
    if abs(delta) < 0.05:
        return shot_prompts

    result = [dict(s) for s in shot_prompts]
    last = result[-1]
    new_dur = snap_to_half_second(last["duration_seconds"] + delta)
    if new_dur >= 1.0:
        last["duration_seconds"] = new_dur
        last["generation_duration_seconds"] = generation_duration_for(new_dur)
        return result

    longest_idx = max(range(len(result)), key=lambda i: result[i].get("duration_seconds", 0.0))
    new_dur = snap_to_half_second(result[longest_idx]["duration_seconds"] + delta)
    if new_dur >= 1.0:
        result[longest_idx]["duration_seconds"] = new_dur
        result[longest_idx]["generation_duration_seconds"] = generation_duration_for(new_dur)
        return result

    scale = target_duration / total if total > 0 else 1.0
    for s in result:
        s["duration_seconds"] = snap_to_half_second(s["duration_seconds"] * scale)
        s["generation_duration_seconds"] = generation_duration_for(s["duration_seconds"])
    return result


def as_str(value: object) -> str:
    if value is None:
        return ""
    return str(value) if not isinstance(value, str) else value
