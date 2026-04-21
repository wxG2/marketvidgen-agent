# 数据库模型说明

本目录下每个文件对应一组 SQLAlchemy ORM 模型，映射到数据库中的具体表。以下按业务域分组说明各表存储的数据含义。

---

## 用户与认证

### `user.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `User` | `users` | 系统用户账号，存储用户名、密码哈希、角色（`user`/`admin`）及是否激活状态 |
| `UserSession` | `user_sessions` | 用户登录会话，存储 session token 哈希值和过期时间，用于鉴权 |

---

## 核心业务

### `project.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `Project` | `projects` | 视频项目，是整个系统的核心工作单元。记录项目名称和当前所处步骤（`current_step`），其他所有业务数据都通过 `project_id` 关联到项目 |

### `prompt.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `PromptMessage` | `prompt_messages` | 项目内的人机对话历史，每条消息有 `role`（`user`/`assistant`）和内容，用于记录用户与 AI 的交互过程 |
| `Prompt` | `prompts` | 最终确定的视频生成提示词，关联到具体的素材选择方案，作为 AI 生成视频的输入 |

### `material.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `Material` | `materials` | 素材库，存储上传的图片、视频、音频等媒体文件的元信息（路径、尺寸、时长、缩略图等）。`category` 字段区分素材类型，`media_type` 区分媒体格式 |

### `material_selection.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `MaterialSelection` | `material_selections` | 记录某个项目选择了哪些素材，以及每条素材在选择列表中的排序位置（`sort_order`）。一个素材在同一项目中只能被选一次（唯一约束） |

---

## 视频生成流水线

### `pipeline.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `PipelineRun` | `pipeline_runs` | 一次完整的视频生成任务运行记录。包含：运行状态（`pending/running/completed/failed/cancelled` 等）、使用的引擎（`pipeline`/`langgraph`）、输入配置（JSON）、当前执行到的 Agent、最终视频路径、错误信息、重试次数，以及用于断点续跑的 `artifacts_snapshot` |
| `AgentExecution` | `agent_executions` | `PipelineRun` 下每个 Agent 的单次执行记录，记录 Agent 名称、输入/输出数据（JSON）、执行时长、尝试次数等，是 pipeline 的执行明细 |

### `generated_video.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `GeneratedVideo` | `generated_videos` | AI 生成的视频片段记录。支持两种生成类型：`image_to_video`（图生视频，调用 Kling）和 `talking_head`（数字人口播）。记录任务状态、视频文件路径、缩略图、时长，以及是否被选中（`is_selected`）用于最终剪辑 |

### `talking_head.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `TalkingHeadTask` | `talking_head_tasks` | 数字人视频生成的分步任务。分为四个阶段：A 输入人物照片和背景素材 → B 合成人物+背景的合成图（`composite`）→ C 配置动作提示词和音频片段 → D 生成对口型视频（`lipsync`）。每步有独立的状态字段 |

### `video_upload.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `VideoUpload` | `video_uploads` | 用户上传的参考视频文件记录，存储文件路径、大小、时长等信息，可关联到 AutoChat 会话用于风格参考 |

### `video_analysis.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `VideoAnalysis` | `video_analyses` | 对 `VideoUpload` 进行 AI 分析的结果，包括内容摘要、场景标签、推荐素材分类等，用于辅助素材推荐和 prompt 生成 |

---

## 时间线与剪辑

### `timeline.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `TimelineAsset` | `timeline_assets` | 用户手动上传到时间线编辑器的本地文件（视频/音频/字幕），区别于 `Material`（素材库），这里是针对特定项目的一次性上传 |
| `TimelineClip` | `timeline_clips` | 时间线上已排布的片段，记录每个片段在哪条轨道（`track_type`：video/audio/subtitle）、哪个位置（`position_ms`）、持续多长（`duration_ms`）。片段来源可以是 `GeneratedVideo` 或 `TimelineAsset` |

---

## Agent 框架（LangGraph）

`agent_state.py` 存储 LangGraph Agent 运行时的完整状态，是 `pipeline.py` 中旧版 Agent 执行记录的替代和升级。

| 类 | 表名 | 说明 |
|---|---|---|
| `AgentThread` | `agent_threads` | 对话线程，一个线程对应一次完整的 Agent 交互上下文（`chat`/`task`/`pipeline` 三种类型） |
| `AgentRun` | `agent_runs` | 线程内的一次 Agent 运行实例，记录运行状态、当前执行节点、用于断点续跑的 `resume_token` |
| `AgentMessage` | `agent_messages` | 线程内的消息历史，role 包含 `system/user/assistant/tool` |
| `AgentStep` | `agent_steps` | 一次 Run 的执行步骤明细，按 `step_index` 有序，记录每步的输入/输出/耗时 |
| `AgentCheckpoint` | `agent_checkpoints` | 运行检查点，快照 Agent 状态（`state_json`），供服务重启后从断点恢复 |
| `ToolCall` | `tool_calls` | Agent 调用工具的完整记录，包括参数（`arguments_json`）和结果（`result_json`） |
| `PromptVersion` | `prompt_versions` | 系统 prompt 的版本管理，每个 prompt 名称可以有多个版本，供 `ModelCall` 关联追踪 |
| `ModelCall` | `model_calls` | 每次调用 LLM 的详细记录，包括模型名、token 用量、延迟、完整请求和响应 JSON |
| `RunEvent` | `run_events` | 运行过程中产生的事件流水，供前端实时推送和日志审计使用 |
| `RetrievalDocument` | `retrieval_documents` | 向量检索文档索引，记录各业务表数据的向量化状态（embedding 版本、向量 ID 等），支持 RAG 检索 |

