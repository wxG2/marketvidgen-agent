# Agent 流程详解

> 生成时间: 2026-05-08 | 只读扫描，未修改任何源代码

---

## 1. Agent 总览表

| Agent 名 | 职责 | 上游 | 下游 | 调用的外部服务 |
|---------|------|------|------|--------------|
| RequirementParserAgent | 从自由文本中结构化提取平台/风格/时长等意图字段 | START（无 reference_video_id 时触发） | orchestrator | LLMService（Qwen） |
| OrchestratorAgent | 多模态理解图片，归一化平台/风格，生成素材摘要和创意简报 | requirement_parser / START | prompt_engineer | LLMService（Qwen 多模态）、Mem0、RagService |
| ReplicationPlannerAgent | 分析参考视频，提取关键帧，生成逐镜头复刻方案，触发 HITL | START（有 reference_video_id 时触发） | prompt_engineer（确认后） | LLMService（Qwen）、KeyframeExtractor |
| PromptEngineerAgent | 生成完整分镜方案（shot plan）：营销排序、素材引用、旁白脚本、时序、视觉 prompt | orchestrator 或 replication_planner | audio_subtitle + video_generator（并行） | LLMService（Qwen 多模态） |
| AudioSubtitleAgent | 调用 TTS 生成配音音频，再生成 SRT 字幕文件 | prompt_engineer | video_editor | TTSService（Qwen TTS） |
| VideoGeneratorAgent | 按分镜并发提交图生视频任务，轮询状态，收集片段 | prompt_engineer | video_editor | VideoGenerator（Seedance / Kling） |
| VideoEditorAgent | 用 FFmpeg 按导演方案时长裁剪片段，再合成最终 MP4 | audio_subtitle + video_generator | qa_reviewer（启用时）/ END | VideoEditorService（FFmpeg） |
| QAReviewerAgent | 规则检查 + LLM 评审，输出通过/回退建议 | video_editor | END 或回退到 video_generator/audio_subtitle/video_editor | LLMService（Qwen） |
| ChatAgent | 对话式入口，路由到 runtime skill 或普通 LLM 回复 | HTTP POST /auto-sessions/.../chat | generate_video / analyze_video / replicate_video skill | LLMService、ToolRegistry、Mem0 |

---

## 2. 编排器

### 2.1 两种执行引擎

| 引擎 | 文件 | 选择条件 |
|------|------|---------|
| LangGraphPipelineExecutor | `agents/executors/langgraph/executor.py` | 默认引擎（`settings.PIPELINE_ENGINE` 控制） |
| PipelineExecutor（顺序） | `agents/executors/pipeline.py` | 旧版顺序执行引擎，可通过配置切换 |

以下均描述 **LangGraphPipelineExecutor**。

---

### 2.2 GraphState 字段清单

文件: `agents/executors/langgraph/state.py`

```python
class LangGraphPipelineState(TypedDict, total=False):
    context:           AgentContext   # trace_id、pipeline_run_id、artifacts、mem0、rag_service
    input_config:      dict           # 用户输入配置（platform、script、duration_seconds 等）
    parsed_requirement:dict           # RequirementParser 输出的结构化需求
    orchestrator_plan: dict           # Orchestrator 或 ReplicationPlanner 的输出
    prompt_plan:       dict           # PromptEngineer 的分镜方案
    audio:             dict           # audio_path、subtitle_path、duration_ms
    video_clips:       dict           # {"video_clips": [{"shot_idx", "video_path", "duration_seconds"}]}
    final_video:       dict           # {"final_video_path", "duration_ms"}
    qa_report:         dict           # {"passed", "overall_score", "issues", "recommendation"}
    qa_retry_count:    int            # QA 已触发的重试次数
    error:             str            # 失败时的错误消息
```

`AgentContext`（不在 State 字段里，但嵌套在 `context` 中）包含:
- `trace_id`、`pipeline_run_id`、`project_id`、`user_id`
- `artifacts`（dict，各 Agent 写入中间结果的共享区域）
- `db_session_factory`、`usage_recorder`
- `memory_service`、`mem0`、`rag_service`

