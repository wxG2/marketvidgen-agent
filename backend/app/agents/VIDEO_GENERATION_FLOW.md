# Video Generation Flow

本文档说明当前自动模式下，一条视频生成请求如何从用户消息进入 pipeline，以及各阶段 Agent 的输入、输出和 artifact 交接关系。

## 总览

当前主要链路是：

```text
Frontend AutoModeStudio
  -> POST /api/projects/{project_id}/auto-sessions/{session_id}/chat
  -> OrchestratorAgent.chat_stream
  -> generate_video / remix_video / replicate_video / analyze_video runtime skill
  -> PipelineRun.input_config
  -> LangGraphPipelineExecutor
  -> Stage Agents
  -> final_video
```

普通生成链路：

```text
START
  -> orchestrator
  -> prompt_engineer
  -> audio_subtitle + video_generator
  -> video_editor
  -> qa_reviewer 可选
  -> END
```

复刻链路：

```text
START
  -> replication_planner
  -> 等待人工确认
  -> prompt_engineer
  -> audio_subtitle + video_generator
  -> video_editor
  -> qa_reviewer 可选
  -> END
```

## 用户消息到 input_config

前端不会直接构造 pipeline 的 `input_config`。前端只把用户消息、可选附件展示信息和当前视频模型发送给后端：

- `content`: 用户输入文本
- `payload.images/files`: 消息展示用附件信息
- `generation_model`: 当前选择的视频生成模型

后端 `OrchestratorAgent.chat_stream(...)` 会根据消息和会话上下文判断是否调用 runtime skill。命中 `generate_video` 后，会把两类数据合并成 skill 参数：

- 从用户最新消息或 LLM 参数提取得到的 `tool_args`，例如 `user_request`、`narration_script`、`platform`、`duration_mode`、`style`
- 从 `session_context` 得到的默认参数，例如 `project_id`、`session_id`、`user_id`、`image_ids`、`draft_script`、`video_model_no_audio`、`video_transition`、`bgm_mood`

`generate-video/runtime.py` 最终把 skill 参数整理成 `input_config`：

```python
{
    "script": normalized_narration,
    "user_request": normalized_request,
    "image_ids": normalized_image_ids,
    "session_id": session_id,
    "background_template_id": background_template_id,
    "platform": platform,
    "duration_seconds": effective_duration,
    "duration_mode": duration_mode,
    "no_audio": video_model_no_audio,              # 兼容旧字段
    "video_model_no_audio": video_model_no_audio,  # 控制视频模型自带声音
    "voiceover_no_audio": voiceover_no_audio,      # 控制 VidGen TTS/字幕
    "generation_model": generation_model,
    "style": style,
    "voice_id": voice_id,
    "transition": transition,
    "transition_duration": transition_duration,
    "bgm_mood": bgm_mood,
    "bgm_volume": bgm_volume,
    "watermark_image_id": watermark_image_id,
    "watermark_path": watermark_path,              # 可选
    "background_template_name": template_name,     # 可选
    "background_context": background_context,      # 可选
}
```

其中：

- `user_request` 表示创作目标或生成要求，不应直接当口播念出来。
- `script` 表示已经确认可直接播报的旁白脚本。
- 旧字段 `script` 会在 runtime 中再次判断：如果像“帮我生成视频”这类指令，会转入 `user_request`；否则转入 `script`。
- `image_ids` 来自当前自动会话已选素材，不直接来自聊天消息 body。

## 调度与状态传递

`LangGraphPipelineExecutor.run(...)` 会创建 `AgentContext`，并把初始 state 交给 LangGraph：

```python
{
    "context": context,
    "input_config": input_config,
    "qa_retry_count": 0,
}
```

每个节点执行成功后，会把结果写入两处：

- `context.artifacts[...]`: 运行时共享 artifact，用于后续节点构造输入
- LangGraph state 返回值: 让图状态也包含该阶段结果

