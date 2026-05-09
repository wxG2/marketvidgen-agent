"""Tests for the video pipeline fixes:

1. generation_duration_seconds uses full supported range, not snapped to 5.
2. video_generator progress text shows both generation and final-cut durations.
3. video_editor works without LLM in no-audio/no-subtitle path.
4. director plan message is persisted after prompt_engineer completes.
"""
from __future__ import annotations

import json
import pytest


# ── 1. generation_duration_seconds uses full SEEDANCE range ──────────────────

def test_generation_duration_not_forced_to_five(monkeypatch):
    """generation_duration_for must return smallest-supported >= input, not nearest."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    monkeypatch.setattr(settings, "VIDEO_GEN_MIN_DURATION_SECONDS", 4.0)
    monkeypatch.setattr(settings, "VIDEO_GEN_MAX_DURATION_SECONDS", 15.0)

    from app.agents.stages.prompt_engineer import _generation_duration_for

    assert _generation_duration_for(2.5) == 4.0    # below min → smallest supported ≥ 2.5 = 4
    assert _generation_duration_for(7.3) == 8.0    # smallest ≥ 7.3 is 8 (not nearest 7)
    assert _generation_duration_for(10.8) == 11.0  # smallest ≥ 10.8 = 11
    assert _generation_duration_for(20.0) == 15.0  # above max → fallback to max


def test_full_supported_range_does_not_snap_all_to_five(monkeypatch):
    """Verify that with a full range config, different shot durations yield
    distinct generation_duration_seconds — previously all would collapse to 5."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    monkeypatch.setattr(settings, "VIDEO_GEN_MIN_DURATION_SECONDS", 4.0)
    monkeypatch.setattr(settings, "VIDEO_GEN_MAX_DURATION_SECONDS", 15.0)

    from app.agents.stages.prompt_engineer import _generation_duration_for

    durations = [2.5, 5.0, 7.5, 10.0, 13.0]
    gen_durations = [_generation_duration_for(d) for d in durations]
    # Expected: [4, 5, 8, 10, 13] — all distinct, not collapsed to 5
    assert len(set(gen_durations)) > 1, f"All collapsed to same value: {gen_durations}"
    # Each generation duration must cover (≥) the final-cut duration
    for dur, gen in zip(durations, gen_durations):
        assert gen >= dur, f"gen={gen} < dur={dur}"


# ── 2. video_generator progress text shows both durations ────────────────────

@pytest.mark.asyncio
async def test_video_generator_progress_shows_both_durations(monkeypatch):
    """Progress text must include both generation duration and final-cut duration."""
    from app.services.video_generator import GenerationStatus, GenerationTask, VideoGenerator
    from app.agents.stages.video_generator import VideoGeneratorAgent
    from app.core.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)

    class _MockGenerator(VideoGenerator):
        async def generate(self, image_path, prompt, duration=5, no_audio=True,
                           generation_model=None, platform="generic") -> GenerationTask:
            return GenerationTask(task_id="t1", status="processing")

        async def poll_status(self, task_id) -> GenerationStatus:
            return GenerationStatus(task_id=task_id, status="completed", video_url="http://example.com/v.mp4")

    progress_messages: list[str] = []

    class _FakeContext:
        pipeline_run_id = "r1"
        artifacts: dict = {}

        async def is_cancelled(self):
            return False

        async def report_progress(self, text, agent_name=None):
            progress_messages.append(text)

    shot = {
        "shot_idx": 0,
        "image_path": "/tmp/img.jpg",
        "video_prompt": "test",
        "duration_seconds": 2.5,          # final-cut: 2.5s
        "generation_duration_seconds": 4.0, # generation: 4s
    }

    agent = VideoGeneratorAgent(video_generator=_MockGenerator())
    ctx = _FakeContext()
    result = await agent._generate_single(ctx, shot)

    submit_msgs = [m for m in progress_messages if "提交" in m]
    assert submit_msgs, "Expected a submit progress message"
    msg = submit_msgs[0]
    assert "4" in msg, f"generation duration (4s) missing in: {msg}"
    assert "2.5" in msg, f"final-cut duration (2.5s) missing in: {msg}"