---

### 2.3 节点注册（add_node）

| 节点名 | 对应函数 |
|--------|---------|
| `orchestrator` | `_orchestrator_node` |
| `replication_planner` | `_replication_planner_node` |
| `prompt_engineer` | `_prompt_engineer_node` |
| `audio_subtitle` | `_audio_node` |
| `video_generator` | `_video_node` |
| `video_editor` | `_editor_node` |
| `qa_reviewer` | `_qa_node`（仅当 `QA_REVIEW_ENABLED=true` 且 qa_reviewer 不为 None） |

---

### 2.4 边定义（add_edge / add_conditional_edges）

```
START
  ├─[有 reference_video_id]──▶ replication_planner
  └─[无 reference_video_id]──▶ orchestrator

orchestrator      ──▶ prompt_engineer
replication_planner──▶ prompt_engineer

prompt_engineer   ──▶ audio_subtitle   (并行)
prompt_engineer   ──▶ video_generator  (并行)

audio_subtitle    ──▶ video_editor
video_generator   ──▶ video_editor

video_editor      ──▶ qa_reviewer      (QA_REVIEW_ENABLED=true)
                  ──▶ END              (QA_REVIEW_ENABLED=false)

qa_reviewer [条件路由 _qa_routing]:
  ├─ "pass"                ──▶ END
  ├─ "retry_video_generator"──▶ video_generator
  ├─ "retry_audio"         ──▶ audio_subtitle
  └─ "retry_editor"        ──▶ video_editor
```

---

### 2.5 HITL 暂停点

| 暂停点 | 触发条件 | 抛出异常 | 恢复入口 |
|--------|---------|---------|---------|
| 分镜方案确认 | `ReplicationPlannerAgent` 输出 `requires_confirmation=True` | `WaitingConfirmation` | `POST .../confirm-plan` → `resume_from_confirmation()` |
| Prompt 审核 | `settings.HUMAN_IN_LOOP_PROMPT_REVIEW=true` 或 `input_config["review_prompts"]=true` | `WaitingPromptReview` | `POST .../confirm-prompt-review` → `resume_from_prompt_review()` |

---

### 2.6 Checkpoint 写入点

每个 Agent 节点成功执行后，调用 `await context.save_checkpoint()`，将 `context.artifacts` 序列化写入数据库（`agent_state` 表）。共有 6 处：
1. RequirementParser 成功后
2. Orchestrator / ReplicationPlanner 成功后
3. PromptEngineer 成功后（HITL 触发前也已写入）
4. AudioSubtitle + VideoGenerator 并行完成后
5. VideoEditor 成功后
6. QAAgent 成功后

### 2.7 失败重试续跑

`POST /api/projects/{project_id}/pipeline/{run_id}/retry-agent` 会读取最近失败的 `AgentExecution`，重跑该 Agent 后调用 `continue_from_retry(...)` 继续下游。LangGraph 执行器已支持该入口：例如 `audio_subtitle` 因 TTS 超时失败时，如果 `video_generator` 已经成功，续跑会复用现有 `video_clips`，只继续执行 `video_editor` 和可选 `qa_reviewer`。

---

## 3. 每个 Agent 的详细 IO 描述

### RequirementParserAgent

- **职责**: 将自由文本用户请求结构化为平台/风格/时长/视频类型等字段
- **触发条件**: START 路由到 orchestrator 时，如果 `requirement_parser` 不为 None 且 `input_config` 中没有 `reference_video_id`，则先执行
- **输入（从 input_config 读）**: `user_request`、`script`、`image_ids`
- **输出（写入 State）**: `parsed_requirement`（dict：platform、style、duration_seconds 等）
- **调用的工具/服务**: `LLMService.generate_structured`（JSON 模式）
- **关键决策点**: `needs_llm_requirement_parsing(raw_message)` 为 False 则跳过 LLM 调用
- **失败处理**: 失败时记录 warning 并继续（不阻断流水线，返回空 dict）

