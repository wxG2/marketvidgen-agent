# vidgen Agent 系统架构与功能说明

本文档基于 2026-04-07（CST, UTC+8）对当前 `vidgen` 代码的实际梳理编写，聚焦说明系统整体架构、Agent 编排、核心模块、主要流程和当前功能范围，仅描述代码中已经体现出来的能力。

## 0. 文档版本与变更时间（精确到小时）

- 最后全量同步时间：`2026-04-07 CST`（UTC+8）。
- 时间口径说明：以下时间是”文档与代码对齐确认时间”，不是每一处代码首次提交时间。
- `2026-04-07`（本次）：新增 Agent 核心能力补强、可靠性与容错、安全与权限三个模块，详见第 14 节。
- `2026-04-07`：全量同步当前 `vidgen` Agent 系统实现，补充 Orchestrator 的三条主路径（图文生成 / 参考视频解析 / 视频复刻）、意图分类逻辑、时长可行性校验、会话级素材分配、`analysis_only` 返回链路、确认后恢复执行、单 Agent 重试续跑，以及三种编排引擎的真实职责边界。
- `2026-04-02 19:05`：同步自动模式“项目内多会话”能力（左栏按会话展示、新建会话、会话切换与会话级状态恢复）。
- `2026-04-02 19:05`：同步复刻确认链路（确认执行 / 调整方案 / 直接终止），终止后保留已展示的复刻方案消息。
- `2026-04-02 19:05`：同步复刻消息增强（assistant 消息展示参考视频卡片、灰字解析进度、关键帧预览卡片、解析报告）。
- `2026-04-02 19:05`：同步复刻理解路径（优先整视频直传模型理解，再结合关键帧；失败回退仅关键帧分析）。
- `2026-04-02 19:05`：同步仓库策略（成片自动入仓；参考视频上传后进入上传仓库记录并可在仓库 uploads 视图查看）。
- `2026-04-02 19:05`：同步抖音发布链路（用户级 OAuth 授权、assistant 自动生成发布草稿、用户确认后再提交发布）。
- `2026-04-02 19:05`：同步主视觉理解模型默认值（`QWEN_OMNI_MODEL=qwen3.5-flash`）与相关配置说明。
- `2026-04-02 19:11`：联网核对官方模型后，将视觉理解主模型切换为 `QWEN_OMNI_MODEL=qwen3-omni-flash` 并同步更新配置与文档示例。
- `2026-04-02 19:15`：完成系统说明文档全量复核与时间线刷新，确保当前描述与代码配置一致。
- `2026-04-02 20:29`：同步复刻链路容错增强；当模型返回异常结构（如把 `audio_design`、`music_design` 或 `shots` 返回成数组）时，后端会自动清洗并跳过坏数据，避免因 `AttributeError('list' object has no attribute 'get')` 导致整个复刻分析节点失败。
- `2026-04-03 11:02`：同步复刻 skill 调度改造；上传参考视频只会把视频挂载到当前会话，不再自动触发复刻。只有当用户输入明确包含“复刻 / 同款 / 按这个视频做”等意图时，调度 Agent 才会选择视频复刻 skill。

## 1. 系统定位

`vidgen` 当前是一个面向短视频生产的 AI 工作台。前端产品体验仍以 `capy` 作为工作台形态呈现，但代码仓库、后端服务和文档层统一以 `vidgen` 为系统名。它并不只是单独调用某个视频模型，而是把素材管理、脚本生成、分镜规划、提示词设计、音频字幕、视频生成、最终合成、项目历史和消耗统计串成了一条完整工作流。

当前版本已经引入本地账号体系、项目级资源隔离、自动模式多会话工作台、个人中心角色背景模板库、参考视频复刻确认链路，以及成片后的多平台交付动作。

系统当前提供两种使用方式：

- `一键生成模式`
  用户选择素材并输入脚本后，系统自动执行整条视频生成流水线。
- `手动模式`
  用户按上传、分析、选素材、写提示词、生成、剪辑等步骤逐步完成视频制作。

## 2. 总体架构

vidgen 采用前后端分离架构，核心技术栈如下：

- 前端：`React 19 + TypeScript + Vite + Zustand + TanStack Query`
- 后端：`FastAPI + SQLAlchemy Async`
- 数据存储：`SQLite`（默认，通过 aiosqlite 驱动）
- 文件存储：本地文件系统
- 编排方式：`多 Agent 流水线`，支持三种执行引擎（`Pipeline`、`LangGraph`、`Swarm`）
- 多媒体处理：`FFmpeg`
- 外部模型服务：`Qwen（QWEN_OMNI_MODEL，当前默认 qwen3-omni-flash）`、`Qwen3 TTS`、`Qwen3-VL`、`Seedance`、`Kling v3`、`Flux Inpaint`、`LTX2.3` 等

可以把系统拆成 6 层：

1. `交互层`
   负责项目管理、自动模式、手动模式、时间轴、示例画廊和用量看板。
2. `会话层`
   负责自动模式多会话、消息流、会话级素材选择、参考视频绑定、草稿脚本和发布草稿持久化。
3. `编排层`
   负责自动生成链路中的 Agent 执行顺序、暂停/恢复、重试和状态流转，支持顺序执行、状态图和 Swarm 三种编排模式。
4. `能力层`
   负责 LLM、TTS、图生视频、视频剪辑、视频分析、图像合成、口型驱动等具体能力。
5. `状态层`
   负责记录项目、任务、素材、生成结果、agent 执行状态和模型用量。
6. `资产层`
   负责保存素材、上传文件、生成音频、字幕、视频片段和最终成片，并自动清理过期产物。

## 3. 前端架构

前端是一个单页应用，围绕项目进行组织，使用 Zustand 管理状态，TanStack Query 管理服务端数据。

### 3.1 状态管理

前端使用三个 Zustand Store：

- `projectStore`：管理当前项目上下文和步骤。
- `pipelineStore`：管理自动模式切换状态。
- `timelineStore`：管理时间轴编辑器状态。