@pytest.mark.asyncio
async def test_video_generator_returns_generation_duration_seconds(monkeypatch):
    """Returned clip dict must include both duration_seconds and generation_duration_seconds."""
    from app.services.video_generator import GenerationStatus, GenerationTask, VideoGenerator
    from app.agents.stages.video_generator import VideoGeneratorAgent
    from app.core.config import settings
    monkeypatch.setattr(settings, "SEEDANCE_SUPPORTED_DURATIONS", [4, 5, 6, 7, 8, 9, 10])
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SHOTS", 2)
    monkeypatch.setattr(settings, "VIDEO_GENERATION_TIMEOUT_SECONDS", 10)

    class _MockGenerator(VideoGenerator):
        async def generate(self, image_path, prompt, duration=5, **kwargs) -> GenerationTask:
            return GenerationTask(task_id="t2", status="processing")

        async def poll_status(self, task_id) -> GenerationStatus:
            return GenerationStatus(task_id=task_id, status="completed", video_url="http://example.com/v.mp4")

    class _FakeContext:
        pipeline_run_id = "r2"
        artifacts: dict = {}

        async def is_cancelled(self):
            return False

        async def report_progress(self, *a, **kw):
            pass

    shot = {
        "shot_idx": 0,
        "image_path": "/tmp/img.jpg",
        "video_prompt": "test",
        "duration_seconds": 3.0,
        "generation_duration_seconds": 4.0,
    }

    agent = VideoGeneratorAgent(video_generator=_MockGenerator())
    result = await agent._generate_single(_FakeContext(), shot)

    assert "duration_seconds" in result
    assert "generation_duration_seconds" in result
    assert result["duration_seconds"] == 3.0        # final-cut preserved
    assert result["generation_duration_seconds"] == 4.0   # generation tracked


# ── 3. video_editor always follows director shot order ───────────────────────

@pytest.mark.asyncio
async def test_video_editor_always_uses_sequential_order(monkeypatch):
    """Video editor must follow director/generator shot order even with subtitles."""
    import os
    import tempfile
    from app.services.video_editing.composer import RealVideoEditorService

    class _ShouldNotBeCalled:
        async def generate_structured(self, **kwargs):
            raise AssertionError("video_editor must not call LLM for clip ordering")

    clip_process_commands = []

    # Fake ffmpeg / ffprobe that just copies the file and reports duration
    async def _fake_run(*args, **kwargs):
        cmd = list(args)
        # copy input to output (ffmpeg -i src ... dst)
        if "-i" in cmd:
            in_idx = cmd.index("-i") + 1
            out = cmd[-1]
            if os.path.basename(out).startswith("clip_") and out.endswith(".mp4"):
                clip_process_commands.append(cmd)
            if os.path.exists(cmd[in_idx]) and out.endswith(".mp4"):
                with open(out, "wb") as f:
                    f.write(b"\x00" * 512)
        return 0, "5.0", ""

    monkeypatch.setattr("app.services.video_editing.composer.run_subprocess", _fake_run, raising=False)

    # Create fake clip files
    tmpdir = tempfile.mkdtemp()
    clips = []
    for i in range(3):
        p = os.path.join(tmpdir, f"clip_{i}.mp4")
        with open(p, "wb") as f:
            f.write(b"\x00" * 512)
        clips.append(p)

    output_path = os.path.join(tmpdir, "out.mp4")
    subtitle_path = os.path.join(tmpdir, "subtitles.srt")
    with open(subtitle_path, "w", encoding="utf-8") as f:
        f.write(
            "1\n00:00:00,000 --> 00:00:02,500\nfirst\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\nsecond\n\n"
            "3\n00:00:05,000 --> 00:00:07,500\nthird\n"
        )

    svc = RealVideoEditorService(llm_service=_ShouldNotBeCalled(), ffmpeg_bin="ffmpeg")

    # Patch media_utils import inside service
    async def _noop_ensure(path, workdir=None):
        return path

    monkeypatch.setattr("app.services.video_editing.composer.ensure_local_file", _noop_ensure, raising=False)
    monkeypatch.setattr("app.services.media_utils.run_subprocess", _fake_run, raising=False)

    context_data = {
        "video_clips_data": [
            {"shot_idx": i, "duration_seconds": 2.5} for i in range(3)
        ],
        "shot_prompts": [{"video_prompt": f"p{i}", "script_segment": f"s{i}"} for i in range(3)],
        "duration_mode": "auto",
        "shot_durations": [2.5, 2.5, 2.5],
        "transition": "none",
        "bgm_mood": "none",
    }

    result = await svc.compose(
        video_clips=clips,
        audio_path="",
        subtitle_path=subtitle_path,
        output_path=output_path,
        context_data=context_data,
    )

    assert result.output_path == output_path
    assert len(clip_process_commands) == 3
    assert [
        os.path.basename(cmd[cmd.index("-i") + 1])
        for cmd in clip_process_commands
    ] == ["clip_0.mp4", "clip_1.mp4", "clip_2.mp4"]
    assert all("-t" in cmd and cmd[cmd.index("-t") + 1] == "2.50" for cmd in clip_process_commands)


# ── 4. langgraph retry continues after failed audio without regenerating video ─