每个节点结束后还会调用 `context.save_checkpoint()`，把当前 artifacts 快照存入 `PipelineRun.artifacts_snapshot`。

此外，`BaseAgent.run(...)` 会在 `prompt_engineer`、`audio_subtitle`、`video_generator` 成功后，把用户可看的中间产物自动写入 `RepositoryAsset`：

- `prompt_engineer`: 提示词方案、shot 级视频提示词、配音参数
- `audio_subtitle`: 音频文件、字幕文件，或跳过音频的状态说明
- `video_generator`: 分镜视频清单、每个 shot 的生成视频片段

前端通过 `/api/projects/{project_id}/pipeline/{run_id}/artifacts` 在自动模式右侧栏展示这些产物，个人仓库通过 `/api/repository/assets` 聚合展示当前账号的历史 Agent 产物。

当前视频生成优先模式下，`generate_video` runtime skill 会把原始用户消息写入 `user_request`。普通生成链路会直接进入 `OrchestratorAgent`，由它内部完成需求解析和素材上下文整理；复刻链路因为带 `reference_video_id`，会直接路由到 `ReplicationPlannerAgent`。

`OrchestratorAgent` 是普通视频生成链路的用户意图调度核心。它内部按状态机执行，每次状态迁移都会通过 `AgentContext.report_progress(...)` 追加到当前 `AgentExecution.progress_text`，`/api/projects/{project_id}/pipeline/{run_id}/stream` 会把这些状态随 agent 执行记录推送给前端。当前状态包括：

```text
intake
  -> parse_requirements
  -> resolve_images
  -> preprocess_images
  -> analyze_images
  -> finalize
```

在普通生成链路中，用户输入由 Orchestrator runtime skill 进入 pipeline，后续视频生产阶段不再直接向用户提问；Orchestrator 的 pipeline 阶段负责把用户消息和图片解析为导演输入上下文，再交给内部的 PromptEngineer、AudioSubtitle、VideoGenerator 和 VideoEditor。

## Agent 输入输出

### Orchestrator chat entry

文件：`backend/app/agents/stages/orchestrator.py` 与 `backend/app/agents/stages/orchestrator_chat.py`

职责：判断用户消息是普通对话，还是需要调用 runtime skill；如果调用视频生成类 skill，则补齐会话上下文并执行对应 skill。

输入：

```python
messages: list[dict[str, str]]
session_context: dict
```

关键 `session_context` 字段：

```python
{
    "project_id": str,
    "session_id": str,
    "user_id": str,
    "reference_video_id": str | None,
    "reference_video_ids": list[str],
    "background_template_id": str | None,
    "draft_script": str | None,
    "platform": str,
    "video_no_audio": bool,
    "video_model_no_audio": bool,
    "duration_mode": str,
    "video_transition": str,
    "bgm_mood": str,
    "watermark_id": str | None,
    "selected_materials": list[dict],
    "generation_model": str | None,
}
```

输出：

- 普通聊天：流式 `text/status/done` 事件
- 工具调用：`tool_call/tool_result/status/done` 事件
- 对视频生成：调用 `generate_video(...)`、`remix_video(...)` 或 `replicate_video(...)`，由 runtime skill 创建 `PipelineRun`

### generate_video Skill

文件：`backend/app/agents/skills/generate-video/runtime.py`

职责：把 Orchestrator 会话入口补齐的生成参数转成 pipeline `input_config`，创建 `PipelineRun`，并后台启动 executor。

输入：

```python
{
    "project_id": str,
    "session_id": str,
    "user_id": str,
    "user_request": str,
    "narration_script": str,
    "script": str,
    "image_ids": list[str],
    "platform": str,
    "duration_mode": "fixed" | "auto",
    "style": str,
    "no_audio": bool,                  # 兼容旧字段
    "video_model_no_audio": bool,
    "voiceover_no_audio": bool,
    "generation_model": str | None,
    "transition": str,
    "bgm_mood": str,
    "voice_id": str,
    "watermark_image_id": str | None,
    "background_template_id": str | None,
    "duration_seconds": int | None,
}
```