### 3.2 项目入口

- 登录 / 注册本地账号
- 创建项目
- 打开最近项目
- 进入自动模式或手动模式
- 浏览示例画廊

### 3.3 自动模式工作台

自动模式是当前的一键生成主界面，包含以下能力：

- 左侧按会话展示自动模式历史，并支持切换到历史会话
- 新建自动模式会话
- 上传素材库或单张文件
- 上传参考视频作为会话级参考资料保存
- 选择角色 / 品牌背景模板
- 根据已选素材自动生成脚本
- 输入脚本并发起整条 pipeline
- 配置 pipeline 参数（目标平台、时长、风格、语音等）
- 会话级持久化保存脚本草稿、参考视频、背景模板、素材选择和当前运行状态
- 当只有参考视频且用户表达“解析 / 分析 / 总结 / 描述”类诉求时，只执行 Orchestrator 的视频解析分支，直接返回 `analysis_report`，不会继续生成视频
- 展示每个 agent 的执行状态和输出
- 通过 SSE 实时显示执行进度
- 在 assistant 消息下展示上传的参考视频卡片
- 只有当用户输入明确命中视频复刻意图时，调度 Agent 才会调用视频复刻 skill；仅上传参考视频不会自动启动复刻分析
- 在复刻解析过程中，把 Agent 的阶段性分析进度以灰字方式持续追加到消息流
- 在复刻消息中展示关键帧预览和解析报告，便于用户确认复刻依据
- 在复刻模式下展示镜头级复刻方案并等待用户确认 / 调整
- 在复刻模式下，对模型返回的异常方案结构做后端容错清洗，尽量继续产出可确认方案，而不是直接失败
- 在等待确认阶段支持直接终止当前流程，避免会话长期卡在待确认状态
- 展示最终视频和中间产物
- 在运行中按 agent 展示过程面板，查看每一步的输入输出、报错和重试结果
- 展示抖音、YouTube 平台卡片视频预览
- 成片完成后自动保存到本地视频仓库
- 上传参考视频后自动进入仓库上传记录（可在仓库 uploads 列表查看）
- 查看当前账号已连接的抖音发布账号
- 在已连接抖音账号后，自动生成抖音发布草稿，并在 assistant 消息中展示可编辑的确认卡片
- 用户确认后按所选抖音账号提交发布
- 查看每个 pipeline 节点的预览

当前自动模式工作台的整体视觉已切换为更统一的 `capybara` 风格：暖沙色背景、卡片化面板、统一的角色 / 工作台视觉语言。

当前自动模式的左栏已经不再按单条消息堆叠，而是按“会话”组织。每个会话对应一个独立的自动生成上下文，包含消息流、参考视频、已选素材、背景模板、草稿脚本、视频参数、当前运行状态和交付记录。

### 3.4 手动模式工作流

手动模式把视频生产拆成 7 个步骤：

1. 上传参考视频
2. 执行视频分析
3. 浏览和选择素材
4. 编辑提示词（支持对话式交互）
5. 生成视频片段
6. Talking Head 工作流
7. 进入时间轴剪辑

### 3.5 仪表盘与个人中心

系统前端包含项目级仪表盘和个人中心。

项目仪表盘用来查看：

- pipeline 执行状态
- 当前正在运行的 agent
- 按 agent/provider/model 维度的 token 消耗
- 请求次数
- 历史运行记录及产物

个人中心当前包含：

- 角色背景模板库管理
- 预设角色模板以图标卡形式浏览和切换
- 用户输入关键词后，由 AI 自动生成角色背景草稿
- 仅展示当前选中的角色背景信息，方便确认后用于自动模式
- 预设角色模板导入
- 模板学习记录查看
- 管理员账号启用 / 禁用

### 3.6 UI 基础组件

- `Toast`：全局通知提示
- `ErrorBoundary`：错误边界
- `Skeleton`：加载骨架屏

## 4. 后端架构

后端采用 FastAPI，按职责分为 `routers`、`agents`、`services`、`models`、`schemas`、`prompts` 六类模块。

### 4.1 Router 层

Router 层负责暴露业务 API，当前主要包含以下路由模块：

- `auth`
  用户注册、登录、登出、获取当前账号信息、管理员账号管理。
- `background_templates`
  背景模板增删改查、学习记录读取、预设模板导入，以及基于关键词的角色背景信息自动生成。

- `projects`
  项目创建、读取、更新、删除、查看项目用量和历史。
- `upload`
  上传参考视频、流式传输，并支持把上传视频绑定到指定自动模式会话。
- `analysis`
  触发视频分析、获取分析结果。
- `materials`
  素材扫描、上传、分类浏览、项目选图。
- `auto_sessions`
  自动模式会话列表、会话详情、消息持久化、会话级素材选择、默认会话初始化与旧数据回填，以及会话级抖音发布草稿生成。
- `social_accounts`
  第三方平台账号管理，目前已实现抖音 OAuth 授权回调、账号列表、默认账号切换、账号删除和 token 刷新。
- `prompts`
  提示词对话、编辑、绑定和读取。
- `generation`
  手动模式下的视频生成、轮询和管理。
- `pipeline`
  自动模式下的整条一键生成流程、SSE 进度推送、agent 重试、成片交付预览、自动保存到仓库、按用户授权抖音账号发布，以及与自动模式会话的 `session_id` 关联。
- `timeline`
  时间轴数据读写、时间轴资产上传和读取。
- `talking_head`
  Talking Head 四步工作流（模型图上传 → 图像合成 → 音频段选择 → 口型驱动）。
- `examples`
  示例画廊。

### 4.2 Agent 层

Agent 层负责自动模式中的多阶段生成流程。

#### 4.2.1 基础架构

