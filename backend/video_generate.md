# 视频生成链路说明

本文档描述当前自动模式下，从“用户在前端输入视频生成请求”到“最终视频生成和产物展示”的真实代码链路。

同步时间：`2026-04-17 CST`

## 1. 总览

当前视频生成主链路分成三段：

1. 对话调度段：前端消息 -> Auto Chat SSE -> `ChatAgent` -> runtime skill
2. 视频生产段：`PipelineRun` -> `OrchestratorAgent(Intake / Context)` -> `PromptEngineerAgent` -> `AudioSubtitleAgent + VideoGeneratorAgent` -> `VideoEditorAgent` -> `QAReviewerAgent`
3. 结果展示段：`AgentExecution` / `RepositoryAsset` / `VideoDelivery` -> 前端右侧栏和个人仓库

普通图文生成链路：

```text
用户输入
  -> AutoModeStudio.chatWithAgent(...)
  -> POST /api/projects/{project_id}/auto-sessions/{session_id}/chat
  -> ChatAgent
  -> generate_video runtime skill
  -> PipelineRun(input_config)
  -> LangGraphPipelineExecutor
  -> OrchestratorAgent(Intake / Context)
  -> PromptEngineerAgent
  -> AudioSubtitleAgent + VideoGeneratorAgent
  -> VideoEditorAgent
  -> QAReviewerAgent 可选
  -> PipelineRun.completed + final_video_path
  -> 自动保存成片 + 展示中间产物
```

带参考视频的复刻链路：

```text
用户输入
  -> ChatAgent
  -> replicate_video runtime skill
  -> PipelineRun(input_config with reference_video_id)
  -> LangGraphPipelineExecutor
  -> ReplicationPlannerAgent
  -> waiting_confirmation
  -> 用户确认后进入 Prompt / Audio / Video / Editor / QA
```

关键点：

- 当前用户消息会作为 `input_config.user_request` 进入 `OrchestratorAgent`。
- 当已选素材且用户明确要求“生成 / 制作 / 输出视频”时，`ChatAgent` 会直接启动 `generate_video`，并把用户原话写入 `input_config.user_request`。
- 普通生成时，`OrchestratorAgent` 是进入生产链路后的第一层 Intake / Context Agent。它内部先解析 `user_request / script / platform / duration / style / bgm / voice / video_model_no_audio / voiceover_no_audio / generation_model`，再整理图片素材上下文。
- `RequirementParserAgent` 源文件仍保留作兼容层和测试辅助，但新普通生成链路不再创建独立的 `requirement_parser` 执行记录。

## 2. 前端发起消息

入口文件：

- `frontend/src/components/pipeline/AutoModeStudio.vue`
- `frontend/src/api/autoSessions.ts`

用户在自动模式输入框发送消息后，前端调用：

```ts
chatWithAgent(projectId, sessionId, {
  role: "user",
  content,
  payload: { mutedLines: [] },
  force_tool,
  generation_model,
})
```

常见输入示例：

```text
帮我生成一个10s的视频，发布平台为抖音，主题为大健康
```

前端会同时带上当前选择的视频模型。素材不是直接放进消息 body，而是已经通过自动会话的素材选择关系保存在后端。

## 3. Auto Sessions Router 加载会话上下文

后端入口：

- `backend/app/routers/auto_sessions.py`

聊天接口会加载当前自动模式会话上下文，包括：

- `project_id`
- `session_id`
- `user_id`
- 已选素材 `selected_materials`
- 参考视频 `reference_video_id`
- 背景模板 `background_template_id`
- 草稿脚本
- 平台、时长模式、转场、BGM、是否无声
- 当前选择的视频生成模型 `generation_model`

核心形态：

```python
session_context = await _load_session_context(db, session, user)
session_context["force_tool"] = req.force_tool
session_context["generation_model"] = req.generation_model
stream = chat_agent.chat_stream(history, session_context)
```