### `agent_memory.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `AgentMemory` | `agent_memories` | Agent 的跨会话持久化记忆，按 `namespace_key + memory_key` 唯一标识一条记忆。`scope` 支持 `conversation/session/user/organization` 四级，可设置重要度（`importance`）和过期时间（`expires_at`） |

---

## AutoChat 自动对话

### `auto_chat.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `AutoChatSession` | `auto_chat_sessions` | AI 辅助创作的对话会话，不仅存储对话状态，还存储本次生成的所有参数配置：目标平台（抖音/小红书/B站）、是否静音、转场效果、BGM 风格、水印等。是 pipeline 运行的参数来源 |
| `AutoChatMessage` | `auto_chat_messages` | 会话内的消息记录，支持结构化 `payload_json` 用于携带进度更新、预览数据等富文本内容 |
| `AutoSessionMaterialSelection` | `auto_session_material_selections` | 会话维度的素材选择（区别于项目维度的 `MaterialSelection`），记录该会话选用了哪些素材及其排序 |

---

## 背景模板

### `background_template.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `BackgroundTemplate` | `background_templates` | 品牌/角色/风格的预设模板，存储品牌信息、角色名称与身份设定、场景语境、语气风格、视觉风格等结构化字段，作为 prompt 生成的背景知识。`learned_preferences` 字段存储从历史生成中自动学习到的偏好 |
| `BackgroundTemplateLearningLog` | `background_template_learning_logs` | 模板自动学习的变更日志，每次从生成结果中更新模板时，记录更新前快照、变更 patch 和更新后快照，保留完整的学习历史 |

---

## 社交发布

### `social_account.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `SocialAccount` | `social_accounts` | 用户绑定的第三方社交账号，目前支持 `douyin`。存储 OAuth 凭证（`access_token`/`refresh_token`）、账号信息（头像、昵称）和授权 scope |

### `video_delivery.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `VideoDelivery` | `video_deliveries` | 视频投递记录，对应一次"保存"或"发布"操作。`platform` 支持 `repository`（保存到本地仓库）和 `douyin`（发布到抖音），记录外部平台返回的任务 ID、URL 及发布状态 |

---

## 辅助与工具

### `repository_asset.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `RepositoryAsset` | `repository_assets` | Pipeline 运行产生的中间产物仓库，`asset_key` 标识产物类型（如 `prompt_engineer.shot.0`、`audio_subtitle.audio`、`video_generator.shot.0`），内容可以是文件（`file_path`）或纯文本（`text_content`）。`prompt_engineer`、`audio_subtitle`、`video_generator` 成功后会自动写入，供自动模式右侧栏和个人仓库 Agent 产物页展示 |

### `model_image.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `ModelImage` | `model_images` | 数字人口播功能使用的人物照片，按项目归档，存储图片文件路径和尺寸信息 |

### `usage.py`

| 类 | 表名 | 说明 |
|---|---|---|
| `ModelUsage` | `model_usages` | LLM Token 用量统计（旧版），按 `pipeline_run_id` + `agent_name` 汇总用量。新版已由 `agent_state.py` 中的 `ModelCall` 替代，提供更细粒度的单次调用追踪 |

---

## 表关系概览

```
User
 ├── UserSession
 ├── Project
 │    ├── PromptMessage
 │    ├── Prompt ──────────── MaterialSelection ── Material
 │    ├── ModelImage
 │    ├── VideoUpload ──────── VideoAnalysis
 │    ├── GeneratedVideo ───── TalkingHeadTask
 │    ├── TimelineAsset
 │    ├── TimelineClip
 │    └── PipelineRun ──────── AgentExecution
 │                        └── RepositoryAsset
 ├── AutoChatSession ──────── AutoChatMessage
 │                       └── AutoSessionMaterialSelection
 ├── BackgroundTemplate ───── BackgroundTemplateLearningLog
 ├── SocialAccount
 ├── AgentMemory
 └── AgentThread ─────────── AgentRun ── AgentStep
                                    ├── AgentMessage
                                    ├── AgentCheckpoint
                                    ├── ToolCall
                                    ├── ModelCall
                                    └── RunEvent
```