- `BaseAgent`（抽象基类）：提供模板方法模式，封装数据库追踪、计时和错误处理。
- `AgentContext`：包含 `trace_id`、`pipeline_run_id`、`project_id`、`artifacts`、`usage_recorder`，并扩展 `task_board`、`shared_workspace`、`events`、`cancelled` 等运行态字段，供 Swarm、多阶段恢复和观测链路复用。
- `AgentResult`：封装 success 标志、output_data、error 和 usage_records。
- `BaseAgent.run(...)`：统一负责更新 `PipelineRun.current_agent`、创建 `AgentExecution`、记录 `attempt_number`、落库 `output_data/error_message/duration_ms`，并通过 `UsageRecorder` 记账。
- `AgentContext.report_progress(...)`：允许运行中的 Agent 把阶段性文本写入 `AgentExecution.progress_text`，前端据此流式显示灰字进度。

#### 4.2.2 核心 Agent

当前主链路包含以下 5 个 agent：

- `OrchestratorAgent`
  系统入口 Agent，承担三类路径：
  1. 普通图文生成：校验素材与时长可行性、估算脚本口播长度、按支持时长分配镜头、结合图片和脚本产出 `orchestrator_plan`。
  2. 参考视频解析：当用户只有“分析/总结/解释”诉求时，走 `analysis_only` 分支，输出结构化视频解析报告，不再进入后续视频生成。
  3. 视频复刻：当用户明确表达“复刻 / 同款 / 仿照 / 按这个视频做”等意图时，调用视频复刻 skill，提取关键帧、生成 `replication_plan` 与 `analysis_report`，并把 run 暂停在待确认态。
  它同时负责意图分类、参考视频路径解析、背景模板语义约束、会话级素材分配，以及对复刻模型返回的 `audio_design`、`music_design`、`shots` 做类型清洗与坏数据跳过，避免异常结构直接打断整条复刻链路。
- `PromptEngineerAgent`
  负责根据 `orchestrator_plan.shots` 和对应图片，生成每个镜头的英文视频提示词，以及整条视频的 `voice_params`（`voice_id`、`speed`、`tone`）。
- `AudioSubtitleAgent`
  负责调用 TTS 生成整段旁白音频，并基于音频生成对齐字幕文件。
- `VideoGeneratorAgent`
  负责基于图片和提示词并行生成每个镜头的视频片段，支持长轮询等待完成；在重试场景下也支持只重生成指定镜头索引。
- `VideoEditorAgent`
  负责最终重排、裁剪、拼接并合成音频、字幕、水印和 BGM。

#### 4.2.3 Orchestrator 的真实职责拆解

当前代码里的 `OrchestratorAgent` 不只是“拆分分镜”，而是自动模式里的统一调度入口，真实职责包括：

- `reference_video_id` 存在时先做意图判定：
  - 明确分析词命中时，直接返回 `analysis`
  - 明确复刻词命中时，直接返回 `replication`
  - 模糊请求时再调用 LLM 做二分类，且默认偏保守地落到 `analysis`
- 普通图文模式下做硬约束校验：
  - 素材张数与目标时长是否可行
  - 口播脚本长度与视频目标时长是否严重失配
  - 镜头时长是否落在模型支持集合中
- 固定时长模式下先分配镜头时长，再让模型只决定镜头内容，避免总时长漂移
- 复刻模式下优先上传整段视频给模型做全局理解，再通过 `extract_keyframes` 工具补充镜头级细节；如果整视频理解失败，会自动退回仅关键帧模式
- 复刻模式下把会话内已选素材按顺序读出，尝试分配到每个镜头，用于后续生成阶段复用用户素材
- 输出类型存在三种：
  - `orchestrator_plan`
  - `analysis_only + analysis_report`
  - `requires_confirmation + replication_plan + analysis_report`

#### 4.2.4 Pipeline 执行引擎

系统支持三种 pipeline 执行引擎，通过 `PIPELINE_ENGINE` 配置项切换：

**1. Pipeline（顺序执行）**

- 默认按 Orchestrator → Prompt Engineer → (Audio + Video 并行) → Editor 顺序执行。
- 支持 `waiting_confirmation` 暂停、`analysis_only` 直接完成、确认后 `resume_from_confirmation(...)` 续跑，以及通过 Router 触发单 Agent 重试后继续向下游推进。

**2. LangGraph（状态图编排）**

- 使用 LangGraph 的 `StateGraph` 实现 DAG 编排。
- 维护 `PipelineState` TypedDict 存储全部中间结果。
- 支持条件分支、并行边（Audio / Video）和异常中断；在当前实现里同样支持 `waiting_confirmation` 与 `analysis_only` 两种提前退出路径。

**3. Swarm（Agent 自主协调）**

- 引入 Lead Agent 作为协调者，使用 `SWARM_LEAD_SYSTEM_PROMPT` 做规划和决策。
- 维护 `SwarmTaskState` 任务板：每个任务有依赖、状态和产物键。
- 支持 `revise_plan`、`interim_reply`、`noop`、`done` 等决策动作。
- `SwarmRunController` 提供全局注册表、异步消息队列和 UI 状态快照同步。
- 每 2 秒做一次检查点同步。
- 任务完成、失败、人工输入和检查点都会重新触发 Lead Agent 决策，允许在运行中调整任务板而不是固定写死 DAG。

### 4.3 Service 层

Service 层封装具体能力，当前主要包括：

- `LLMService`
  提供结构化生成和对话能力，支持 Mock 和真实 Qwen 兼容服务（当前默认 `qwen3-omni-flash`），支持图片输入。
- `QwenClient`
  Qwen API 的封装层，提供结构化输出和视觉理解调用。
- `TTSService`
  提供文本转语音和字幕对齐能力，支持 Mock 和真实 Qwen3 TTS 服务。
- `VideoGenerator`
  提供图生视频能力，支持 Seedance 1.5 Pro、Kling v3 和 Mock 实现。
- `VideoEditorService`
  负责最终视频合成，支持 Mock 和真实 FFmpeg 实现。
- `VideoAnalyzer`
  负责参考视频分析，支持 Mock 和真实 Qwen3-VL 服务。
- `MaterialService`
  负责素材扫描、索引、分类管理、缩略图生成和删除。