Auto Chat SSE 还有一层心跳保护：等待下一条内部事件时，每 10 秒发一次 `status` 事件；如果 180 秒都没有内部事件，才按超时断开。这解决的是浏览器 / 代理 / 前端误以为连接静默的问题，不代表外部视频服务一定可用。

## 4. ChatAgent 路由到 generate_video

入口文件：

- `backend/app/agents/chat/agent.py`

`ChatAgent.chat_stream(...)` 会先判断当前消息属于哪类：

- 普通聊天：走 LLM 流式对话
- 明确视频生成：调用 `generate_video`
- 参考视频分析：调用 `analyze_video`
- 参考视频复刻：调用 `replicate_video`

当前针对视频生成有一条快路径：

```python
if self._should_launch_generate_video_directly(user_message, session_context):
    yield ChatEvent(type="status", content="已识别为视频生成任务，直接启动视频流水线。")
    async for event in self._execute_forced_tool("generate_video", user_message, session_context):
        yield event
    return
```

它要求同时满足：

- `generate_video` skill 已注册且有权限
- 当前会话有必要输入，例如 `project_id / session_id / user_id / image_ids`
- 用户不是只要“方案 / 策划 / 设计稿”
- 用户消息看起来是明确的视频生产动作，例如“生成视频 / 制作视频 / 输出视频 / 开始生成”

命中快路径时，`ChatAgent` 不再让 LLM 做工具参数抽取，而是直接把原始用户消息作为：

```python
{"user_request": user_message}
```

再由 `_execute_forced_tool(...)` 补齐会话上下文字段，例如：

```python
project_id
session_id
user_id
image_ids
platform
duration_mode
generation_model
style
background_template_id
watermark_image_id
no_audio                  # 兼容旧字段
video_model_no_audio      # 是否关闭 Seedance/Kling 模型原声，默认 true
voiceover_no_audio        # 是否跳过 VidGen TTS 配音/字幕，默认 true
transition
bgm_mood
duration_seconds
```

这里的 `image_ids` 来自当前会话已选素材。

## 5. generate_video Runtime Skill 创建 PipelineRun

入口文件：

- `backend/app/agents/skills/generate-video/runtime.py`

核心函数：

```python
async def generate_video(...)
```

它会把 ChatAgent 传来的参数整理成 `PipelineRun.input_config`：

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
    "video_model_no_audio": video_model_no_audio,  # 控制 Seedance/Kling 自带声音
    "voiceover_no_audio": voiceover_no_audio,      # 控制 VidGen TTS/字幕
    "generation_model": generation_model or settings.VIDEO_GENERATION_MODEL,
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

字段语义：

- `user_request`：用户创作目标，例如“生成一个10s的大健康抖音视频”
- `script`：用户明确提供、可直接播报的旁白脚本
- `image_ids`：当前会话已选素材
- `duration_seconds`：目标时长；如果没有传，runtime 会按素材数量给一个兜底值

创建 `PipelineRun` 后，runtime skill 会更新 `AutoChatSession.current_run_id`，然后后台启动：

```python
asyncio.create_task(_run_pipeline(...))
```

返回给前端的 `tool_result` 包含：

```python
{
    "run_id": run.id,
    "status": "started",
    "run": {...}
}
```

前端收到 `run_id` 后开始监听 pipeline 状态。

## 6. LangGraphPipelineExecutor 启动图执行

入口文件：

- `backend/app/agents/executors/langgraph/executor.py`
- `backend/app/agents/executors/langgraph/nodes.py`
- `backend/app/agents/executors/shared.py`

当前默认执行器由 `settings.PIPELINE_ENGINE` 决定，默认是 `langgraph`。

`LangGraphPipelineExecutor.run(...)` 创建 `AgentContext`：

