from pathlib import Path

from app.agents.orchestrator import OrchestratorAgent
from app.config import settings


def _make_agent() -> OrchestratorAgent:
    return OrchestratorAgent(llm_service=None)


def test_replication_prompt_uses_background_as_primary_when_user_has_no_explicit_direction():
    agent = _make_agent()

    prompt = agent._build_replication_user_prompt(
        video_path="/tmp/reference.mp4",
        platform="douyin",
        style="commercial",
        script="复刻这个视频",
        background_context="品牌信息：高端护肤\n用户需求：突出抗老与专业感\n场景背景：轻奢美容院",
        adjustment_feedback="",
    )

    assert "必须将背景信息视为本次复刻执行方案的主要约束" in prompt
    assert "背景信息参考" in prompt
    assert "用户需求描述（可含脚本）" in prompt


def test_replication_prompt_prioritizes_explicit_script_over_background():
    agent = _make_agent()

    prompt = agent._build_replication_user_prompt(
        video_path="/tmp/reference.mp4",
        platform="douyin",
        style="commercial",
        script="保留原视频运镜节奏，但把内容改成口腔诊所种草，突出医生专业度和洁净环境。",
        background_context="品牌信息：高端护肤\n场景背景：轻奢美容院",
        adjustment_feedback="",
    )

    assert "优先满足用户明确提出的需求描述（含脚本要求）或调整反馈" in prompt
    assert "背景信息在不冲突时用于补充品牌、角色和场景细节" in prompt


def test_adjustment_feedback_counts_as_explicit_direction():
    agent = _make_agent()

    prompt = agent._build_replication_user_prompt(
        video_path="/tmp/reference.mp4",
        platform="douyin",
        style="commercial",
        script="",
        background_context="品牌信息：高端护肤",
        adjustment_feedback="把前两镜改成医生出镜讲解，不要再用门店空镜开场。",
    )

    assert "用户调整反馈" in prompt
    assert "优先满足用户明确提出的需求描述（含脚本要求）或调整反馈" in prompt


def test_generic_replication_phrases_are_not_treated_as_explicit_direction():
    agent = _make_agent()

    assert agent._has_explicit_replication_direction(script="参考这个视频", adjustment_feedback="") is False
    assert agent._has_explicit_replication_direction(script="按这个视频复刻", adjustment_feedback="") is False
    assert agent._has_explicit_replication_direction(script="跟这个一样", adjustment_feedback="") is False


def test_replication_skill_requires_explicit_recreation_intent():
    agent = _make_agent()

    assert agent._should_invoke_video_replication_skill(script="请复刻这个视频的节奏和镜头。") is True
    assert agent._should_invoke_video_replication_skill(script="我想做一个同款视频。") is True
    assert agent._should_invoke_video_replication_skill(script="先看一下这个视频。") is False
    assert agent._should_invoke_video_replication_skill(script="") is False


def test_adjustment_feedback_can_retrigger_replication_skill():
    agent = _make_agent()

    assert agent._should_invoke_video_replication_skill(
        script="先帮我看看这个视频",
        adjustment_feedback="把第三镜改成同款慢推镜头",
    ) is True


def test_select_requested_skill_only_returns_replication_when_intent_and_video_exist():
    agent = _make_agent()

    assert agent._select_requested_skill({
        "reference_video_id": "video-1",
        "script": "请复刻这个视频",
    }) == agent.video_replication_skill_name
    assert agent._select_requested_skill({
        "reference_video_id": "video-1",
        "script": "先帮我分析一下这个视频",
    }) is None
    assert agent._select_requested_skill({
        "script": "请复刻这个视频",
    }) is None


def test_replication_analysis_report_includes_key_sections():
    agent = _make_agent()

    report = agent._build_replication_analysis_report(
        replication_plan={
            "video_summary": "一条围绕门店体验展开的种草短视频。",
            "overall_style": "真实探店",
            "color_palette": "暖色自然光",
            "pacing": "前快后稳",
            "audio_design": {
                "voice_style": "女声讲解",
                "voice_speed": 1.05,
                "voice_tone": "轻松可信",
                "narration_notes": "前半段强调体验感，后半段强调转化。",
            },
            "music_design": {
                "bgm_mood": "轻快",
                "bgm_style": "生活方式",
                "volume_level": "中低",
                "music_notes": "避免盖过口播。",
            },
            "shots": [
                {
                    "shot_idx": 0,
                    "description": "门头开场后切入环境空镜。",
                    "visual_design": "先门头再室内环境",
                    "camera_movement": "推进",
                    "color_tone": "暖米色",
                    "subjects": ["门店", "前台"],
                    "timestamp_range": [0, 3.5],
                    "suggested_duration_seconds": 4,
                }
            ],
        },
        background_context="品牌信息：高端医美\n用户需求：突出专业感",
        extracted_frames=[
            {"frame_path": "/tmp/frame-1.jpg", "timestamp_seconds": 0.5, "frame_index": 0},
            {"frame_path": "/tmp/frame-2.jpg", "timestamp_seconds": 2.8, "frame_index": 1},
        ],
    )

    assert "内容概述" in report
    assert "风格与节奏" in report
    assert "关键帧数量：2" in report
    assert "背景信息约束" in report
    assert "音频设计" in report
    assert "音乐设计" in report
    assert "镜头 1：" in report