- `ImageCompositor`
  负责图像合成能力，支持 Mock 和 Flux Inpaint 实现。
- `LipSyncGenerator`
  负责口型驱动能力，支持 Mock 和 LTX2.3 实现。
- `UsageRecorder`
  负责记录模型请求和 token 用量。
- `MediaUtils`
  提供文件本地化和平台特定的图片预处理（尺寸适配）。
- `ArtifactCleanup`
  自动清理过期生成产物（默认 7 天保留期），支持定期后台执行。
- `KeyframeExtractor`
  为参考视频理解和镜头拆解提供关键帧提取。
- `BackgroundTemplateLearning`
  在完整任务成功后，对绑定的背景模板做安全增量学习。
- `BackgroundTemplate Keyword Generation`
  根据用户输入的关键词，结合当前角色模板或最相近的预设角色，自动扩展出完整的角色背景信息；当真实 LLM 不可用时，会回退到基于预设模板的本地生成逻辑。
- `VideoDelivery`
  负责生成抖音 / YouTube 卡片预览元数据、保存成片到本地视频仓库、生成抖音发布草稿，以及在用户确认后调用抖音发布接口。
- `SocialAccounts`
  负责抖音 OAuth 授权地址生成、授权回调换取 token、账号信息更新、默认账号切换和过期 token 自动刷新。

### 4.4 Prompt 层

Prompt 层集中管理系统提示词，包括：

- 分镜规划 prompt（`ORCHESTRATOR_SYSTEM_PROMPT`）
- 提示词生成 prompt（`PROMPT_ENGINEER_SYSTEM_PROMPT`），规定 80-200 词、镜头运动、光影等细节
- 视频编辑决策 prompt（`VIDEO_EDITOR_SYSTEM_PROMPT`）
- QA 审核 prompt（`QA_REVIEWER_SYSTEM_PROMPT`），目前预留未接入 pipeline
- 视频复刻分析 prompt（`VIDEO_REPLICATION_SYSTEM_PROMPT`），用于关键帧分析、复刻方案生成，以及在无明确需求时按背景信息约束方案语义
- Swarm 协调 prompt（`SWARM_LEAD_SYSTEM_PROMPT`），规定任务创建、依赖管理和防循环规则

## 5. 自动模式主流程

自动模式是当前系统最完整的端到端链路，其执行流程如下：

1. 用户在前端选择素材并输入脚本
2. 用户可以先在左栏新建一个自动模式会话，或切换到历史会话继续编辑
3. 可选：绑定角色背景模板，或上传参考视频进入复刻模式
4. 自动模式会话会持续保存脚本草稿、参考视频、素材选择和视频参数
5. 普通图文模式下，前端调用 `preflight-check` 进行预检查
6. 前端发起 `pipeline` 创建请求，并把当前 `session_id` 一并传给后端
7. 后端创建一条 `PipelineRun`（记录引擎类型，并绑定所属自动模式会话）
8. 后端后台异步执行完整 pipeline
9. Orchestrator 先决定当前请求属于普通生成、参考视频解析还是视频复刻
10. 如果是 `analysis_only`，run 直接完成，前端把 `analysis_report` 渲染成 assistant 消息，不进入 Prompt / Audio / Video / Editor
11. 如果是复刻模式，Orchestrator 先生成镜头级复刻方案，前端展示确认卡片
12. 用户可在复刻确认阶段选择“确认执行 / 提交调整意见 / 直接终止本次流程”；确认后继续执行 Prompt / Audio / Video / Editor，提交调整意见则重新生成复刻方案，终止会把 run 标记为 `cancelled` 且保留已展示方案消息
13. 前端通过 SSE 持续监听任务状态
14. 任务结束后返回最终视频路径和各阶段执行记录
15. 成片完成后自动保存到视频仓库，并生成抖音 / YouTube 平台卡片预览
16. 如果当前平台为抖音且用户已连接抖音账号，系统会自动生成一条抖音发布草稿消息，预填标题、文案、话题和封面标题建议
17. 用户在草稿卡片中确认后，后端按所选抖音账号提交 `upload_video + create_video`
18. 支持对失败 agent 发起重试；重试完成后会按该 Agent 的下游依赖继续执行，而不是只重跑单点

自动模式会话切换时，前端会按会话详情接口恢复：

- 消息记录
- 参考视频
- 已选素材
- 背景模板
- 草稿脚本
- 平台 / 时长 / 是否保留原声 / 转场 / BGM / 水印等参数
- 当前 `PipelineRun`
- `AgentExecution` 历史
- 成片交付与仓库状态
- 已连接抖音账号、推荐发布账号和最近一次发布草稿

### 5.1 Orchestrator 阶段

输入信息包括：

- 脚本文案
- 选中的素材 ID
- 可选的参考视频 ID
- 可选的背景模板上下文
- 目标平台（支持 generic、douyin、xiaohongshu、bilibili）
- 目标时长
- 风格参数
- 语音参数

输出信息包括：

- 视频类型
- 每个镜头的脚本片段
- 每个镜头对应的素材图片路径
- 每个镜头的时长
- 在分析模式下输出 `analysis_only` 和 `analysis_report`
- 在复刻模式下输出 `replication_plan`、`analysis_report`、关键帧分析结果和等待确认状态

复刻模式下，系统会优先把“完整参考视频”直接输入模型做全局理解，再结合关键帧工具做镜头级细化；当视频直传链路异常时会自动回退为“仅关键帧分析”模式。若用户没有提供明确脚本或调整意见，但绑定了角色背景模板，则生成方案时会优先让镜头主体、场景和表达口径对齐背景信息，而不是只机械复用参考视频里的主体内容。

普通图文模式下，Orchestrator 还承担两个很重要的工程职责：

- 固定时长模式会先把目标总时长映射到模型支持的镜头时长集合，保证总时长严格可落地
- 在模型拆镜失败时，会回退到本地脚本切分逻辑，保证整条 pipeline 不因分镜 LLM 异常而完全不可用