```python
context = AgentContext(
    trace_id=str(uuid.uuid4()),
    pipeline_run_id=pipeline_run_id,
    project_id=project_id,
    db_session_factory=self.db_session_factory,
    usage_recorder=UsageRecorder(self.db_session_factory),
    artifacts={},
    user_id=user_id,
    memory_service=memory_service,
    mem0=mem0,
)
```

然后把初始 state 交给 LangGraph：

```python
{
    "context": context,
    "input_config": input_config,
    "qa_retry_count": 0,
}
```

首节点路由逻辑：

```python
if input_config.get("reference_video_id"):
    return "replication_planner"
return "orchestrator"
```

因此普通生成链路是：

```text
START -> orchestrator -> prompt_engineer
```

复刻链路是：

```text
START -> replication_planner -> prompt_engineer
```

## 7. OrchestratorAgent 做 Intake / Context

入口文件：

- `backend/app/agents/stages/orchestrator.py`
- `backend/app/agents/stages/requirement_utils.py`

`OrchestratorAgent` 是普通视频生成链路的首节点。它内部先做需求解析，读取：

```python
raw_message = input_data.get("user_request") or input_data.get("script")
```

然后解析：

```python
{
    "user_intent": str,
    "explicit_script": str,
    "platform": "xiaohongshu" | "douyin" | "bilibili" | "generic",
    "duration_seconds": int,
    "style": "commercial" | "lifestyle" | "cinematic" | "vlog" | "documentary",
    "bgm_mood": "none" | "upbeat" | "calm" | "cinematic" | "energetic",
    "voice_id": str,
    "image_ids": list[str],
    "duration_mode": str,
    "generation_model": str,
    "no_audio": bool,                  # 兼容旧字段
    "video_model_no_audio": bool,
    "voiceover_no_audio": bool,
    ...
}
```

它先用关键词快速推断平台、风格、BGM 和时长。消息较长或隐含信息较多时，再调用 Qwen 结构化解析。解析失败不会中断 pipeline；进度会区分 `Qwen 请求失败`、`Qwen 返回解析失败`、`Qwen 返回校验失败`，然后使用本地关键词规则兜底。

需求解析后，Orchestrator 继续做素材上下文整理：

- 读取 `user_request / script`
- 读取并校验 `image_ids`
- 将素材 ID 转成真实图片路径
- 按平台画幅预处理图片
- 多模态理解每张图片的内容、营销角色和关键主体
- 输出供 PromptEngineer 使用的“导演输入上下文”

Orchestrator 不再负责最终分镜、旁白和配音设计；这些由 `PromptEngineerAgent` 作为导演 Agent 生成。

当前 Orchestrator 内部按状态机推进，并通过 `AgentContext.report_progress(...)` 写入 `AgentExecution.progress_text`。前端右侧栏会看到类似：

```text
intake: 读取用户消息、会话参数和已选素材
parse_requirements: 已解析需求：平台=douyin，时长=10s，风格=commercial，BGM=none。
resolve_images: 确认 3 张图片素材
preprocess_images: 按 douyin 平台画幅准备图片
analyze_images: 多模态理解图片内容、营销角色，确认平台和风格。
finalize: 素材感知完成：已整理图片内容、营销角色和上下文，交给导演 Agent 创作分镜方案。
```

输出写入：

```python
context.artifacts["orchestrator_plan"]
```

输出结构大致为：

```python
{
    "creative_brief": str,
    "explicit_script": str,
    "platform": str,
    "duration_seconds": int,
    "target_duration_seconds": int,
    "duration_mode": str,
    "style": str,
    "bgm_mood": str,
    "voice_id": str,
    "generation_model": str,
    "no_audio": bool,                  # 兼容旧字段
    "video_model_no_audio": bool,
    "voiceover_no_audio": bool,
    "background_context": str,
    "source_images": [
        {
            "image_idx": int,
            "image_id": str,
            "image_path": str,
            "original_image_path": str,
            "image_content": str,
            "visual_role": str,
            "key_subjects": list[str],
            "marketing_angle": str,
        }
    ],
    "image_context": [...],
    "video_type": str,
    "voice_config": {...},
    "intent": {...},
    "state_machine": {...},
}
```