def test_sanitize_replication_plan_handles_list_payloads_without_crashing():
    agent = _make_agent()

    sanitized = agent._sanitize_replication_plan({
        "video_summary": "测试方案",
        "overall_style": "口播复刻",
        "audio_design": [],
        "music_design": ["bad"],
        "shots": [
            ["unexpected"],
            {
                "shot_idx": "2",
                "description": "医生讲解",
                "subjects": "医生",
                "timestamp_range": ["0", "3.5", "bad"],
                "suggested_duration_seconds": "4",
            },
        ],
    })

    assert sanitized["audio_design"] == {}
    assert sanitized["music_design"] == {}
    assert len(sanitized["shots"]) == 1
    assert sanitized["shots"][0]["shot_idx"] == 2
    assert sanitized["shots"][0]["subjects"] == ["医生"]
    assert sanitized["shots"][0]["timestamp_range"] == [0.0, 3.5]
    assert sanitized["shots"][0]["suggested_duration_seconds"] == 4


def test_replication_analysis_report_tolerates_malformed_llm_shapes():
    agent = _make_agent()

    report = agent._build_replication_analysis_report(
        replication_plan={
            "video_summary": "一条测试复刻方案",
            "audio_design": [],
            "music_design": ["bad"],
            "shots": [
                ["bad-shot"],
                {
                    "shot_idx": 0,
                    "description": "保留这一镜",
                    "camera_movement": "推进",
                    "subjects": ["人物"],
                },
            ],
        },
        background_context="",
        extracted_frames=[],
    )

    assert "一条测试复刻方案" in report
    assert "镜头 1：保留这一镜" in report


def test_assign_materials_to_shots_cycles_in_order(tmp_path, monkeypatch):
    agent = _make_agent()
    monkeypatch.setattr(settings, "MATERIALS_ROOT", str(tmp_path))

    shots = [
        {"shot_idx": 0, "description": "镜头一"},
        {"shot_idx": 1, "description": "镜头二"},
        {"shot_idx": 2, "description": "镜头三"},
    ]
    materials = [
        {
            "material_id": "mat-1",
            "file_path": "session/a.jpg",
            "filename": "a.jpg",
            "category": "product",
            "thumbnail_url": "/api/materials/mat-1/thumbnail",
        },
        {
            "material_id": "mat-2",
            "file_path": "session/b.jpg",
            "filename": "b.jpg",
            "category": "product",
            "thumbnail_url": "/api/materials/mat-2/thumbnail",
        },
    ]

    assigned = agent._assign_materials_to_shots(shots, materials)

    assert [shot["material_id"] for shot in assigned] == ["mat-1", "mat-2", "mat-1"]
    assert assigned[0]["material_filename"] == "a.jpg"
    assert assigned[1]["material_thumbnail_url"] == "/api/materials/mat-2/thumbnail"
    assert assigned[2]["material_image_path"] == str((Path(tmp_path) / "session/a.jpg").resolve())


def test_assign_materials_to_shots_uses_first_n_when_materials_exceed_shots(tmp_path, monkeypatch):
    agent = _make_agent()
    monkeypatch.setattr(settings, "MATERIALS_ROOT", str(tmp_path))

    shots = [{"shot_idx": 0}, {"shot_idx": 1}]
    materials = [
        {
            "material_id": "mat-1",
            "file_path": "session/a.jpg",
            "filename": "a.jpg",
            "category": "product",
            "thumbnail_url": "/api/materials/mat-1/thumbnail",
        },
        {
            "material_id": "mat-2",
            "file_path": "session/b.jpg",
            "filename": "b.jpg",
            "category": "product",
            "thumbnail_url": "/api/materials/mat-2/thumbnail",
        },
        {
            "material_id": "mat-3",
            "file_path": "session/c.jpg",
            "filename": "c.jpg",
            "category": "product",
            "thumbnail_url": "/api/materials/mat-3/thumbnail",
        },
    ]

    assigned = agent._assign_materials_to_shots(shots, materials)

    assert [shot["material_id"] for shot in assigned] == ["mat-1", "mat-2"]


def test_assign_materials_to_shots_preserves_shots_when_no_materials():
    agent = _make_agent()
    shots = [{"shot_idx": 0, "description": "镜头一"}]

    assigned = agent._assign_materials_to_shots(shots, [])

    assert assigned == shots