输出：

```python
{
    "run_id": str,
    "status": "started",
    "run": dict,
}
```

副作用：

- 创建 `PipelineRun(input_config=...)`
- 更新 `AutoChatSession.current_run_id`
- 回写 `AutoChatSession.draft_script`
- `asyncio.create_task(...)` 后台运行 pipeline

### replicate_video Skill

文件：`backend/app/agents/skills/replicate-video/runtime.py`

职责：根据当前参考视频创建复刻型 pipeline run。它会设置 `reference_video_id`，使 LangGraph 首节点进入 `replication_planner`。

输入：

```python
{
    "project_id": str,
    "session_id": str,
    "user_id": str,
    "reference_video_id": str,
    "direction": str,
    "platform": str,
    "style": str,
    "generation_model": str | None,
    "background_template_id": str | None,
}
```

输出：

```python
{
    "run_id": str,
    "status": "started",
    "run": dict,
}
```

生成的 `input_config` 会包含：

```python
{
    "script": "请复刻这个参考视频。...",
    "image_ids": [],
    "reference_video_id": reference_video_id,
    "platform": platform,
    "duration_seconds": 30,
    "duration_mode": "fixed",
    "generation_model": generation_model,
    "style": style,
    ...
}
```

### remix_video Skill

文件：`backend/app/agents/skills/remix-video/runtime.py`

职责：根据当前会话 2 个及以上参考视频创建混剪型 pipeline run。它会设置 `reference_video_ids`，使 LangGraph 首节点进入 `remix_planner`，后续等待 `confirm-remix`；确认后若启用旁白，会先生成 / 复用 TTS 音频并按真实音频时长调整混剪时间线，必要时重跑规划并再次等待确认，最后再由 `remix_assembler` 抽片拼接。

输入：

```python
{
    "project_id": str,
    "session_id": str,
    "user_id": str,
    "reference_video_ids": list[str],
    "direction": str,
    "platform": str,
    "style": str,
    "generation_model": str | None,
    "background_template_id": str | None,
}
```

输出：

```python
{
    "run_id": str,
    "status": "started",
    "message": "已启动多视频混剪流程，完成规划后会暂停等待确认。",
    "run": dict,
}
```

## Stage Agents

### OrchestratorAgent

文件：`backend/app/agents/stages/orchestrator.py`

职责：普通生成首节点，也是 Intake / Context Agent。它读取用户消息、会话参数和已选图片，先解析真实创作意图、脚本、平台、风格、目标时长、BGM、语音、是否无声和视频模型，再理解每张图片的内容与营销角色，输出“导演输入上下文”给 PromptEngineer。

时长职责：Orchestrator 不再做最终镜头方案或短视频总时长分配，只把用户目标时长继续传给 PromptEngineer。

输入来源：

- LangGraph 节点传入 `build_agent_input("orchestrator", ...)`
- 对 orchestrator 来说，输入就是原始 `input_config`

输入：

```python
{
    "script": str,
    "user_request": str,
    "image_ids": list[str],
    "platform": str,
    "duration_seconds": int,
    "duration_mode": "fixed" | "auto",
    "style": str,
    "voice_id": str,
    "background_context": str,
}
```

输出 artifact key：`orchestrator_plan`

输出：