## 9. PromptEngineerAgent 生成视频提示词

入口文件：

- `backend/app/agents/stages/prompt_engineer.py`

输入来自：

```python
artifacts["orchestrator_plan"]
```

它会根据：

- `image_context / source_images`
- `creative_brief / explicit_script`
- 平台
- 风格
- 视频类型
- 目标时长
- 背景模板上下文

生成每个镜头的视频生成提示词和配音参数：

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

输出写入：

```python
context.artifacts["prompt_plan"]
```

同时，`BaseAgent.run(...)` 会把这些用户可读产物保存为 `RepositoryAsset`：

- `prompt_engineer.plan`
- `prompt_engineer.shot.{shot_idx}`
- `prompt_engineer.voice_params`

## 10. AudioSubtitleAgent 和 VideoGeneratorAgent 并行执行

LangGraph 中 `prompt_engineer` 后会同时进入：

```text
audio_subtitle
video_generator
```

### 10.1 AudioSubtitleAgent

入口文件：

- `backend/app/agents/stages/audio_subtitle.py`

输入由 `PipelineExecutorSupportMixin.build_audio_input(...)` 构造：

```python
{
    "script": shot_script or orchestrator_plan["script"] or input_config["script"],
    "voice_params": prompt_plan["voice_params"],
    "no_audio": input_config.get("voiceover_no_audio", input_config.get("no_audio", True)),
    "voiceover_no_audio": input_config.get("voiceover_no_audio", input_config.get("no_audio", True)),
}
```

如果 `voiceover_no_audio=True` 或脚本为空，它会跳过 TTS：

```python
{
    "audio_path": "",
    "subtitle_path": "",
    "duration_ms": 0,
    "skipped": True,
    "skip_reason": "voiceover_no_audio" | "empty_script",
}
```

否则它会生成：

- TTS 音频文件
- 字幕文件
- 音频时长

输出写入：

```python
context.artifacts["audio"]
```

并保存为 `RepositoryAsset`：

- `audio_subtitle.audio`
- `audio_subtitle.subtitle`
- 如果跳过，则保存 `audio_subtitle.status`

### 10.2 VideoGeneratorAgent

入口文件：

- `backend/app/agents/stages/video_generator.py`
- `backend/app/services/video_generator.py`

输入由 `PipelineExecutorSupportMixin.build_video_input(...)` 构造：

```python
{
    "shot_prompts": list[dict],
    "source_images": list[dict],
    "no_audio": video_model_no_audio,
    "video_model_no_audio": bool,
    "generation_model": str,
    "platform": str,
}
```

这里有一个重要保护：`build_video_input(...)` 会信任 `prompt_plan.shot_prompts[*].image_path`。如果导演 Agent 没有填 `image_path`，才按 `source_image_idx / shot_idx` 从 `orchestrator_plan.source_images` 兜底补齐图片路径和图片内容。

`VideoGeneratorAgent` 对每个 shot 执行：

```python
task = await self.generator.generate(...)
status = await self.generator.poll_status(task.task_id)
```

并发与稳定性配置：

- `MAX_CONCURRENT_SHOTS=2`
- `VIDEO_GENERATION_TIMEOUT_SECONDS=600`
- `VIDEO_GENERATION_HTTP_RETRIES=2`
- `VIDEO_GENERATION_HTTP_RETRY_BACKOFF_SECONDS=2.0`

`MAX_CONCURRENT_SHOTS` 控制同一 run 内同时提交到外部视频服务的 shot 数量。当前默认降到 2，是为了减少多张图片并发上传 / 创建任务时被服务端断开的概率。

`video_generator.py` 服务层会对以下临时问题做有限重试：