---

### OrchestratorAgent

- **职责**: 多模态理解图片素材，归一化平台/风格，生成逐图摘要和营销角色，构建导演上下文
- **触发条件**: 无 `reference_video_id` 时由 START 路由触发
- **输入（从 input_config / artifacts 读）**:
  - `user_request`、`script`、`image_ids`（图片 ID 列表）
  - `platform`、`style`、`duration_seconds`、`duration_mode`
  - `background_context`（背景描述）
- **输出（写入 State）**: `orchestrator_plan`（dict）含:
  - `source_images`: 每张图的摘要、visual_role、marketing_angle
  - `platform`、`style`、`video_type`（detected/confirmed）
  - `creative_brief`、`duration_seconds`、`duration_mode`
- **调用的工具/服务**:
  - `LLMService.generate_structured`（Qwen 多模态，发送图片路径）
  - `context.mem0.search`（检索用户历史风格偏好）
  - `context.rag_service.retrieve_similar`（检索相似历史方案）
  - `context.mem0.add_explicit`（记录本次执行上下文）
- **关键决策点**:
  - 内部有 6 个状态枚举（`INTAKE` → `PARSE_REQUIREMENTS` → `RESOLVE_IMAGES` → `PREPROCESS_IMAGES` → `ANALYZE_IMAGES` → `FINALIZE`）
  - LLM 返回缺少 `image_summaries` 时使用本地摘要兜底
  - 平台变化时重新裁剪图片尺寸（`_preprocess_image_assets_for_platform`）
- **失败处理**: LLM 调用失败时使用本地摘要兜底，不抛异常；最终失败抛 `RuntimeError`（上层捕获后设流水线状态为 failed）

---

### ReplicationPlannerAgent

- **职责**: 下载/读取参考视频，提取关键帧，调用 LLM 生成逐镜头复刻方案，等待用户确认
- **触发条件**: `input_config` 含 `reference_video_id` 时由 START 路由到此节点
- **输入（从 input_config 读）**:
  - `reference_video_id`（VideoUpload 表主键）
  - `session_id`、`user_id`
  - `platform`、`style`、`direction`（复刻方向提示）
- **输出（写入 State）**: `orchestrator_plan`（含复刻方案）；同时写 `context.artifacts["replication_plan"]`
- **调用的工具/服务**:
  - 数据库查询 `VideoUpload` 获取视频路径
  - `KeyframeExtractor`：从参考视频提取关键帧（本地 FFmpeg/OpenCV）
  - `LLMService.generate_structured`（Qwen 多模态）：逐镜头分析
  - 数据库查询 `AutoSessionMaterialSelection`：获取会话素材
  - 数据库查询 `Material`：获取素材文件路径
- **关键决策点**:
  - 内置 `_run_cancellable` 包装长耗时协程，支持流水线取消
  - 输出中 `requires_confirmation=True` 时抛 `WaitingConfirmation`，流水线暂停
- **失败处理**: 失败抛 `RuntimeError`；长耗时操作轮询 `context.is_cancelled()` 支持取消

---

### PromptEngineerAgent

- **职责**: 基于图片摘要/复刻方案生成完整分镜方案（shot plan）：每个镜头的旁白、视觉 prompt、时长、镜头运动
- **触发条件**: orchestrator 或 replication_planner 完成后
- **输入（从 artifacts 读）**:
  - `source_images` 或 `shots`（复刻路径）
  - `creative_brief`、`platform`、`style`、`video_type`
  - `duration_seconds`、`duration_mode`、`voice_config`
  - `explicit_script`（用户提供的旁白）、`background_context`
- **输出（写入 State）**: `prompt_plan`（dict）含:
  - `shots`: 每镜头的 `shot_idx`、`source_image_idx`、`sequence_role`、`sequence_reason`、`video_prompt`、`script_segment`、`duration_seconds`、`generation_duration_seconds`
  - `narration_script`（完整旁白）
  - `total_duration`、`platform`、`style`