当前前端在复刻模式下还会把上传的参考视频直接展示在 assistant 消息下方，并把 Orchestrator 的 `progress_text` 以灰字流式附加到同一条解析消息中，帮助用户看到“提取关键帧、分析镜头、组织执行方案”等中间过程。同时会在消息中展示提取出的关键帧预览卡片和解析报告文本，便于用户在确认前快速核对复刻依据。

### 5.2 Prompt Engineer 阶段

基于 Orchestrator 的输出，为每个镜头生成：

- 视频提示词（80-200 词，包含镜头运动、光影描述）
- 镜头时长
- 脚本片段
- TTS 语音参数（voice_id、speed、tone）

### 5.3 Audio Subtitle 阶段

根据完整脚本和语音参数生成：

- 配音音频文件
- 对齐字幕文件
- 音频时长

### 5.4 Video Generator 阶段

基于每个镜头的图片和提示词并行生成视频片段，输出内容包括：

- 镜头索引
- 视频片段路径
- 片段时长
- 第三方任务 ID

当前实现里这一层是最典型的长耗时节点，内部会为每个镜头单独提交生成任务，并持续轮询第三方服务状态。在失败重试场景下，支持只对指定 `shot_idx` 重新出片，同时复用其他未受影响镜头的已有结果。

### 5.5 Video Editor 阶段

将视频片段、音频和字幕进行最终合成，主要过程包括：

- 决定片段顺序
- 按时长裁剪片段
- 拼接全部片段
- 添加转场（xfade）
- 合成旁白音频
- 混入 BGM（按情绪选择）
- 烧录字幕
- 添加水印
- 输出最终视频
- 最终时长探测

编辑阶段对外只暴露一个 `final_video_path`，但内部上下文已经拿到了完整的 `video_clips_data`、`shot_prompts`、镜头时长、转场、BGM 和水印配置，因此这一层也是未来扩展“局部替换 / 局部重剪 / 质量复审”的主要挂点。

## 6. 手动模式流程

手动模式的目标是让用户逐步控制各个生产环节，主要能力如下：

### 6.1 上传参考视频

用户可以上传一段参考视频，系统会为该项目保存上传记录，支持拖拽上传和预览。上传后视频会进入仓库上传记录（`/api/repository/uploads`）并可在自动模式会话中绑定为当前参考视频。

### 6.2 视频分析

系统可基于上传的视频（通过 Qwen3-VL）生成：

- 视频摘要
- 场景标签
- 推荐素材分类

### 6.3 素材浏览与选择

用户可以：

- 扫描本地素材库（自动生成缩略图）
- 按分类查看素材
- 上传项目专属素材（支持批量）
- 为当前项目选择参与生成的素材

### 6.4 提示词工作区

用户可以通过对话式交互查看、生成、编辑和保存提示词，支持提示词与素材的绑定关系，为后续视频生成做准备。

### 6.5 视频生成

系统可根据提示词和对应素材，逐条生成视频结果，支持轮询等待和状态查看，并保存为生成记录。

### 6.6 Talking Head 工作流

Talking Head 是一个四步特殊流程：

1. 上传模型人物图片
2. 将人物图与背景图进行合成（Flux Inpaint）
3. 选择音频段和设置运动提示词
4. 生成口型驱动视频（LTX2.3）

### 6.7 时间轴剪辑

时间轴模块支持：

- 保存项目时间轴片段
- 管理视频轨、音频轨、字幕轨
- 片段重排、裁剪、定位
- 上传额外资产文件
- 引用生成结果和时间轴资产
- 按平台格式导出（抖音、小红书、B站）

## 7. 视频编辑与媒体处理能力

最终视频合成由 `VideoEditorService` 负责，主要能力包括：

- 片段本地化处理
- 平台特定的图片尺寸适配（generic 1280×720、douyin 720×1280、xiaohongshu 1080×1440、bilibili 1280×720）
- 按镜头时长裁剪
- 顺序重排
- 普通拼接
- `xfade` 转场拼接
- 音频长度适配
- BGM 混音（按情绪匹配）
- 字幕渲染与烧录
- 水印覆盖
- 最终时长探测

系统在媒体处理时主要依赖 FFmpeg，并结合少量图片渲染逻辑来处理字幕覆盖。

## 8. 模型与外部服务集成

当前代码中已经接入或预留的模型与服务包括：

- `Qwen（QWEN_OMNI_MODEL）`
  用于结构化规划、提示词生成和编辑决策，支持结构化 JSON Schema 输出；当前默认模型配置为 `qwen3-omni-flash`。
- `Qwen3 TTS`
  用于文本转语音和字幕对齐。
- `Qwen3-VL`
  用于视频分析和视觉理解。
- `Seedance 1.5 Pro`
  作为默认图生视频提供方，支持可配置的时长和分辨率。
- `Kling v3`
  作为另一套图生视频服务接入。
- `Flux Inpaint`
  用于 Talking Head 中的图像合成。
- `LTX2.3`
  用于 Talking Head 中的口型驱动。

系统通过 `USE_MOCK_*` 系列配置项决定启用真实服务还是 Mock 服务，因此同一套架构可以在开发态和真实服务态之间切换。

## 9. 数据模型与状态管理

系统通过数据库记录项目、任务和生成状态，核心模型如下：

- `Project`
  表示项目基本信息和当前步骤。
- `User` / `UserSession`
  表示本地账号体系、登录会话和管理员能力。
- `Material`
  表示素材库中的单个素材，支持 image/video 类型和标签。
- `MaterialSelection`
  表示项目和素材之间的选用关系，支持排序。
- `SocialAccount`
  表示用户已连接的第三方发布账号。当前已实现抖音账号，保存 `open_id`、展示名、头像、`access_token` / `refresh_token`、过期时间、scope、默认账号状态和同步时间。
- `AutoChatSession`
  表示自动模式中的一个会话实体，保存标题、状态摘要、草稿脚本、背景模板、参考视频、视频参数、当前运行 ID 和最近活跃时间。