```python
{
    "creative_brief": str,
    "explicit_script": str,
    "video_type": str,
    "platform": str,
    "duration_seconds": int,
    "target_duration_seconds": int,   # 用户请求的目标时长
    "duration_mode": "fixed" | "auto",
    "style": str,
    "bgm_mood": str,
    "voice_id": str,
    "generation_model": str,
    "no_audio": bool,                  # 兼容旧字段
    "video_model_no_audio": bool,
    "voiceover_no_audio": bool,
    "intent": {
        "video_type": str,
        "platform": str,
        "style": str,
        "target_duration_seconds": int,
        "duration_mode": "fixed" | "auto",
        "generation_model": str,
        "video_model_no_audio": bool,
        "voiceover_no_audio": bool,
    },
    "source_images": [
        {
            "image_idx": int,
            "image_id": str,
            "image_path": str,
            "original_image_path": str,
            "filename": str,
            "width": int | None,
            "height": int | None,
            "media_type": str,
            "tags": str | None,
            "image_content": str,
            "visual_role": str,
            "key_subjects": list[str],
            "marketing_angle": str,
        }
    ],
    "image_context": list[dict],      # 与 source_images 同源，兼容旧读取路径
    "state_machine": {
        "completed_states": list[str],
        "current_state": "finalize",
    },
    "voice_config": {
        "voice_id": str,
        "speed": float,
    },
    "background_context": str,
}
```

### ReplicationPlannerAgent

文件：`backend/app/agents/stages/replication_planner.py`

职责：复刻链路首节点。解析参考视频，提取关键帧，生成复刻方案，并要求人工确认。

输入来源：

- LangGraph 首节点在 `input_config.reference_video_id` 存在时进入该 Agent
- 输入就是原始 `input_config`

输入：

```python
{
    "reference_video_id": str,
    "script": str,
    "platform": str,
    "style": str,
    "voice_id": str,
    "background_context": str,
    "adjustment_feedback": str,
}
```

输出 artifact key：`orchestrator_plan`

输出：

```python
{
    "requires_confirmation": True,
    "replication_plan": dict,
    "extracted_frames": list[dict],
    "analysis_report": str,
    "tool_call_log": list[dict],
    "analysis_mode": str,
    "platform": str,
    "style": str,
    "voice_config": {
        "voice_id": str,
        "speed": float,
    },
    "script": str,
    "background_context": str,
}
```

注意：当 `requires_confirmation=True` 时，LangGraph 节点会抛出 `WaitingConfirmation`，pipeline 状态变为 `waiting_confirmation`。用户确认后，后端会把复刻方案转换成普通 `orchestrator_plan`，再继续后续节点。

### RemixPlannerAgent / RemixAssemblerAgent

文件：

- `backend/app/agents/stages/remix_planner.py`
- `backend/app/agents/stages/remix_assembler.py`

职责：多视频混剪链路。`reference_video_ids` 包含 2 个及以上视频时，执行器先进入 `RemixPlannerAgent`，生成 `remix_plan` 并暂停为 `waiting_remix_confirmation`。用户确认后，若 `remix_config.add_voiceover=true`，执行器会先复用 `AudioSubtitleAgent` 生成或复用旁白音频 / 字幕，以真实音频时长调整 `remix_plan.target_duration_seconds` 和片段起止时间；若当前片段无法覆盖旁白长度，会把 `remix_config.target_duration_seconds` 更新为音频时长，自动重跑 `RemixPlannerAgent`，并再次等待用户确认。

`RemixAssemblerAgent` 只在时间线可覆盖旁白音频后执行：它从源视频抽取已确认片段，拼接转场，按 `audio_design.strategy` 处理源声、BGM 或静音，并把旁白音轨混入最终视频。混剪字幕烧录复用普通剪辑链路的透明 PNG overlay 渲染方式，通过 FFmpeg `overlay` filter 叠加，不依赖 FFmpeg `subtitles` filter。

### PromptEngineerAgent

文件：`backend/app/agents/stages/prompt_engineer.py`

职责：把分镜计划变成每个镜头的视频生成 prompt，决定每个 shot 的短视频生成时长，并设计配音参数。

输入来源：

```python
artifacts["orchestrator_plan"]
```

输入：

```python
{
    "creative_brief": str,
    "explicit_script": str,
    "style": str,
    "video_type": str,
    "platform": str,
    "duration_seconds": int,
    "target_duration_seconds": int,
    "duration_mode": "fixed" | "auto",
    "voice_config": dict,
    "image_context": list[dict],
    "source_images": list[dict],
    "background_context": str,
}
```