- **调用的工具/服务**:
  - `LLMService.generate_structured`（Qwen 多模态，JSON schema 输出）
  - `persist_director_plan_message`（将分镜方案写入 AutoChat 消息，供前端显示）
- **关键决策点**:
  - 有 `existing_shots`（复刻路径）时跳过图片分析，直接用关键帧生成 prompt
  - `review_prompts=true` 时完成后抛 `WaitingPromptReview`
  - 时长对齐：`snap_to_half_second`、`rhythmic_durations`、`generation_duration_for`
- **失败处理**: 无输入图片时立即返回失败；LLM 失败抛 `RuntimeError`

---

### AudioSubtitleAgent

- **职责**: 将旁白脚本转为 TTS 音频，再生成 SRT 字幕
- **触发条件**: prompt_engineer 完成后（与 video_generator 并行）
- **输入（从 artifacts 读）**:
  - `script`（旁白脚本）
  - `voice_params`（`voice_id`、`speed`）
  - `voiceover_no_audio`（bool）
- **输出（写入 State）**: `audio`（dict）含:
  - `audio_path`（wav/mp3 路径）
  - `subtitle_path`（SRT 路径）
  - `duration_ms`
  - `skipped: True`（`voiceover_no_audio=true` 或 script 为空时）
- **调用的工具/服务**:
  - `TTSService.synthesize`（Qwen TTS）
  - `TTSService.generate_subtitles`（基于 TTS 结果生成字幕）
- **关键决策点**:
  - `voiceover_no_audio=true` 或 `script` 为空时直接返回 skipped，不调用 TTS
- **失败处理**: TTS 失败抛 `RuntimeError`；两处 `is_cancelled()` 检查

---

### VideoGeneratorAgent

- **职责**: 并发提交所有分镜到图生视频 API，轮询状态，收集生成结果
- **触发条件**: prompt_engineer 完成后（与 audio_subtitle 并行）
- **输入（从 artifacts 读）**:
  - `shot_prompts`（list of dict：shot_idx、video_prompt、duration_seconds、image_path）
  - `source_images`（含 image_path 的素材列表，用于补全 shot 的图片路径）
  - `generation_model`、`video_model_no_audio`、`platform`
  - `regenerate_indices`（可选，仅重试指定镜头）
- **输出（写入 State）**: `video_clips`（dict）含:
  - `video_clips`: list of `{shot_idx, video_path, duration_seconds, generation_duration_seconds, task_id, generation_model}`
- **调用的工具/服务**:
  - `VideoGenerator.generate`（提交单镜头任务：Seedance / Kling）
  - `VideoGenerator.poll_status`（轮询，每 5 秒一次）
- **关键决策点**:
  - `asyncio.Semaphore(MAX_CONCURRENT_SHOTS)` 限制并发数
  - 每镜头有超时限制（`VIDEO_GENERATION_TIMEOUT_SECONDS`）
  - `generation_duration_seconds` = `snap_to_smallest_supported(target)` 确保 API 接受
  - 有 `regenerate_indices` 时保留其余镜头的已有结果
- **失败处理**: 单镜头失败记为 error，其他镜头继续；全部失败时返回 `AgentResult(success=False)`

---

### VideoEditorAgent

- **职责**: 用 FFmpeg 按导演方案中的 `duration_seconds` 裁剪镜头片段，并将音频、字幕合成最终 MP4
- **触发条件**: audio_subtitle 和 video_generator 均完成后
- **输入（从 artifacts 读）**:
  - `video_clips`: list of `{video_path, ...}`
  - `audio_path`、`subtitle_path`
  - `shot_prompts`、`duration_mode`、`shot_durations`
  - `transition`、`transition_duration`、`bgm_mood`、`bgm_volume`、`watermark_path`
  - `video_model_no_audio`