- `AutoChatMessage`
  表示自动模式会话内的消息记录，支持文本内容、灰字进度、图片缩略图、参考视频卡片和抖音发布草稿卡片等附加 payload。
- `AutoSessionMaterialSelection`
  表示自动模式会话和素材之间的选用关系，用于隔离不同自动模式会话的素材上下文。
- `VideoUpload`
  表示用户上传的参考视频；在自动模式中可通过 `session_id` 绑定到指定会话。
- `BackgroundTemplate`
  表示用户可复用的角色 / 品牌背景模板，支持长期偏好学习。
- `BackgroundTemplateLearningLog`
  表示模板在任务完成后被 Agent 增量学习的历史记录。
- `VideoAnalysis`
  表示对参考视频的分析结果。
- `Prompt`
  表示生成或编辑后的提示词。
- `PromptMessage`
  表示提示词对话中的消息记录。
- `GeneratedVideo`
  表示手动模式中的视频生成记录，支持状态追踪和选择标记。
- `PipelineRun`
  表示自动模式中的整条任务执行，记录引擎类型、输入配置、Swarm 状态、Lead Agent 最新消息、当前执行 Agent、最终视频路径，并可通过 `session_id` 归属到某个自动模式会话。
- `AgentExecution`
  表示每个 agent 的执行记录，包含输入、输出、耗时、错误、重试次数和实时 `progress_text`。
- `VideoDelivery`
  表示成片交付动作记录，当前既用于“保存到仓库”，也用于“抖音发布草稿 / 提交发布”状态跟踪，已扩展 `social_account_id`、`draft_payload_json`、`external_status`、`platform_error_code`、`submitted_at`、`published_at` 等字段。
- `ModelUsage`
  表示模型请求次数和 token 消耗，按 provider/model/operation 维度。
- `TimelineClip`
  表示时间轴中的片段。
- `TimelineAsset`
  表示时间轴中上传的外部资源。
- `ModelImage`
  表示 Talking Head 使用的人物图。
- `TalkingHeadTask`
  表示 Talking Head 四步任务。

### 9.1 数据库迁移

- `001_initial_schema.py`（2026-03-23）：初始化全部核心表。
- `002_add_swarm_state_columns.py`（2026-03-30）：为 `pipeline_runs` 表添加 `engine`、`swarm_state_json`、`latest_lead_message` 字段，支持 Swarm 引擎。

此外，当前系统在应用启动时还会通过轻量级兼容迁移补齐部分历史列，例如：

- `pipeline_runs.user_id`
- `pipeline_runs.session_id`
- `video_uploads.session_id`
- `materials.user_id`
- `prompt_messages.user_id`
- `prompts.user_id`

自动模式多会话相关表（`auto_chat_sessions`、`auto_chat_messages`、`auto_session_material_selections`）和第三方账号表（`social_accounts`）在启动时由 SQLAlchemy 自动创建。对于历史项目，如果还没有自动模式会话，系统会自动生成一个 `默认会话`，并把旧的 `PipelineRun`、`VideoUpload` 以及项目级已选素材回填到这个默认会话中。`video_deliveries` 的新增草稿 / 账号字段也会在启动时通过轻量兼容迁移补齐。

## 10. 实时状态与可观测性

系统当前具备较完整的运行状态追踪能力，主要体现在：

- `PipelineRun`
  跟踪整条自动生成任务的状态（pending/running/completed/failed/cancelled），记录执行引擎类型。
- `AgentExecution`
  跟踪每个 agent 的输入、输出、耗时、错误、重试次数和 `progress_text`。
- `AutoChatSession`
  跟踪自动模式会话级状态，确保用户刷新页面或重新登录后仍可恢复到之前的工作上下文。
- `AutoChatMessage`
  跟踪自动模式消息流，支持 assistant 灰字解析过程、图片缩略图、参考视频卡片和抖音发布草稿卡片等 UI 展示所需的结构化数据。
- `SocialAccount`
  跟踪用户已连接的抖音账号、默认账号和授权有效期，供自动模式和交付面板复用。
- `VideoDelivery`
  跟踪仓库保存、抖音草稿生成和抖音发布提交状态，并保存平台返回的外部 ID / 状态 / 错误码。
- `ModelUsage`
  跟踪每次模型调用的 provider、model、operation 和 token 消耗。
- `SSE Stream`
  让前端可以实时看到 pipeline 的状态变化。
- `Swarm 状态快照`
  Swarm 引擎下，通过 `SwarmRunController` 维护实时状态快照和异步消息队列；快照会同步到 `pipeline_runs.swarm_state_json`，Lead 的最近一条用户可见总结会落到 `latest_lead_message`。
- `Project History`
  让前端可以查看项目历史运行结果和相关产物。

## 11. 系统输出产物

系统在不同流程中会生成多类中间产物和最终产物，包括：

- 分镜计划
- 每个镜头的视频提示词
- 语音参数
- 参考视频解析报告
- 复刻解析报告
- 关键帧图片
- 音频文件
- 字幕文件
- 视频片段
- 合成图像（Talking Head）
- 口型驱动视频
- 最终合成视频
- 仓库上传记录（参考视频）
- 仓库交付记录（成片保存）
- 抖音发布草稿与提交记录
- 时间轴资产
- 项目历史记录
- 模型消耗统计

这些产物既用于前端展示，也用于后续编辑、下载和历史回看。过期产物会被自动清理（默认 7 天保留期）。

## 12. 配置与部署

### 12.1 关键配置项

