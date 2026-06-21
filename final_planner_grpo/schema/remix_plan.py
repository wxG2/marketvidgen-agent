"""Pydantic contract aligned with marketvidgen-agent's RemixPlannerAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Transition = Literal["cut", "fade", "dissolve", "slideright", "slideup"]
SegmentRole = Literal["intro", "buildup", "highlight", "climax", "outro"]
AudioStrategy = Literal["source_audio", "bgm_only", "mix", "silent"]
BgmSource = Literal["uploaded", "generated", "library", "none"]


class SourceVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    video_id: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0.0)
    total_shots: int = Field(ge=1)
    analysis_summary: str = ""


class PlanSegment(BaseModel):
    """One existing ShotProfile selected by the planner."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    segment_idx: int = Field(ge=0)
    source_video_id: str = Field(min_length=1)
    source_shot_idx: int = Field(ge=0)
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    description: str = Field(min_length=1)
    emotion_tag: str = "neutral"
    voiceover: str | None = None
    role: SegmentRole
    quality_score: float = Field(ge=1.0, le=10.0)
    transition_to_next: Transition
    transition_duration: float = Field(ge=0.0, le=1.0)
    reference_keyframe_path: str = ""

    @model_validator(mode="after")
    def validate_segment(self) -> "PlanSegment":
        if self.end_seconds - self.start_seconds < 1.0:
            raise ValueError("segment duration must be at least 1.0 second")
        if self.transition_to_next == "cut" and self.transition_duration != 0.0:
            raise ValueError("cut transition_duration must be 0")
        if self.transition_to_next != "cut" and self.transition_duration <= 0.0:
            raise ValueError("non-cut transition_duration must be positive")
        return self


class AudioDesign(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    strategy: AudioStrategy
    bgm_source: BgmSource = "none"
    bgm_material_id: str | None = None
    bgm_path: str = ""
    bgm_filename: str = ""
    bgm_duration_seconds: float | None = Field(default=None, gt=0.0)
    bgm_mood: str = "none"
    bgm_volume: float = Field(default=0.25, ge=0.0, le=1.0)
    voice_id: str = "default"
    voice_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    voice_tone: str = ""
    narration_notes: str = ""


class RemixPlan(BaseModel):
    """Final normalized remix_plan consumed by RemixAssemblerAgent."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    title: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    target_duration_seconds: float | None = Field(default=None, gt=0.0)
    source_videos: list[SourceVideo] = Field(default_factory=list)
    segments: list[PlanSegment] = Field(min_length=1)
    audio_design: AudioDesign
    analysis_report: str = Field(min_length=1)
    requires_more_material: bool = False

    @model_validator(mode="after")
    def validate_plan_invariants(self) -> "RemixPlan":
        indices = sorted(segment.segment_idx for segment in self.segments)
        if indices != list(range(len(self.segments))):
            raise ValueError("segment_idx values must be unique and contiguous from 0")

        ordered = sorted(self.segments, key=lambda segment: segment.segment_idx)
        for previous, current in zip(ordered, ordered[1:]):
            if (
                previous.source_video_id == current.source_video_id
                and previous.source_shot_idx == current.source_shot_idx
            ):
                raise ValueError("adjacent segments cannot repeat the same source shot")
        terminal = ordered[-1]
        if terminal.transition_to_next != "cut" or terminal.transition_duration != 0.0:
            raise ValueError("the terminal segment must use cut with duration 0")
        return self


REMIX_PLAN_SCHEMA = RemixPlan.model_json_schema()