- **输出（写入 State）**: `final_video`（dict）含:
  - `final_video_path`（MP4 本地路径）
  - `duration_ms`
- **调用的工具/服务**:
  - `VideoEditorService.compose`（FFmpeg 封装）
- **关键决策点**:
  - `clip_paths` 为空时立即返回失败
  - 最终拼接顺序以导演方案 / `video_generator` 输出的 `shot_idx` 顺序为准，`VideoEditorService` 不再调用 LLM 重新排序
  - `video_model_no_audio` 决定是否混入模型原声还是替换为 TTS 音频
- **失败处理**: compose 失败抛 `RuntimeError`；有 `is_cancelled()` 检查

---

### QAReviewerAgent

- **职责**: 两层质量检查（硬规则 + LLM 评审），输出通过/回退建议
- **触发条件**: video_editor 完成后，且 `QA_REVIEW_ENABLED=true`
- **输入（从 artifacts 读）**:
  - `shot_prompts`、`video_clips`、`audio`、`final_video`
  - `input_config`（platform、duration_seconds、script）
- **输出（写入 State）**: `qa_report`（dict）含:
  - `passed`（bool）
  - `overall_score`（0–1）
  - `issues`: list of `{severity, category, message}`
  - `recommendation`: `"pass" | "retry_video_generator" | "retry_audio" | "retry_editor"`
- **调用的工具/服务**:
  - 硬规则：本地计算（缺失镜头、时长偏差、A/V 同步）
  - `LLMService.generate_structured`（Qwen，holistic review）
- **关键决策点**:
  - `has_critical` 强制覆盖 LLM 的 passed 结论
  - 回退映射 `_RECOMMENDATION_MAP`: `missing_clips`→generator, `duration_mismatch`→editor, `av_sync`→audio
  - `_qa_routing`: `qa_retry_count > MAX_QA_RETRIES` 时强制 pass（不再重试）
  - `QA_AUTO_RETRY_ENABLED=false` 时跳过重试，直接 pass
- **失败处理**: QA Agent 本身执行失败时返回 `{passed: True, score: 0.5}`（容错，不阻断投递）

---

## 4. ChatAgent 与 Skill 路由

### 4.1 ChatAgent 路由决策树

文件: `agents/chat/agent.py`

```
用户消息进入 chat_stream()
  │
  ├─ is_skill_inventory_question()? → 直接文本回复 skill 列表，结束
  │
  ├─ mem0.search() 检索用户记忆（如有）
  │
  ├─ force_tool 指定?
  │     └─ _execute_forced_tool(force_tool, ...) → 执行指定 skill
  │
  ├─ _should_launch_generate_video_directly()?
  │     ├─ 条件 A: session_context["auto_mode"]==True 且存在已选素材
  │     └─ 条件 B: 消息包含 "/generate" 或 "generate_video"
  │         └─ _execute_forced_tool("generate_video", ...)
  │
  ├─ _route_runtime_skill()  ← LLM 辅助路由
  │     ├─ 从 ToolRegistry 获取可用 skill 列表
  │     ├─ _score_runtime_skill_candidates()：基于关键词打分
  │     │     ├─ 高分且超过阈值 → 直接选中
  │     │     └─ 分数接近 → 召集 LLM 裁决
  │     └─ _route_skill_candidates_with_llm()：LLM 在候选 skill 中选择
  │
  ├─ selected_tool_name 有值? → _execute_selected_tool()
  │
  └─ fallback → _stream_direct_llm_reply()（普通对话模式）
                 或 tool_mode（LLM 自主调用注册工具）
```

### 4.2 Runtime Skill 体系

目录: `agents/skills/`

| Skill 名 | 声明文件 | runtime 文件 | 触发条件 |
|---------|---------|------------|---------|
| `generate_video` | `generate_video.py` | `generate-video/runtime.py` | 存在已选素材 + 消息含视频生成意图 |
| `analyze_video` | `analyze_video.py` | `analyze-video/runtime.py` | 存在参考视频 + 消息含分析意图 |
| `replicate_video` | `replicate_video.py` | `replicate-video/runtime.py` | 存在参考视频 + 消息含复刻意图 |