- `PIPELINE_ENGINE`：执行引擎选择（`pipeline` | `langgraph` | `swarm`，默认 `langgraph`）
- `QWEN_API_KEY` / `QWEN_API_URL`：Qwen 模型服务
- `QWEN_OMNI_MODEL`：主 LLM / 视觉理解链路使用的模型名（当前默认 `qwen3-omni-flash`）
- `QWEN_TTS_MODEL`：语音合成模型名（当前默认 `qwen3-tts-flash`）
- `KLING_API_KEY` / `WAVESPEED_API_KEY`：视频生成服务
- `SEEDANCE_MODEL`：Seedance 图生视频模型标识（当前默认 `doubao-seedance-1-5-pro-251215`）
- `IMAGE_COMPOSITOR_API_KEY` / `LTX_API_KEY`：Talking Head 相关服务
- `DOUYIN_CLIENT_KEY` / `DOUYIN_CLIENT_SECRET` / `DOUYIN_REDIRECT_URI`：抖音 OAuth 授权与发布配置
- `DOUYIN_DEFAULT_SCOPE`：抖音默认授权 scope
- `FRONTEND_BASE_URL`：抖音授权完成后前端工作台的基础地址
- `PLATFORM_RESOLUTIONS`：平台分辨率映射（generic、douyin、xiaohongshu、bilibili）
- `USE_MOCK_*` 系列：开发态 Mock 开关（analyzer、llm、generator、tts、editor、compositor、lipsync）
- 目录配置：`UPLOAD_DIR`、`MATERIALS_ROOT`、`GENERATED_DIR`、`BGM_DIR`、`WATERMARKS_DIR`、`THUMBNAILS_DIR`

### 12.2 生命周期管理

系统启动时通过 FastAPI lifespan 完成数据库初始化和定期清理任务的注册。

## 13. 关键代码入口

如果后续需要继续查看系统实现，建议优先阅读以下文件：

### 后端核心

- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/database.py`

### Agent 与 Pipeline

- `backend/app/agents/base.py`
- `backend/app/agents/pipeline.py`
- `backend/app/agents/langgraph_pipeline.py`
- `backend/app/agents/swarm_pipeline.py`
- `backend/app/agents/swarm_runtime.py`
- `backend/app/agents/orchestrator.py`
- `backend/app/agents/prompt_engineer.py`
- `backend/app/agents/audio_subtitle.py`
- `backend/app/agents/video_generator_agent.py`
- `backend/app/agents/video_editor.py`

### 服务层

- `backend/app/services/llm_service.py`
- `backend/app/services/qwen_client.py`
- `backend/app/services/social_accounts.py`
- `backend/app/services/video_delivery.py`
- `backend/app/services/video_generator.py`
- `backend/app/services/video_editor_service.py`
- `backend/app/services/tts_service.py`
- `backend/app/services/video_analyzer.py`
- `backend/app/services/material_service.py`
- `backend/app/services/image_compositor.py`
- `backend/app/services/lipsync_generator.py`
- `backend/app/services/media_utils.py`
- `backend/app/services/artifact_cleanup.py`

### 路由层

- `backend/app/routers/auto_sessions.py`
- `backend/app/routers/pipeline.py`
- `backend/app/routers/social_accounts.py`
- `backend/app/routers/generation.py`
- `backend/app/routers/materials.py`
- `backend/app/routers/talking_head.py`
- `backend/app/routers/examples.py`

### 新增模块（2026-04-07）

- `backend/app/agents/qa_reviewer.py`
- `backend/app/agents/tool_registry.py`
- `backend/app/models/agent_memory.py`
- `backend/app/services/agent_memory.py`
- `backend/app/services/input_validator.py`

### 提示词

- `backend/app/prompts/system_prompts.py`

### 前端核心

- `frontend/src/App.tsx`
- `frontend/src/api/autoSessions.ts`
- `frontend/src/components/pipeline/AutoModeStudio.tsx`
- `frontend/src/components/pipeline/PipelineLauncher.tsx`
- `frontend/src/components/pipeline/PipelineProgress.tsx`
- `frontend/src/components/talking-head/TalkingHeadPanel.tsx`
- `frontend/src/stores/projectStore.ts`
- `frontend/src/stores/pipelineStore.ts`
- `frontend/src/stores/timelineStore.ts`

## 14. 新增能力模块（2026-04-07）

本节记录本次新增的三个模块：Agent 核心能力补强、可靠性与容错、安全与权限。

### 14.1 Agent 核心能力补强

#### 14.1.1 QA 审核 Agent

新增 `QAReviewerAgent`（`backend/app/agents/qa_reviewer.py`），在 `VideoEditorAgent` 之后自动执行成片质量审核。

审核采用双层机制：

- **硬编码规则检查**（始终执行，不依赖 LLM）：
  - 镜头覆盖率：检测有无缺失视频片段（`missing_clips`）
  - 时长合规：成片时长与目标时长误差超过 30% 时告警，超过 50% 时判定为 `critical`
  - 音视频同步：音频与视频总时长差超过 5 秒时告警
  - 提示词质量：检查所有镜头是否均缺少镜头运动关键词（dolly / pan / push / zoom / tilt / track）
- **LLM 语义审核**（调用 `QA_REVIEWER_SYSTEM_PROMPT`）：
  - 检查脚本覆盖度、平台合规性，综合给出 `overall_score`（0–1）

输出结构：

```json
{
  "passed": true | false,
  "overall_score": 0.0–1.0,
  "issues": [{"severity": "critical|warning|info", "category": "...", "message": "..."}],
  "recommendation": "pass | retry_video_generator | retry_audio | retry_editor"
}
```

当 `passed = false` 且 `QA_AUTO_RETRY_ENABLED = true` 时，pipeline 自动按 `recommendation` 重跑对应上游 Agent，最多重试 `MAX_QA_RETRIES` 次（默认 1 次）后强制交付。QA 审核可通过 `QA_REVIEW_ENABLED = false` 完全关闭。

相关新配置项：`QA_REVIEW_ENABLED`（默认 `true`）、`QA_AUTO_RETRY_ENABLED`（默认 `true`）、`MAX_QA_RETRIES`（默认 `1`）。

#### 14.1.2 Agent 跨 Run 记忆（AgentMemory）

新增 `AgentMemoryService`（`backend/app/services/agent_memory.py`）和 `AgentMemory` 数据模型（`backend/app/models/agent_memory.py`），为 Agent 提供跨流水线 run 的持久化用户级记忆。

`agent_memories` 表结构：`user_id`、`key`（点号分隔命名空间）、`value_json`（任意 JSON）。

内置语义接口：

- `remember_voice_params / recall_voice_params`：保存 / 读取最近一次成功的 TTS 语音参数
- `remember_platform_style / recall_platform_style`：保存 / 读取平台专属提示词风格偏好
- `remember_shot_duration_pattern / recall_shot_duration_pattern`：保存 / 读取成功的镜头时长分配模式

`AgentContext` 新增 `user_id`、`memory_service` 字段，各 Agent 可在 `execute()` 中通过 `context.memory_service` 读写记忆。

相关新配置项：`AGENT_MEMORY_ENABLED`（默认 `true`）。

#### 14.1.3 Tool Registry（Agent 工具注册表）

新增 `ToolRegistry`（`backend/app/agents/tool_registry.py`），为 Agent 提供运行时动态工具发现与调用基础设施。

核心概念：

- `ToolDefinition`：描述单个工具（name、description、async fn、optional required_permission）
- `ToolRegistry.register / grant_permission / list_tools / invoke`

`AgentContext` 新增 `tool_registry` 字段。Pipeline executor 可在启动时注册工具并为各 Agent 授权，Agent 在 `execute()` 中通过 `context.tool_registry.invoke(tool_name, agent_name=self.name, ...)` 调用已授权工具。当前作为基础设施提供，各 Agent 的具体工具接入在后续迭代中逐步推进。

#### 14.1.4 Human-in-the-Loop 增强（Prompt 审核）

在普通图文生成模式下，PromptEngineer 完成后支持暂停等待用户审核镜头提示词方案。

新增 pipeline 状态值 `waiting_prompt_review`，触发条件：`input_config.review_prompts = true` 或全局配置 `HUMAN_IN_LOOP_PROMPT_REVIEW = true`（默认 `false`）。

暂停时，`orchestrator_plan` 和 `prompt_plan` 已持久化到 checkpoint，前端可展示镜头级提示词供用户修改。用户确认后通过 `resume_from_prompt_review()` 继续执行 Audio / Video / Editor / QA 阶段。

---

### 14.2 可靠性与容错

#### 14.2.1 断点 Checkpoint

`AgentContext` 新增 `save_checkpoint()` 方法，每个 Agent 完成后将 `artifacts` 字典序列化并写入 `pipeline_runs.artifacts_snapshot` 列（新增，兼容迁移在启动时自动补列）。

`AgentContext.restore_checkpoint()` 类方法可按 `pipeline_run_id` 读取最后一次快照，供后续实现"从上次成功节点续跑"功能使用。

两个 pipeline executor（`PipelineExecutor` 和 `LangGraphPipelineExecutor`）均已在每个 Agent 节点后调用 `await context.save_checkpoint()`。

#### 14.2.2 超时熔断

`VideoGeneratorAgent` 对每个镜头的轮询循环使用 `asyncio.wait_for` 包裹，超时阈值由 `VIDEO_GENERATION_TIMEOUT_SECONDS`（默认 600 秒 / 10 分钟）控制。超时后抛出 `TimeoutError` 并通过上层 `AgentResult(success=False)` 上报，不再无限挂起。

相关新配置项：`VIDEO_GENERATION_TIMEOUT_SECONDS`（默认 `600`）、`AGENT_TIMEOUT_SECONDS`（全局 Agent 超时预留，默认 `900`）。

#### 14.2.3 并发控制与限流

`VideoGeneratorAgent` 使用 `asyncio.Semaphore(settings.MAX_CONCURRENT_SHOTS)` 控制同一 pipeline run 内并行提交到外部 API 的镜头数上限（默认 5），防止短时间内发起大量请求触发 API 限流。

相关新配置项：`MAX_CONCURRENT_SHOTS`（默认 `5`）。

---

### 14.3 安全与权限

#### 14.3.1 Agent 文件访问沙箱

`services/input_validator.py` 提供 `validate_agent_file_access(file_path, allowed_dirs)`，将 Agent 访问的文件路径解析为绝对路径后，验证其是否位于指定允许目录内。不在允许范围内的路径抛出 `ValueError`，防止 Agent 读写任意文件系统位置。

#### 14.3.2 上传安全校验

`services/input_validator.py` 提供以下校验函数，供 Router 层在接收用户上传时调用：

- `validate_filename(filename)`：检测路径遍历序列（`../`、`%2e%2e`、null byte 等），返回清洗后的文件名
- `validate_content_type(content_type, allowed)`：验证 MIME 类型是否在白名单内
- `validate_file_size(size_bytes, max_bytes)`：超出限制时返回 HTTP 413

相关新配置项：`MAX_UPLOAD_SIZE_MB`（默认 `500`）、`MAX_IMAGE_SIZE_MB`（默认 `50`）、`ALLOWED_IMAGE_TYPES`、`ALLOWED_VIDEO_TYPES`。

#### 14.3.3 LLM 输出净化

`sanitize_llm_output(text, max_length=50_000)` 函数对 LLM 返回文本做轻量防御：去除 null byte，截断超长输出（防 token stuffing）。可在任何需要将 LLM 输出写入下游系统的节点调用。

---

### 14.4 数据模型变更

| 表 / 列 | 类型 | 变更说明 |
|---|---|---|
| `pipeline_runs.artifacts_snapshot` | `TEXT` | 新增。存储每次 checkpoint 的 `AgentContext.artifacts` JSON 快照 |
| `agent_memories` | 新表 | `id`、`user_id`（FK → users）、`key`、`value_json`、`created_at`、`updated_at`；唯一约束 `(user_id, key)` |

`artifacts_snapshot` 列通过启动时兼容迁移自动补齐历史数据库，无需手动执行 SQL。`agent_memories` 表由 SQLAlchemy `create_all` 在首次启动时自动创建。