这里的 `shot_prompts[*].duration_seconds` 是后续 `video_generator` 和 `video_editor` 使用的镜头时长来源；PromptEngineer 会把模型输出时长吸附到当前视频 provider 支持的时长集合中。

输出 artifact key：`prompt_plan`

输出：

```python
{
    "shot_prompts": [
        {
            "shot_idx": int,
            "image_path": str,
            "image_content": str,
            "source_image": dict,
            "video_prompt": str,
            "duration_seconds": int,
            "script_segment": str,
        }
    ],
    "voice_params": {
        "voice_id": str,
        "speed": float,
        "tone": str,
    },
}
```

如果 `input_config.review_prompts` 或 `settings.HUMAN_IN_LOOP_PROMPT_REVIEW` 开启，节点完成后会抛出 `WaitingPromptReview`，pipeline 状态变为 `waiting_prompt_review`。

### AudioSubtitleAgent

文件：`backend/app/agents/stages/audio_subtitle.py`

职责：生成口播音频和字幕文件。若 `voiceover_no_audio=True` 或脚本为空，则跳过。

输入来源：

`PipelineExecutorSupportMixin.build_audio_input(...)` 从 `prompt_plan`、`orchestrator_plan` 和 `input_config` 构造。

输入：

```python
{
    "script": str,
    "voice_params": dict,
    "no_audio": voiceover_no_audio,
    "voiceover_no_audio": bool,
}
```

输出 artifact key：`audio`

输出：

```python
{
    "audio_path": str,
    "subtitle_path": str,
    "duration_ms": int,
}
```

跳过时输出：

```python
{
    "audio_path": "",
    "subtitle_path": "",
    "duration_ms": 0,
    "skipped": True,
    "skip_reason": "voiceover_no_audio" | "empty_script",
}
```

### VideoGeneratorAgent

文件：`backend/app/agents/stages/video_generator.py`

职责：按镜头并发调用视频生成服务，把参考图片和 `video_prompt` 转成视频片段。

输入来源：

`PipelineExecutorSupportMixin.build_video_input(...)` 从 `prompt_plan` 和 `input_config` 构造。

输入：

```python
{
    "shot_prompts": [
        {
            "shot_idx": int,
            "image_path": str,
            "video_prompt": str,
            "duration_seconds": int,
            "script_segment": str,
        }
    ],
    "source_images": list[dict],
    "no_audio": video_model_no_audio,
    "video_model_no_audio": bool,
    "generation_model": str,
    "platform": str,
    "regenerate_indices": list[int] | None,
}
```

`build_video_input(...)` 会信任 `prompt_plan.shot_prompts[*].image_path`；如果导演 Agent 没有填 `image_path`，才按 `source_image_idx / shot_idx` 从 `orchestrator_plan.source_images` 兜底补齐图片路径和图片内容。

输出 artifact key：`video_clips`

输出：

```python
{
    "video_clips": [
        {
            "shot_idx": int,
            "video_path": str,
            "duration_seconds": int,
            "task_id": str,
            "generation_model": str,
        }
    ]
}
```

内部服务：

- `VideoGeneratorRouter` 根据 `generation_model` 选择 Seedance、Kling 或 mock
- 每个镜头受 `settings.MAX_CONCURRENT_SHOTS` 并发限制，当前默认 `2`
- 每个镜头受 `settings.VIDEO_GENERATION_TIMEOUT_SECONDS` 超时限制
- 外部视频生成 HTTP 请求会按 `settings.VIDEO_GENERATION_HTTP_RETRIES` 和 `settings.VIDEO_GENERATION_HTTP_RETRY_BACKOFF_SECONDS` 对服务端断连、超时类传输错误和 429/5xx 临时错误做重试

### VideoEditorAgent

文件：`backend/app/agents/stages/video_editor.py`

职责：把视频片段、音频、字幕、转场、BGM、水印合成为最终 mp4。

输入来源：