- `httpx.TransportError`
- 服务端断开连接
- 408 / 409 / 425 / 429 / 500 / 502 / 503 / 504

如果看到类似错误：

```text
Server disconnected without sending a response
```

通常说明请求已经到达外部视频生成服务或其网关，但服务端没有返回有效 HTTP 响应。这不是 `OrchestratorAgent` 没识别需求，也不是素材没有进入链路。

生成成功后输出：

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

输出写入：

```python
context.artifacts["video_clips"]
```

并保存为 `RepositoryAsset`：

- `video_generator.manifest`
- `video_generator.shot.{shot_idx}`

## 11. VideoEditorAgent 合成最终视频

入口文件：

- `backend/app/agents/stages/video_editor.py`
- `backend/app/services/video_editor_service.py`

输入由 `PipelineExecutorSupportMixin.build_editor_input(...)` 构造：

```python
{
    "video_clips": video_clips,
    "audio_path": audio_path,
    "subtitle_path": subtitle_path,
    "shot_prompts": shot_prompts,
    "duration_mode": duration_mode,
    "shot_durations": shot_durations,
    "transition": transition,
    "transition_duration": transition_duration,
    "bgm_mood": bgm_mood,
    "bgm_volume": bgm_volume,
    "watermark_path": watermark_path,
}
```

它负责：

- 下载或读取分镜视频
- 按镜头顺序拼接
- 按平台尺寸裁剪 / 填充
- 添加转场
- 合入音频
- 合入字幕
- 合入 BGM
- 加水印
- 输出最终 mp4

成功后输出：

```python
{
    "final_video_path": str,
    "duration_ms": int,
}
```

输出写入：

```python
context.artifacts["final_video"]
```

## 12. QAReviewerAgent 可选审核

入口文件：

- `backend/app/agents/stages/qa_reviewer.py`

如果 `QA_REVIEW_ENABLED=True`，`video_editor` 后会进入 QA 节点。

QA 检查内容包括：

- 分镜覆盖
- 视频片段缺失
- 时长偏差
- 音视频同步
- 提示词质量

输出写入：

```python
context.artifacts["qa_report"]
```

如果 `QA_AUTO_RETRY_ENABLED=True`，且 QA 给出建议：

```text
retry_video_generator
retry_audio
retry_editor
```

LangGraph 会路由回对应节点，直到通过或超过 `MAX_QA_RETRIES`。

## 13. PipelineRun 完成与成片入仓

`LangGraphPipelineExecutor.run(...)` 完成后读取：

```python
final_video = (result_state.get("final_video") or {}).get("final_video_path")
```

然后更新：

```python
PipelineRun.status = "completed"
PipelineRun.final_video_path = final_video
PipelineRun.completed_at = now
```

后台任务 `_run_pipeline(...)` 随后调用：

```python
_auto_save_run_to_repository(run_id)
```

如果 run 已完成且 `final_video_path` 存在，会通过 `save_video_to_repository(...)` 创建 `VideoDelivery` 记录，把最终成片保存到本地视频仓库。

## 14. 中间产物入仓与可视化

入口文件：

- `backend/app/services/pipeline_artifact_repository.py`
- `backend/app/agents/core/base.py`
- `backend/app/routers/pipeline.py`
- `backend/app/routers/repository.py`
- `frontend/src/components/pipeline/AutoModeStudio.vue`
- `frontend/src/components/repository/RepositoryPage.vue`

`BaseAgent.run(...)` 在 Agent 成功后会调用：

```python
save_agent_artifacts(...)
```

当前自动入仓的 Agent：

- `prompt_engineer`
- `audio_subtitle`
- `video_generator`

保存到模型：

```python
RepositoryAsset
```

保存规则：

- 文本类产物写入 `text_content`
- 本地文件类产物复制到 `settings.VIDEO_REPOSITORY_DIR/artifacts/...`
- 远程视频 URL 直接保存 URL
- `asset_key` 用于区分类型，例如 `prompt_engineer.shot.0`