**generate_video skill 的触发条件（`_should_launch_generate_video_directly`）**:
```
tool = registry.get_tool("generate_video")
tool 可用（has_selected_materials=True 或类似条件）
AND (
    消息含 "/generate" 或 "generate_video"
    OR _looks_like_video_generation_request()：
        - 消息含"素材/这些图/这些图片/参考图" AND 含"请/帮我/生成/制作"
)
```

**generate_video runtime** 调用链:
`launch_pipeline_task()` → `_run_pipeline()` → `LangGraphPipelineExecutor.run()`（主流水线）

**analyze_video runtime** 调用链:
查询 `VideoUpload` → `llm_service` 调用 Qwen 视频理解（有缓存则直接返回 `upload.analysis_report`）

**replicate_video runtime** 调用链:
`_run_pipeline()` 带 `reference_video_id` → 路由到 `ReplicationPlannerAgent`

---

### 4.3 Skill 加载机制

`agents/skills/loader.py` 在启动时扫描 `agents/skills/*/` 子目录，读取每个 skill 的声明文件（`generate_video.py` 等）获取 name、description、input_schema、routing_hints，注册到 `ToolRegistry`。runtime 实现由 `create_*_skill()` 工厂函数返回，通过闭包注入 executor、db_factory 等依赖。

---

## 5. RAG 与 Mem0 接入点

### 5.1 RagService

文件: `services/rag_service.py`

| 使用位置 | 操作 | 目的 |
|---------|------|------|
| `OrchestratorAgent.execute` | `rag_service.retrieve_similar(requirement, platform, limit=3)` | 检索相似历史生成方案，作为 few-shot context 注入 LLM prompt |
| 流水线完成后（router 层） | `rag_service.index_pipeline_run(...)` | 将完成的方案写入向量库供下次检索 |

**Qdrant Collection**: `vidgen_pipeline_plans`
- 向量维度: 1024（`text-embedding-v3`）
- payload 字段: `project_id`、`requirement`、`platform`、`style`、`orchestrator_plan_summary`、`overall_score`
- 嵌入模型: Qwen `text-embedding-v3`（通过 Qwen Embeddings API）
- 兜底: Qdrant 或 API 不可用时返回 `[]`，不阻断流水线

---

### 5.2 Mem0 接入点

文件: `services/mem0_service.py`，通过 `AgentContext.mem0` 传递

| 使用位置 | 操作 | 存储/检索的内容 |
|---------|------|--------------|
| `OrchestratorAgent.execute` | `mem0.search("video style preference for {platform}", user_id, limit=3)` | 检索用户历史视频风格偏好 |
| `OrchestratorAgent.execute`（成功后） | `mem0.add_explicit("用户在{platform}做{video_type}类型视频，{n}张素材，风格{style}", user_id)` | 记录本次执行上下文 |
| `ChatAgent.chat_stream` | `mem0.search(user_message, user_id, limit=N)` | 检索与当前消息相关的会话记忆 |
| `ChatAgent._build_system_prompt` | 将 memories 拼入系统 prompt | 让 LLM 感知用户历史偏好 |

Mem0 底层使用 Qdrant 作为向量存储（`mem0ai` SDK 内部管理，collection 名由 SDK 自动创建）。

---

### 5.3 Qdrant Collection 汇总

| Collection | 管理方 | 维度 | Payload 字段 | 用途 |
|-----------|--------|------|------------|------|
| `vidgen_pipeline_plans` | `RagService` 直接管理 | 1024 | project_id, requirement, platform, style, orchestrator_plan_summary, overall_score | 检索相似历史生成方案（OrchestratorAgent few-shot） |
| `mem0_*`（SDK 自动命名） | `mem0ai` SDK 管理 | 由 SDK 决定 | memory, user_id, metadata | 用户风格偏好和会话记忆 |