`PipelineExecutorSupportMixin.build_editor_input(...)` 从 `video_clips`、`audio`、`prompt_plan`、`orchestrator_plan` 和 `input_config` 构造。

输入：

```python
{
    "video_clips": list[dict],
    "audio_path": str,
    "subtitle_path": str,
    "shot_prompts": list[dict],
    "duration_mode": "fixed" | "auto",
    "shot_durations": list[int],
    "transition": str,
    "transition_duration": float,
    "bgm_mood": str,
    "bgm_volume": float,
    "watermark_path": str | None,
}
```

输出 artifact key：`final_video`

输出：

```python
{
    "final_video_path": str,
    "duration_ms": int,
}
```

真实编辑服务 `RealVideoEditorService` 会：

- 沿用导演方案 / `video_generator` 输出的 `shot_idx` 顺序，不再调用 LLM 重排片段
- 下载或定位各片段
- 用 ffmpeg 裁剪、转码、拼接或添加 xfade 转场
- 对齐音频和视频长度
- 混入 BGM
- 烧录字幕
- 添加水印
- 探测最终视频时长

### QAReviewerAgent

文件：`backend/app/agents/stages/qa_reviewer.py`

职责：可选 QA 节点。检查缺失片段、时长偏差、音画同步、prompt 质量等问题，并决定是否自动重试某一阶段。

输入来源：

`PipelineExecutorSupportMixin.build_qa_input(...)` 从所有前序 artifacts 和 `input_config` 构造。

输入：

```python
{
    "shot_prompts": list[dict],
    "video_clips": list[dict],
    "audio": dict,
    "final_video": dict,
    "input_config": dict,
}
```

输出 artifact key：`qa_report`

输出：

```python
{
    "passed": bool,
    "overall_score": float,
    "issues": [
        {
            "severity": "critical" | "warning" | "info",
            "category": str,
            "message": str,
        }
    ],
    "recommendation": "pass" | "retry_video_generator" | "retry_audio" | "retry_editor",
}
```

当 `settings.QA_AUTO_RETRY_ENABLED=True` 时，LangGraph 会根据 `recommendation` 路由回对应节点，直到通过或超过 `settings.MAX_QA_RETRIES`。

## Artifact 交接表

| 阶段 | 输入来源 | 输出 artifact | 后续使用方 |
| --- | --- | --- | --- |
| `orchestrator` | `input_config` | `orchestrator_plan` | `prompt_engineer`, `audio_subtitle`, `video_editor` |
| `replication_planner` | `input_config` + 参考视频 | `orchestrator_plan` 或 `replication_plan` | 人工确认后进入 `prompt_engineer` |
| `prompt_engineer` | `orchestrator_plan` | `prompt_plan` | `audio_subtitle`, `video_generator`, `video_editor`, `qa_reviewer` |
| `audio_subtitle` | `prompt_plan` + `orchestrator_plan` + `input_config` | `audio` | `video_editor`, `qa_reviewer` |
| `video_generator` | `prompt_plan` + `input_config` | `video_clips` | `video_editor`, `qa_reviewer` |
| `video_editor` | `video_clips` + `audio` + plans + `input_config` | `final_video` | `qa_reviewer`, pipeline result |
| `qa_reviewer` | 全部前序 artifacts + `input_config` | `qa_report` | LangGraph retry routing |

其中 `prompt_plan`、`audio`、`video_clips` 还会被拆成可展示的 `RepositoryAsset` 记录；文件类产物会复制到 `settings.VIDEO_REPOSITORY_DIR/artifacts/...`，远程视频 URL 则直接保存 URL。

## 最终返回

`LangGraphPipelineExecutor.run(...)` 在图执行完成后读取：

```python
final_video = result_state.get("final_video", {})
```

并把 `final_video.final_video_path` 写回 `PipelineRun.final_video_path`，同时将 run 状态置为 `completed`。

最终返回给调用方的是：

```python
{
    "final_video_path": str,
    "duration_ms": int,
}
```