相关接口：

```text
GET /api/projects/{project_id}/pipeline/{run_id}/artifacts
GET /api/repository/assets
```

前端展示：

- 自动模式右侧栏：按 `提示词 Agent / 音频 Agent / 视频生成 Agent` 分组展示当前 run 的中间产物
- 个人仓库：新增 `Agent 产物` 标签页，展示当前账号历史 run 的中间产物

因此即使后续 `video_generator` 或 `video_editor` 失败，已经成功完成的上游产物仍然会保留下来，便于排查和复用。

## 15. 前端如何看到进度和结果

入口文件：

- `frontend/src/components/pipeline/AutoModeStudio.vue`
- `frontend/src/api/pipeline.ts`

前端拿到 `run_id` 后调用：

```ts
startPolling(run.id)
```

内部会打开 pipeline SSE：

```ts
pipelineStream = streamPipeline(props.projectId, runId)
```

SSE 事件返回：

```ts
{
  run,
  agents
}
```

同时 `refreshRun(runId)` 会拉取：

```ts
getPipelineRun(...)
getPipelineAgents(...)
getPipelineUsage(...)
getPipelineDelivery(...)
getPipelineArtifacts(...)
```

前端右侧栏展示：

- 当前 run 状态
- 当前 agent
- 每个 `AgentExecution`
- `progress_text`
- 错误信息
- Token 消耗
- 中间产物
- 交付入口
- 发布草稿

如果用户想停止：

- “中止对话”：只断开当前 Auto Chat SSE / tool task
- “取消流程”：调用 `/api/projects/{project_id}/pipeline/{run_id}/cancel`，把已创建的 `PipelineRun` 标记为 `cancelled`

## 16. 失败归因

常见失败点和含义：

| 现象 | 真实含义 |
| --- | --- |
| `orchestrator` 已完成，但 `video_generator` 失败 | 需求和素材上下文已整理成功，失败发生在外部图生视频阶段 |
| `Qwen 请求失败` | LLM HTTP / 网络 / 超时失败，系统会按本地规则兜底 |
| `Qwen 返回解析失败 / 校验失败` | LLM 已返回内容，但 JSON 提取或字段校验不通过，系统会记录真实异常并兜底 |
| `Server disconnected without sending a response` | 外部视频服务或网关断开，没有返回有效 HTTP 响应 |
| 顶部 current agent 指向 `video_generator` | 当前失败归因已回写到真正失败的视频生成节点 |
| `audio_subtitle` skipped | 通常是 `voiceover_no_audio=True` 或脚本为空，不一定是错误 |
| 右侧有提示词 / 音频产物但无最终视频 | 上游产物已入仓，后续视频生成或剪辑失败 |

## 17. 一句话总结

当前全链路是：

```text
用户输入
  -> AutoModeStudio.chatWithAgent
  -> /auto-sessions/{session_id}/chat
  -> ChatAgent 识别视频生成意图
  -> generate_video runtime skill 创建 PipelineRun
  -> OrchestratorAgent 解析用户自由输入并整理素材上下文
  -> PromptEngineerAgent 生成 shot prompts 和 voice params
  -> AudioSubtitleAgent 生成音频字幕或跳过
  -> VideoGeneratorAgent 调外部视频服务生成分镜视频
  -> VideoEditorAgent 拼接剪辑成最终视频
  -> QAReviewerAgent 可选审核
  -> RepositoryAsset 保存中间产物
  -> VideoDelivery 保存最终成片
  -> 前端 SSE / 轮询展示状态、产物和交付入口
```

当前实现更准确地说是：用户原话先通过 `ChatAgent + generate_video runtime skill` 写入 `input_config.user_request`，然后由 `OrchestratorAgent` 内部完成需求解析与素材上下文整理，再把 `orchestrator_plan` 作为导演输入上下文交给 `PromptEngineerAgent`。