@pytest.mark.asyncio
async def test_langgraph_audio_retry_reuses_existing_video_clips(monkeypatch):
    """retry-agent on audio_subtitle should reuse completed video_generator output."""
    from app.agents.core.base import AgentResult
    from app.agents.executors.langgraph.nodes import LangGraphPipelineNodeMixin
    from app.core.config import settings

    monkeypatch.setattr(settings, "QA_REVIEW_ENABLED", False)

    class _Context:
        trace_id = "trace-test"
        pipeline_run_id = "run-test"

        def __init__(self):
            self.artifacts = {
                "audio": {"audio_path": "retried.mp3"},
                "video_clips": {"video_clips": ["already-generated.mp4"]},
            }
            self.saved = 0

        async def save_checkpoint(self):
            self.saved += 1

    class _Agent:
        def __init__(self, output):
            self.output = output
            self.calls = 0

        async def run(self, context, input_data):
            self.calls += 1
            return AgentResult(success=True, output_data=self.output)

    class _Executor(LangGraphPipelineNodeMixin):
        def __init__(self):
            self.audio_agent = _Agent({"audio_path": "new.mp3"})
            self.video_gen_agent = _Agent({"video_clips": ["new.mp4"]})
            self.video_editor = _Agent({"final_video_path": "final.mp4"})
            self.qa_reviewer = None

        def get_agent_map(self):
            return {
                "audio_subtitle": self.audio_agent,
                "video_generator": self.video_gen_agent,
                "video_editor": self.video_editor,
            }

        @staticmethod
        def get_agent_to_artifact_key():
            return {
                "audio_subtitle": "audio",
                "video_generator": "video_clips",
                "video_editor": "final_video",
            }

        def build_agent_input(self, agent_name, artifacts, input_config):
            return {"agent_name": agent_name, "artifacts": artifacts, **input_config}

        async def _update_run(self, *args, **kwargs):
            return None

    context = _Context()
    executor = _Executor()

    result = await executor.continue_from_retry(context, "audio_subtitle", {})

    assert executor.video_gen_agent.calls == 0
    assert executor.video_editor.calls == 1
    assert result == {"final_video_path": "final.mp4"}
    assert context.artifacts["video_clips"] == {"video_clips": ["already-generated.mp4"]}


# ── 5. director plan message persisted after prompt_engineer completes ────────

@pytest.mark.asyncio
async def test_director_plan_message_persisted_after_prompt_engineer(tmp_path):
    """persist_director_plan_message should create an AutoChatMessage with
    role='assistant', title='director_plan', and directorPlan in payload."""
    import json as _json
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.director_plan_chat import persist_director_plan_message

    # Build fake DB objects
    run_id = "run-test-001"
    session_id = "sess-test-001"

    fake_run = MagicMock()
    fake_run.session_id = session_id

    fake_session = MagicMock()
    fake_session.last_activity_at = None

    saved_messages: list[dict] = []

    class _FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, model, pk):
            from app.models.pipeline import PipelineRun
            from app.models.auto_chat import AutoChatSession
            if model.__name__ == "PipelineRun":
                return fake_run
            if model.__name__ == "AutoChatSession":
                return fake_session
            return None

        async def execute(self, stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            return result

        def add(self, obj):
            saved_messages.append(obj)

        async def delete(self, obj):
            pass

        async def commit(self):
            pass

    def _factory():
        return _FakeDB()

    prompt_plan = {
        "shot_prompts": [
            {"shot_idx": 0, "duration_seconds": 3.0, "script_segment": "hook", "video_prompt": "p0"},
            {"shot_idx": 1, "duration_seconds": 7.0, "script_segment": "product", "video_prompt": "p1"},
        ],
        "voice_design": {"voice_id": "Cherry", "speed": 1.0, "tone": "warm"},
        "director_summary": "Test director summary",
    }

    await persist_director_plan_message(_factory, run_id, prompt_plan)

    assert len(saved_messages) == 1
    msg = saved_messages[0]
    assert msg.role == "assistant"
    assert msg.title == "director_plan"
    assert msg.session_id == session_id

    payload = _json.loads(msg.payload_json)
    plan = payload["directorPlan"]
    assert plan["run_id"] == run_id
    assert len(plan["shot_prompts"]) == 2
    assert plan["voice_design"]["voice_id"] == "Cherry"
    assert plan["director_summary"] == "Test director summary"


@pytest.mark.asyncio
async def test_director_plan_message_deduplicates_by_run_id():
    """Re-persisting for the same run_id should delete the old message first."""
    import json as _json
    from unittest.mock import MagicMock
    from app.services.director_plan_chat import persist_director_plan_message

    run_id = "run-dup-test"
    session_id = "sess-dup-test"

    fake_run = MagicMock()
    fake_run.session_id = session_id

    fake_session = MagicMock()
    fake_session.last_activity_at = None

    deleted: list = []
    added: list = []

    # Simulate an existing message for the same run_id
    old_msg = MagicMock()
    old_msg.payload_json = _json.dumps({"directorPlan": {"run_id": run_id}})

    class _FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, model, pk):
            from app.models.pipeline import PipelineRun
            from app.models.auto_chat import AutoChatSession
            if model.__name__ == "PipelineRun":
                return fake_run
            if model.__name__ == "AutoChatSession":
                return fake_session
            return None

        async def execute(self, stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = [old_msg]
            return result

        def add(self, obj):
            added.append(obj)

        async def delete(self, obj):
            deleted.append(obj)

        async def commit(self):
            pass

    await persist_director_plan_message(_FakeDB, run_id, {"shot_prompts": [], "voice_design": {}})

    assert old_msg in deleted, "Old message was not deleted before re-insert"
    assert len(added) == 1, "New message was not added"
