# vidgen Agent 系统架构与功能说明

本文档基于 2026-04-23（CST, UTC+8）对当前 `vidgen` 代码的实际梳理编写，聚焦说明系统整体架构、Agent 编排、核心模块、主要流程和当前功能范围，仅描述代码中已经体现出来的能力。

## 0. 文档版本与变更时间（精确到小时）

- 最后全量同步时间：`2026-04-23 CST`（UTC+8）。
- 时间口径说明：以下时间是”文档与代码对齐确认时间”，不是每一处代码首次提交时间。
- `2026-05-18`：Qwen TTS 接入 instruct 提示词控制。当 `QWEN_TTS_MODEL` 使用 `qwen3-tts-instruct-flash` 等 instruct 模型时，`AudioSubtitleAgent` 会把 `voice_params.tone` 与 `speed` 传入 `TTSService`，`RealTTSService` 会生成短视频口播风格的 `instructions` 并随 Qwen TTS 请求发送；混剪链路也会保留 `audio_design.voice_tone`。音色仍通过官方 `voice_id` 列表选择，不把自然语言音色描述当作新音色。
- `2026-05-18`：个人仓库的参考视频 uploads 列表改为直接渲染 `<video>` 预览播放器，并使用 `preload="metadata"` 降低列表初始加载成本，不再需要先点击“播放预览”才出现播放器。
- `2026-05-15`：外部 `/v1/video-jobs` 创建接口补齐混剪提交能力。调用方可使用 Bearer API Key 在同一次 `multipart/form-data` 请求中上传 2 到 20 个 `reference_videos`、可选 `bgm` 音频文件和 `spec.remix_config`；后端自动创建私有项目、写入 `VideoUpload` / 音频 `Material`，并把任务路由到 `remix_planner -> waiting_remix_confirmation -> remix_assembler`。外部审核接口 `/v1/video-jobs/{job_id}/review` 已支持 `remix_plan` 确认或调整后续跑。
- `2026-05-09`：自动模式聊天框新增失败流程继续命令。当前 `PipelineRun` 为 `failed` 时，用户输入 `continue`、`/continue`、`retry`、`/retry`、`继续` 或 `重试`，前端会直接调用 `/api/projects/{project_id}/pipeline/{run_id}/retry-agent`，重试最近失败的 Agent 并从该阶段继续；`LangGraphPipelineExecutor` 已补齐 `continue_from_retry(...)`，例如音频阶段超时但分镜视频已生成时，会复用已完成的 `video_generator` 输出，重试 `audio_subtitle -> video_editor -> qa_reviewer`。
- `2026-05-09`：修正最终视频拼接顺序。`VideoEditorService` 不再调用 LLM 重新决定 `ordered_indices`，而是始终使用导演方案 / 视频生成器已经确定的 `shot_idx` 顺序，避免剪辑阶段覆盖 PromptEngineer 或 ReplicationPlanner 的镜头叙事设计。
- `2026-05-08`：自动模式顶部隐藏调试型 pipeline 参数按钮（时长模式、模型原声、系统配音、转场、BGM、视频生成开关）。这些值改由 `frontend/src/components/pipeline/AutoModeStudio.vue` 中的 `AUTO_PIPELINE_CODE_SWITCHES` 统一控制，界面保留素材 / 参考视频入口、平台、背景模板和视频模型选择。
- `2026-05-08`：强化导演 Agent 镜头方案。`PromptEngineerAgent` 现在要求将 `shot_idx` 作为最终时间线位置、`source_image_idx` 作为素材索引，并输出 `sequence_role` 与 `sequence_reason` 解释营销排序；普通生成会更明确地按 Hook / 场景痛点 / 核心卖点 / 细节证明 / 结果联想 / CTA 组织素材。
- `2026-05-08`：修正剪辑时长优先级。`VideoEditorService` 在 fixed 与 auto 模式下都会优先使用导演方案中的 `duration_seconds` 对每个片段执行 FFmpeg 裁剪，字幕时长只作为缺少镜头时长时的兜底。
- `2026-05-08`：新增鉴权最终成片预览接口 `GET /api/projects/{project_id}/pipeline/{run_id}/final-video`，自动模式聊天区的实时生成进度卡会在 pipeline 完成后直接渲染最终 `<video>`，不再要求先保存到仓库才能预览。
- `2026-05-08`：自动模式聊天区新增实时“生成进度”卡片，复用 pipeline SSE 返回的 `PipelineRun` 与 `AgentExecution.progress_text` 展示当前阶段、各 Agent 状态、最新进度日志，并提供确认镜头方案和取消流程入口；右栏仍保留完整执行状态和中间产物。
- `2026-05-08`：自动模式已选素材展示从文件名胶囊增强为缩略图条；会话中已选择的图片素材复用 `/api/materials/{material_id}/thumbnail` 直接显示预览，非图片素材仍显示文件名卡片，素材选择和 pipeline `image_ids` 传递逻辑不变。
- `2026-05-08`：同步后端目录可读性调整。应用基础设施从根包迁入 `backend/app/core/`，数据库 session 迁入 `backend/app/db/session.py`；Qwen 客户端拆到 `backend/app/services/llm/`，视频生成能力拆到 `backend/app/services/video_generation/`，视频剪辑合成拆到 `backend/app/services/video_editing/`。`backend/app/services/video_generator.py` 仍作为兼容导出入口保留。
- `2026-04-23`：前端仪表盘新增独立 `API Keys` 页签。普通用户可在“我的密钥”视图自助创建、查看和停用外部调用 API Key；管理员可在“客户密钥”视图按用户筛选、为指定客户创建 key 并查看其最后使用时间。完整 `vg_...` key 仅在创建成功当下展示一次，列表页仍只保留前缀、状态、scope 和最后使用时间，不改变后端“明文 key 不入库”的安全语义。
- `2026-04-20`：新增外部视频生成 API v1。登录用户可创建 `vg_` 前缀 API Key，外部客户通过 `Authorization: Bearer vg_...` 调用 `/v1/video-jobs`，一次性上传多张图片素材和生成 `spec`；后端自动创建私有项目、入库素材、创建 `PipelineRun` 并复用现有执行器。外部任务默认强制进入审核态：普通生成等待 `shot_plan` 审核，复刻生成等待 `replication_plan` 审核；确认后继续生成，最终视频只通过 `/v1/video-jobs/{job_id}/result` 下载，不暴露本地绝对路径。
- `2026-04-20`：补齐 P0 安全与一致性改进。上传入口新增统一 `upload_validation` 服务，参考视频、素材图片、Talking Head 图片 / 音频、时间线资产会校验扩展名、声明 MIME、文件头和大小限制；`PipelineCreateRequest` 对平台、时长、模型、转场、BGM、音量等字段增加 Pydantic 约束；抖音账号状态约束新增 `reauthorization_required` 并处理 SQLite naive datetime；抖音连接入口会在发起扫码 OAuth 前校验 Client Key / Client Secret 和 HTTPS 回调地址，配置错误返回 503 可读诊断；自动会话摘要补齐 `waiting_prompt_review` 中文状态；`USE_MOCK_LLM`、`USE_MOCK_GENERATOR`、`USE_MOCK_VIDEO_EDITOR` 已进入服务装配逻辑。
- `2026-04-19`：面向项目经理更新项目说明口径；补齐“产品能力 + 底层实现原理 + 当前能力边界”的阅读路径。同步当前默认配置：`PIPELINE_ENGINE=langgraph`、`HUMAN_IN_LOOP_PROMPT_REVIEW=true`、`QA_REVIEW_ENABLED=true`、`MEM0_ENABLED=true`。修正 Prompt 审核、QA 接入、AgentMemory 数据模型、Mem0 语义记忆、手动模式视频分析真实状态等描述，明确自动模式视频分析 skill 已通过 Qwen 多模态 `video_paths` 实现，传统 `/api/projects/{project_id}/analyze` 的 `Qwen3VLAnalyzer` 真实实现仍待接入。
- `2026-04-17`：补齐中间产物仓库链路；`BaseAgent.run(...)` 会在 `prompt_engineer`、`audio_subtitle`、`video_generator` 成功后自动把提示词方案、shot 级提示词、配音参数、音频、字幕和分镜视频保存为 `RepositoryAsset`。新增 `/api/projects/{project_id}/pipeline/{run_id}/artifacts` 与 `/api/repository/assets`，Vue 自动模式右侧栏和个人仓库新增 Agent 产物可视化。视频生成服务增加瞬时 HTTP 断线重试，`MAX_CONCURRENT_SHOTS` 默认从 `5` 调整为 `2`，并补充 `VIDEO_GENERATION_HTTP_RETRIES` 与 `VIDEO_GENERATION_HTTP_RETRY_BACKOFF_SECONDS`。
- `2026-04-17`：普通生成链路语义收敛为 `OrchestratorAgent(Intake / Context) -> PromptEngineerAgent(Director)`。`RequirementParserAgent` 源文件保留兼容，但新普通 run 不再创建独立 `requirement_parser` 执行记录；Orchestrator 内部完成需求解析与素材上下文整理，`orchestrator_plan` 语义固定为导演输入上下文。Qwen 结构化返回增加宽容 JSON 提取、轻量 schema 校验和 `Qwen 请求失败 / 返回解析失败 / 返回校验失败` 三类进度诊断。
- `2026-04-16`：移除实验性的 Swarm 编排模式；当前自动生成链路仅保留顺序 `PipelineExecutor` 与 `LangGraphPipelineExecutor`，同步删除 Swarm runtime、Lead prompt、运行中消息接口和前端状态展示字段。
- `2026-04-16`：剪枝旧前端与未接入 helper；删除遗留 `.tsx` React 页面、未使用的 pipeline helper 和 `services/input_validator.py`，Orchestrator 不再分配短视频总时长，shot 时长交由提示词/生成方案侧表达，后端仅按视频提供方支持值兜底。
- `2026-04-16`：拆分 FastAPI 应用装配职责；`backend/app/main.py` 只保留 lifespan、router 注册和静态文件挂载，服务 / Agent / runtime skill 装配下沉到 `backend/app/bootstrap.py`，异常处理与 CORS / 鉴权中间件移到 `backend/app/core/http.py`，health 和 artifact cleanup 移到 `backend/app/routers/system.py`。
- `2026-04-16`：收紧自动模式 `generate_video` runtime skill 路由；“设计方案 / 策划方案 / 营销方案”等文字方案请求默认走普通 assistant 对话，不再因包含“生成”二字直接启动视频 pipeline。当前视频生成优先模式下，若会话已有选中素材且用户明确要求生成 / 制作 / 输出视频，ChatAgent 会直接启动 `generate_video`，并把原始消息作为 `user_request` 进入 pipeline；随后由 Orchestrator 内部做需求理解与素材上下文整理。Vue 自动模式补齐“中止对话”和右侧“取消流程”入口，chat SSE 断开时会取消尚未完成的 tool task；已创建的 `PipelineRun` 会继续通过右侧 pipeline SSE 更新，需由 `/api/projects/{project_id}/pipeline/{run_id}/cancel` 标记为 `cancelled`。
- `2026-04-17`：同步视频音频开关拆分；新增 `video_model_no_audio` 控制 Seedance/Kling 自带原声，默认关闭，`voiceover_no_audio` 控制 VidGen TTS/字幕，避免“模型原声”开关误跳过或误生成配音。
- `2026-04-16`：强化普通视频生成链路的 `OrchestratorAgent` 调度职责；该 Agent 以状态机解析用户消息和图片，确定视频类型、发布平台、风格与目标时长，并把 `intent`、`image_context/source_images` 作为导演输入上下文传给下游。每次状态迁移会追加到 `AgentExecution.progress_text`，Vue 自动模式通过 pipeline SSE 更实时地展示执行进度。
- `2026-04-15`：前端运行入口迁移到 `Vue 3 + Vite`；当前构建入口为 `frontend/src/main.ts` 与 `frontend/src/App.vue`，核心页面改为 `.vue` 单文件组件，状态管理改为 Vue 响应式单例 store，React / Zustand 依赖已从前端 package 中移除。
- `2026-04-13`：同步复刻链路拆分后的真实职责边界；复刻方案由 `ReplicationPlannerAgent` 生成并在 `waiting_confirmation` 暂停，确认接口和前端流程面板优先读取 `replication_planner` 输出，同时展示 `qa_reviewer` 节点。
- `2026-04-13`：同步视频生成模型选择；新增 `VIDEO_GENERATION_MODEL` 与 `SEEDANCE_20_MODEL` 配置，自动模式可在 `seedance1.5-pro`、`seedance2.0`、`kling` 间选择，默认仍为 `seedance1.5-pro`。
- `2026-04-13`：同步仓库上传视频导入语义；仓库 uploads 按用户可见，导入到自动模式会话时会为目标项目 / 会话创建可访问的 `VideoUpload` 记录，避免复用旧会话记录导致参考视频归属校验失败。
- `2026-04-13`：同步自动模式 ChatAgent 流式状态事件；收到消息后会先返回不进入最终正文的 `status` 事件，并在 skill 路由、参数提取、工具执行和模型等待阶段持续输出灰字状态。普通对话补齐 Qwen OpenAI 兼容 `stream=True` 分块解析，自动模式 chat SSE 已补充 10 秒心跳，当前无事件超时阈值为 180 秒。
- `2026-04-13`：同步生成链路演示修复；`generate_video` runtime skill 将 `user_request` 与 `narration_script/script` 拆分，Orchestrator 不再把“根据这些素材生成方案”类元指令直接作为口播分镜；当时的旧 `no_audio=true` 兼容字段会跳过 TTS，VideoEditor 支持无音轨合成；视频分析模型返回空文本时改为显式失败。
- `2026-04-10`：接通 Qwen 兼容模式文本对话的真实流式输出；`prompts` 路由下的 SSE 不再只回放整段文本，而是按 DashScope `chat/completions` 流式分块逐段转发给前端。
- `2026-04-10`：自动模式 `auto_sessions` 下的 `ChatAgent` 补齐真实流式分支；普通 assistant 对话会直接转发 Qwen 原生文本分块，只有显式命中 `analyze_video / replicate_video / generate_video` 等工具意图时才保留原有 `tool_call / tool_result` 事件流。
- `2026-04-10`：自动模式 `ChatAgent` 的 runtime skill 进一步对齐 Claude 官方 Skill 设计：`backend/app/agents/skills` 改为目录式 `SKILL.md + schema.json + runtime.py` 结构；启动时只读取 `SKILL.md` frontmatter，命中后再按需展开正文、schema、runtime 和直接引用的参考文件，聊天时仍先按 `required_inputs / routing_hints` 筛候选，以降低 token 消耗。
- `2026-04-10`：重构 `backend/app/agents` 目录结构，按职责拆分为 `core / stages / executors / chat / skills`；其中 LangGraph 执行器进一步拆为 `executor / nodes / state`，顶层旧模块名保留为兼容 shim，避免现有导入路径立即失效。
- `2026-04-07`（本次）：新增 Agent 核心能力补强、可靠性与容错、安全与权限三个模块，详见第 14 节。
- `2026-04-07`：全量同步当时的 `vidgen` Agent 系统实现，补充 Orchestrator 在旧实现中的三条主路径（图文生成 / 参考视频解析 / 视频复刻）、意图分类逻辑、时长可行性校验、会话级素材分配、`analysis_only` 返回链路、确认后恢复执行、单 Agent 重试续跑，以及三种编排引擎的真实职责边界。
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
- `外部 API 模式`
  外部客户使用 API Key 调用 `/v1/video-jobs`，一次性提交图片生成、单参考视频复刻或多参考视频混剪素材和生成需求；后端复用同一条 pipeline，并通过审核接口继续执行和下载成片。

## 2. 总体架构

vidgen 采用前后端分离架构，核心技术栈如下：

- 前端：`Vue 3 + TypeScript + Vite + Vue 响应式状态`
- 后端：`FastAPI + SQLAlchemy Async`
- 数据存储：`SQLite`（默认，通过 aiosqlite 驱动）
- 文件存储：本地文件系统
- 编排方式：`多 Agent 流水线`，支持两种执行引擎（`Pipeline`、`LangGraph`）
- 多媒体处理：`FFmpeg`
- 外部模型服务：`Qwen（QWEN_OMNI_MODEL，当前默认 qwen3-omni-flash）`、`Qwen3 TTS`、`Seedance 1.5 Pro / 2.0`、`Kling v3` 等；自动模式参考视频分析通过 Qwen 多模态 `video_paths` 调用实现，传统手动分析路由中的 `Qwen3VLAnalyzer` 真实集成仍待接入，Flux Inpaint 与 LTX2.3 目前保留真实实现占位并以 Mock 为主

可以把系统拆成 6 层：

1. `交互层`
   负责项目管理、自动模式、手动模式、时间轴、示例画廊和用量看板。
2. `会话层`
   负责自动模式多会话、消息流、会话级素材选择、参考视频绑定、草稿脚本和发布草稿持久化。
3. `编排层`
   负责自动生成链路中的 Agent 执行顺序、暂停/恢复、重试和状态流转，支持顺序执行和 LangGraph 状态图两种编排模式。
4. `能力层`
   负责 LLM、TTS、图生视频、视频剪辑、视频分析、图像合成、口型驱动等具体能力。
5. `状态层`
   负责记录项目、任务、素材、生成结果、agent 执行状态和模型用量。
6. `资产层`
   负责保存素材、上传文件、生成音频、字幕、视频片段和最终成片，并自动清理过期产物。

## 3. 前端架构

前端是一个 Vue 单页应用，围绕项目进行组织，使用 Vue 响应式单例 store 管理客户端状态，服务端数据通过现有 `frontend/src/api/*.ts` 封装直接请求 FastAPI。

### 3.1 状态管理

前端使用三个 Vue 响应式 Store：

- `projectStore`：管理当前项目上下文和步骤。
- `pipelineStore`：管理自动模式切换状态、当前 pipeline run、agent 执行记录和 token 汇总。
- `timelineStore`：管理时间轴编辑器片段、缩放和播放头状态。

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
- 配置可见 pipeline 参数（目标平台、背景模板、视频模型等）；时长模式、模型原声、系统配音、转场、BGM 和视频生成行为由前端代码常量 `AUTO_PIPELINE_CODE_SWITCHES` 控制
- 会话级持久化保存脚本草稿、参考视频、背景模板、素材选择和当前运行状态
- 当只有参考视频且用户表达“解析 / 分析 / 总结 / 描述”类诉求时，ChatAgent 会调用 `analyze_video` runtime skill，直接返回 `analysis_report`，不会进入视频生成 pipeline
- 当用户只是要求“设计方案 / 策划方案 / 营销方案”时，ChatAgent 会按普通对话处理；只有明确要求“开始生成 / 输出视频 / 启动流水线”等生产动作时才自动命中 `generate_video`
- 展示每个 agent 的执行状态和输出
- 通过 SSE 实时显示执行进度
- PromptEngineer 完成后会把镜头级导演方案持久化为 assistant 消息；默认 `HUMAN_IN_LOOP_PROMPT_REVIEW=true`，pipeline 进入 `waiting_prompt_review`，用户在自动模式聊天记录或右侧状态栏确认后才继续进入音频、视频、剪辑和 QA
- 在 assistant 消息下展示上传的参考视频卡片
- 只有当用户输入明确命中视频复刻意图时，调度 Agent 才会调用视频复刻 skill；仅上传参考视频不会自动启动复刻分析
- 在复刻解析过程中，把 Agent 的阶段性分析进度以灰字方式持续追加到消息流
- 在复刻消息中展示关键帧预览和解析报告，便于用户确认复刻依据
- 在复刻模式下展示镜头级复刻方案并等待用户确认 / 调整
- 在复刻模式下，对模型返回的异常方案结构做后端容错清洗，尽量继续产出可确认方案，而不是直接失败
- 在等待确认阶段支持直接终止当前流程，避免会话长期卡在待确认状态
- 在右侧执行状态中支持取消 `pending / running / waiting_confirmation / waiting_prompt_review` 的当前流程
- 当当前流程失败时，聊天框输入 `continue` / `retry` / `继续` 会调用失败 Agent 重试接口，并从最近失败阶段继续执行
- 展示最终视频和中间产物；提示词 Agent、音频字幕 Agent、视频生成 Agent 的产物会在成功后自动进入 `RepositoryAsset`，并在右侧栏按 agent 分组预览
- 在运行中按 agent 展示过程面板，查看每一步的输入输出、报错和重试结果
- 展示抖音、YouTube 平台卡片视频预览
- 成片完成后自动保存到本地视频仓库
- 个人仓库新增 Agent 产物页，可按当前账号查看历史 pipeline 保存的提示词、音频、字幕和分镜视频
- 上传参考视频后自动进入仓库上传记录（可在仓库 uploads 列表查看）
- 从仓库选择已有上传视频时，后端会导入为当前项目 / 会话可访问的参考视频记录
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
- 独立 `API Keys` 页签：
  - 普通用户可创建、查看和停用自己的外部调用 key
  - 管理员可切到“客户密钥”视图，为指定用户创建和管理 key
  - 创建成功后会在前端一次性展示完整 `vg_...` key，离开提示后列表只保留前缀与元数据

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
- `api_keys`
  当前登录用户创建、查看和禁用外部调用 API Key；管理员可查看全量 key、为指定用户创建 key 并禁用任意 key；前端仪表盘已接入独立管理界面，明文 key 仍仅在创建时返回一次。
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
  自动模式下的整条一键生成流程、SSE 进度推送、agent 重试、中间产物查询、成片交付预览、自动保存到仓库、按用户授权抖音账号发布，以及与自动模式会话的 `session_id` 关联。
- `timeline`
  时间轴数据读写、时间轴资产上传和读取。
- `talking_head`
  Talking Head 四步工作流（模型图上传 → 图像合成 → 音频段选择 → 口型驱动）。
- `examples`
  示例画廊。
- `system`
  健康检查与内部维护接口，包括 `/api/health` 和手动触发 artifact 清理。
- `repository`
  聚合展示用户上传参考视频、已保存成片，以及由 pipeline Agent 自动保存的 `RepositoryAsset` 中间产物。
- `public_video_jobs`
  外部 `/v1/video-jobs` API facade，使用 Bearer API Key 鉴权，一次性上传图片、单参考视频或多参考视频混剪素材并创建内部项目和 `PipelineRun`，提供状态查询、SSE、审核确认和成片下载。

### 4.2 Agent 层

Agent 层负责自动模式中的多阶段生成流程。

#### 4.2.1 基础架构

- `BaseAgent`（抽象基类）：提供模板方法模式，封装数据库追踪、计时和错误处理。
- `AgentContext`：包含 `trace_id`、`pipeline_run_id`、`project_id`、`artifacts`、`usage_recorder`、`events`、`cancelled` 等运行态字段，供多阶段恢复和观测链路复用。
- `AgentResult`：封装 success 标志、output_data、error 和 usage_records。
- `BaseAgent.run(...)`：统一负责更新 `PipelineRun.current_agent`、创建 `AgentExecution`、记录 `attempt_number`、落库 `output_data/error_message/duration_ms`，并通过 `UsageRecorder` 记账；当 `prompt_engineer`、`audio_subtitle`、`video_generator` 成功时，还会把可视化中间产物写入 `RepositoryAsset`。
- `AgentContext.report_progress(...)`：允许运行中的 Agent 把阶段性文本写入 `AgentExecution.progress_text`，前端据此流式显示灰字进度。
- 自 `2026-04-10` 起，`backend/app/agents` 目录按职责拆分：
  - `core/`：`BaseAgent`、`AgentContext`、`ToolRegistry` 等基础设施
  - `stages/`：视频生产链路的单职责阶段 Agent
  - `executors/`：`Pipeline`、`LangGraph` 执行器
  - `chat/`：对话式 Agent
  - `skills/`：供 Agent 选择和调用的技能封装；当前采用目录式 `SKILL.md + schema.json + runtime.py`，启动时只读取 frontmatter 并自动注册
  - 顶层 `backend/app/agents/*.py` 旧文件名仍保留，但仅作为兼容导出层，便于渐进迁移

#### 4.2.2 核心 Agent

当前自动 pipeline 可包含以下 agent：

- `RequirementParserAgent`
  源文件保留为兼容层和测试辅助，复用的需求解析规则已抽到 `requirement_utils.py`。新普通生成链路不再把它作为独立节点执行，也不会再产生新的 `requirement_parser` 执行记录。
- `OrchestratorAgent`
  普通图文生成入口和 Intake / Context Agent，负责解析用户消息、素材图片和可选旁白脚本，确定视频类型、发布平台、风格和目标时长，产出 `orchestrator_plan`。它内部按 `intake -> parse_requirements -> resolve_images -> preprocess_images -> analyze_images -> finalize` 状态机推进，并把每次状态迁移追加到 `AgentExecution.progress_text` 供前端展示。它不再负责最终分镜、旁白和配音设计，只输出 `creative_brief / explicit_script / source_images / image_context / intent` 等导演输入上下文。
- `ReplicationPlannerAgent`
  复刻规划 Agent。当 pipeline 输入包含 `reference_video_id` 时，执行器优先路由到该 Agent；它负责解析参考视频、提取关键帧、生成 `replication_plan` 与 `analysis_report`，并把 run 暂停在 `waiting_confirmation` 等待用户确认或调整。它也负责背景模板语义约束、会话级素材分配，以及对复刻模型返回的 `audio_design`、`music_design`、`shots` 做类型清洗与坏数据跳过。
- `PromptEngineerAgent`
  作为导演 Agent，根据 `orchestrator_plan.source_images / image_context`、创作需求和目标时长生成最终镜头方案、英文视频提示词、旁白片段、每个 shot 的生成时长，并输出整条视频的 `voice_params`（`voice_id`、`speed`、`tone`）。
- `AudioSubtitleAgent`
  负责调用 TTS 生成整段旁白音频，并基于音频生成对齐字幕文件；当 pipeline 输入 `voiceover_no_audio=true` 或脚本为空时会跳过 TTS，返回空音频 / 字幕路径并标记 `skipped`。
- `VideoGeneratorAgent`
  负责基于图片和提示词并行生成每个镜头的视频片段，支持长轮询等待完成；`video_model_no_audio` 单独控制 Seedance/Kling 自带原声，默认关闭。在重试场景下也支持只重生成指定镜头索引。
- `VideoEditorAgent`
  负责最终重排、裁剪、拼接并合成音频、字幕、水印和 BGM；当上游无音频路径时可输出无音轨成片，不再强制执行音频混流。
- `QAReviewerAgent`
  在配置开启时位于 `VideoEditorAgent` 之后，负责检查镜头覆盖、时长偏差、音视频同步和整体交付质量，并可给出重试建议。

#### 4.2.3 Orchestrator 与 Replication Planner 的真实职责拆解

当前代码里的 `OrchestratorAgent` 聚焦普通图文生成，真实职责包括：

- 普通图文模式下作为视频生成链路的 Intake / Context 核心，读取 `script/user_request/image_ids/platform/duration_seconds/duration_mode/style/bgm_mood/voice_id/video_model_no_audio/voiceover_no_audio/generation_model/background_context`
- 内部完成需求解析，拆出 `creative_brief / explicit_script / platform / duration_seconds / style / bgm_mood / voice_id / generation_model / video_model_no_audio / voiceover_no_audio`
- 解析每张素材图片的内容与营销角色，输出 `image_context` 与 `source_images`
- 状态机每次迁移通过 `AgentContext.report_progress(...)` 追加进度文本，前端可通过 pipeline SSE 或执行记录轮询看到“边想边做”的过程
- 不再做固定时长镜头分配、最终分镜、旁白或配音设计；这些由 PromptEngineer 结合目标时长、图片上下文和视频 provider 支持时长决定
- 输出 `creative_brief`、`explicit_script`、`intent`、`image_context/source_images`、`voice_config` 和 `state_machine`

复刻模式下，`PipelineExecutor` / `LangGraphPipelineExecutor` 会在发现 `reference_video_id` 时优先执行 `ReplicationPlannerAgent`。该 Agent 会优先上传整段视频给模型做全局理解，再通过 `extract_keyframes` 工具补充镜头级细节；如果整视频理解失败，会自动退回仅关键帧模式。它还会把会话内已选素材按顺序读出并尝试分配到每个镜头，用于后续生成阶段复用用户素材。

参考视频分析不再由 Orchestrator 内部分支承载，而是通过 `analyze_video` runtime skill 直接返回分析报告；参考视频复刻通过 `replicate_video` runtime skill 或 pipeline 的 `reference_video_id` 输入创建 run，并由 `ReplicationPlannerAgent` 进入确认链路。

#### 4.2.4 Pipeline 执行引擎

系统支持两种 pipeline 执行引擎，通过 `PIPELINE_ENGINE` 配置项切换：

**1. Pipeline（顺序执行）**

- 普通生成默认按 Orchestrator(Intake / Context) → Prompt Engineer(Director) → Prompt Review 确认 → (Audio + Video 并行) → Editor → QA 顺序执行；复刻输入会从 Replication Planner 进入确认链路。
- 支持 `waiting_confirmation`、`waiting_prompt_review` 暂停，确认后分别通过 `resume_from_confirmation(...)` / `resume_from_prompt_review(...)` 续跑，以及通过 Router 触发单 Agent 重试后继续向下游推进；纯参考视频分析由 `analyze_video` runtime skill 完成，不进入视频生成 pipeline。

**2. LangGraph（状态图编排）**

- 使用 LangGraph 的 `StateGraph` 实现 DAG 编排。
- 维护 `PipelineState` TypedDict 存储全部中间结果。
- 支持条件分支、并行边（Audio / Video）和异常中断；普通生成会先进入 `orchestrator` 节点，复刻输入会直接进入 `replication_planner`，当前实现里同时支持 `waiting_confirmation` 和 `waiting_prompt_review` 两类人工确认暂停路径。

### 4.3 Service 层

Service 层封装具体能力，当前主要包括：

- `LLMService`
  提供结构化生成和对话能力，支持 Mock 和真实 Qwen 兼容服务（当前默认 `qwen3-omni-flash`），支持图片输入。
- `QwenClient`
  Qwen API 的封装层，提供结构化输出和视觉理解调用。
- `TTSService`
  提供文本转语音和字幕对齐能力，支持 Mock 和真实 Qwen3 TTS 服务。
- `VideoGenerator`
  提供图生视频能力，支持按 run 选择 Seedance 1.5 Pro、Seedance 2.0、Kling v3 和 Mock 实现；默认选择由 `VIDEO_GENERATION_MODEL` 控制。
- `VideoEditorService`
  负责最终视频合成，支持 Mock 和真实 FFmpeg 实现。
- `VideoAnalyzer`
  负责传统手动模式 `/api/projects/{project_id}/analyze` 的参考视频分析入口；当前 Mock 可用，`Qwen3VLAnalyzer` 真实调用仍是待接入实现。自动模式的参考视频分析不走该类，而是由 `analyze_video` runtime skill 通过 `LLMService.generate_text(..., video_paths=[...])` 调用 Qwen 多模态能力并缓存到 `VideoUpload.analysis_report`。
- `MaterialService`
  负责素材扫描、索引、分类管理、缩略图生成和删除。
- `ImageCompositor`
  负责图像合成能力；当前 Mock 可用，Flux Inpaint 真实实现仍是占位。
- `LipSyncGenerator`
  负责口型驱动能力；当前 Mock 可用，LTX2.3 真实实现仍是占位。
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
- 视频编辑阶段不再有独立排序 prompt；最终拼接顺序沿用导演方案 / `video_generator` 的 `shot_idx` 顺序
- QA 审核 prompt（`QA_REVIEWER_SYSTEM_PROMPT`），当前已接入剪辑后的 `QAReviewerAgent`，并可按建议触发有限自动重试
- 视频复刻分析 prompt（`VIDEO_REPLICATION_SYSTEM_PROMPT`），用于关键帧分析、复刻方案生成，以及在无明确需求时按背景信息约束方案语义

## 5. 自动模式主流程

自动模式是当前系统最完整的端到端链路，其执行流程如下：

1. 用户在前端选择素材并输入创作要求；如果用户明确提供最终口播，也可作为旁白脚本输入
2. 用户可以先在左栏新建一个自动模式会话，或切换到历史会话继续编辑
3. 可选：绑定角色背景模板，或上传参考视频进入复刻模式
4. 自动模式会话会持续保存脚本草稿、参考视频、素材选择和视频参数
5. 普通图文模式下，前端调用 `preflight-check` 进行预检查
6. 前端发起 `pipeline` 创建请求，并把当前 `session_id` 一并传给后端
7. 后端创建一条 `PipelineRun`（记录引擎类型，并绑定所属自动模式会话）
8. 后端后台异步执行完整 pipeline
9. ChatAgent / runtime skill 先决定当前请求属于普通生成、参考视频分析还是视频复刻；直接调用 pipeline 时，包含 `reference_video_id` 的 run 会进入复刻规划路径
10. 如果是参考视频分析，`analyze_video` runtime skill 直接返回 `analysis_report`，前端把报告渲染成 assistant 消息，不进入 Prompt / Audio / Video / Editor
11. 如果是复刻模式，`ReplicationPlannerAgent` 先生成镜头级复刻方案，前端展示确认卡片
12. 用户可在复刻确认阶段选择“确认执行 / 提交调整意见 / 直接终止本次流程”；确认后继续执行 Prompt / Audio / Video / Editor，提交调整意见则重新生成复刻方案，终止会把 run 标记为 `cancelled` 且保留已展示方案消息
13. 普通生成时 `OrchestratorAgent` 内部先解析用户自由输入，再继续拆解图片和导演输入上下文；这些状态迁移会作为 `progress_text` 出现在执行状态面板
14. `PromptEngineerAgent` 生成导演方案、镜头提示词、旁白片段和语音参数，并通过 `persist_director_plan_message(...)` 写入自动模式 assistant 消息
15. 默认配置下 pipeline 暂停为 `waiting_prompt_review`；前端展示镜头方案表格，用户可编辑旁白和视频提示词，点击“确认并生成视频”后调用 `/api/projects/{project_id}/pipeline/{run_id}/confirm-prompt-review` 继续执行。
16. 前端通过 pipeline SSE 持续监听任务状态；提示词、音频字幕和视频生成产物会陆续写入仓库并在右侧栏展示
17. 任务结束后返回最终视频路径和各阶段执行记录
18. 成片完成后自动保存到视频仓库，并生成抖音 / YouTube 平台卡片预览
19. 如果当前平台为抖音且用户已连接抖音账号，系统会自动生成一条抖音发布草稿消息，预填标题、文案、话题和封面标题建议
20. 用户在草稿卡片中确认后，后端按所选抖音账号提交 `upload_video + create_video`
21. 支持对失败 agent 发起重试；重试完成后会按该 Agent 的下游依赖继续执行，而不是只重跑单点

自动模式会话切换时，前端会按会话详情接口恢复：

- 消息记录
- 参考视频
- 已选素材
- 背景模板
- 草稿脚本
- 平台、视频模型等可见参数；隐藏的时长 / 原声 / 配音 / 转场 / BGM / 视频生成开关按 `AUTO_PIPELINE_CODE_SWITCHES` 的当前代码值生效
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
- 清洗后的创作需求和可播报脚本
- 每张素材的图片路径、图片内容、视觉角色和营销角度
- 用户目标总时长、平台、风格、BGM、语音、无声模式和视频模型
- 复刻模式下由 `ReplicationPlannerAgent` 输出 `replication_plan`、`analysis_report`、关键帧分析结果和等待确认状态
- 分析模式下由 `analyze_video` runtime skill 输出 `analysis_report`

复刻模式下，系统会优先把“完整参考视频”直接输入模型做全局理解，再结合关键帧工具做镜头级细化；当视频直传链路异常时会自动回退为“仅关键帧分析”模式。若用户没有提供明确脚本或调整意见，但绑定了角色背景模板，则生成方案时会优先让镜头主体、场景和表达口径对齐背景信息，而不是只机械复用参考视频里的主体内容。

普通图文模式下，Orchestrator 还承担两个很重要的工程职责：

- 只保留模型支持时长兜底，不再主动把目标总时长分配到每个镜头
- 在 Qwen 请求失败、JSON 解析失败或 schema 校验失败时，会把具体诊断写入进度，并用本地需求解析和图片摘要兜底，保证整条 pipeline 不因上下文理解异常而完全不可用

当前前端在复刻模式下还会把上传的参考视频直接展示在 assistant 消息下方，并把 `ReplicationPlannerAgent` 的 `progress_text` 以灰字流式附加到同一条解析消息中，帮助用户看到“提取关键帧、分析镜头、组织执行方案”等中间过程。同时会在消息中展示提取出的关键帧预览卡片和解析报告文本，便于用户在确认前快速核对复刻依据。

### 5.2 Prompt Engineer 阶段

基于 Orchestrator 的导演输入上下文，为每个镜头生成：

- 视频提示词（80-200 词，包含镜头运动、光影描述）
- 镜头时长（此处是后续 VideoGenerator / VideoEditor 使用的最终 shot 时长来源）
- 营销叙事位置（`sequence_role`）和排序理由（`sequence_reason`）
- 素材引用索引（`source_image_idx`，允许按专业营销结构重排素材）
- 脚本片段
- TTS 语音参数（voice_id、speed、tone）

这一阶段是“导演方案”的权威来源。代码会把 `shot_prompts`、`voice_params`、`director_summary`、`creative_concept`、`pacing_strategy` 和 `narration_script` 写入 `prompt_plan` checkpoint；LangGraph 节点还会调用 `persist_director_plan_message(...)`，把方案作为带 `directorPlan` payload 的 assistant 消息展示在自动模式聊天记录中。

如果 `input_config.review_prompts` 未显式指定，则使用全局 `HUMAN_IN_LOOP_PROMPT_REVIEW`。当前默认值为 `true`，所以普通生成会在 PromptEngineer 后暂停，等待用户确认镜头方案；确认接口支持按 `shot_idx` 覆盖旁白片段、视频提示词和镜头时长后继续执行。

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

- 按导演方案 / 视频生成器输出的 `shot_idx` 顺序拼接片段，不在剪辑阶段调用 LLM 二次重排
- 按导演方案中的 `duration_seconds` 裁剪片段（fixed / auto 模式均优先遵循方案时长）
- 拼接全部片段
- 添加转场（xfade）
- 合成旁白音频
- 混入 BGM（按情绪选择）
- 烧录字幕
- 添加水印
- 输出最终视频
- 最终时长探测

编辑阶段会写回 `final_video_path`，同时 `/api/projects/{project_id}/pipeline/{run_id}/final-video` 提供鉴权读取，供自动模式聊天区直接预览最终成片。内部上下文已经拿到了完整的 `video_clips_data`、`shot_prompts`、镜头时长、转场、BGM 和水印配置，因此这一层也是未来扩展“局部替换 / 局部重剪 / 质量复审”的主要挂点。

## 6. 手动模式流程

手动模式的目标是让用户逐步控制各个生产环节，主要能力如下：

### 6.1 上传参考视频

用户可以上传一段参考视频，系统会为该项目保存上传记录，支持拖拽上传和预览。上传后视频会进入仓库上传记录（`/api/repository/uploads`）并可在自动模式会话中绑定为当前参考视频。仓库 uploads 列表按当前用户聚合展示，并直接显示参考视频预览播放器；从仓库导入到另一个自动模式会话时，`/api/repository/uploads/{upload_id}/import` 会为目标项目 / 会话创建一条可访问的 `VideoUpload` 记录，而不是直接复用旧会话的记录。

### 6.2 视频分析

手动模式保留传统视频分析步骤，接口为 `/api/projects/{project_id}/analyze`。当前实现中 Mock 分析可用；真实 `Qwen3VLAnalyzer.analyze(...)` 仍是待接入状态，未启用 Mock 时会返回未实现错误。自动模式中用户要求“分析 / 解析 / 总结参考视频”时，实际走的是 `analyze_video` runtime skill，通过 Qwen 多模态 `video_paths` 生成完整中文解析报告，并缓存到 `video_uploads.analysis_report`。

该分析报告包含：

- 视频摘要
- 场景标签
- 推荐素材分类
- 当模型返回空文本时，`analyze_video` runtime skill 会显式报错，避免用占位文案伪装为完整分析报告。

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

Talking Head 是一个四步特殊流程设计，目前前后端流程与 Mock 能力可用于演示，真实 Flux Inpaint / LTX2.3 调用仍是预留：

1. 上传模型人物图片
2. 将人物图与背景图进行合成（Flux Inpaint 预留，当前真实调用未接入）
3. 选择音频段和设置运动提示词
4. 生成口型驱动视频（LTX2.3 预留，当前真实调用未接入）

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
  用于文本转语音和字幕对齐；当配置为 `qwen3-tts-instruct-flash` 等 instruct 模型时，会额外发送由语气与语速生成的 `instructions`，用于控制口播表达方式。
- `Qwen 多模态 video input`
  当前用于自动模式 `analyze_video` runtime skill 的参考视频解析；实现入口是 `LLMService.generate_text(..., video_paths=[...])` 与 `QwenClient.chat_text(...)`。传统 `Qwen3VLAnalyzer` 类保留在手动分析路由中，但真实调用尚未完成。
- `Seedance 1.5 Pro`
  作为默认图生视频提供方，支持可配置的时长和分辨率。
- `Seedance 2.0`
  通过 Volcengine Ark `contents/generations/tasks` 接口接入，使用 `SEEDANCE_20_MODEL` 指定模型名，自动模式可手动选择。
- `Kling v3`
  作为另一套图生视频服务接入。
- `Flux Inpaint`
  预留用于 Talking Head 中的图像合成；当前真实调用尚未接入。
- `LTX2.3`
  预留用于 Talking Head 中的口型驱动；当前真实调用尚未接入。

系统通过 `USE_MOCK_*` 系列配置项决定启用真实服务还是 Mock 服务，因此同一套架构可以在开发态和真实服务态之间切换。

## 9. 数据模型与状态管理

系统通过数据库记录项目、任务和生成状态，核心模型如下：

- `Project`
  表示项目基本信息和当前步骤。
- `User` / `UserSession`
  表示本地账号体系、登录会话和管理员能力。
- `ApiKey`
  表示外部 API 调用凭证，保存 key 前缀、哈希、状态、scope 和最后使用时间；明文 key 不入库。
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
  表示用户上传的参考视频；在自动模式中可通过 `session_id` 绑定到指定会话。仓库导入已有上传视频时，会创建目标项目 / 会话可访问的记录，继续满足参考视频分析和复刻链路的归属校验。
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
  表示自动模式中的整条任务执行，记录引擎类型、输入配置、当前执行 Agent、最终视频路径，并可通过 `session_id` 归属到某个自动模式会话。
- `ExternalVideoJob`
  表示外部 `/v1/video-jobs` 任务与内部 `Project` / `PipelineRun` 的映射，保存调用方引用 ID、请求 JSON 和可选幂等键哈希。
- `AgentExecution`
  表示每个 agent 的执行记录，包含输入、输出、耗时、错误、重试次数和实时 `progress_text`。
- `VideoDelivery`
  表示成片交付动作记录，当前既用于“保存到仓库”，也用于“抖音发布草稿 / 提交发布”状态跟踪，已扩展 `social_account_id`、`draft_payload_json`、`external_status`、`platform_error_code`、`submitted_at`、`published_at` 等字段。
- `RepositoryAsset`
  表示 pipeline Agent 自动保存的中间产物，可存储 `text_content` 或 `file_path`，用 `asset_key` 标识提示词方案、shot 级提示词、音频、字幕、分镜视频等类型。
- `ModelUsage`
  表示模型请求次数和 token 消耗，按 provider/model/operation 维度。
- `AgentMemory`
  表示关系型 Agent 记忆记录，按 `user_id + namespace_key + memory_key + scope` 存储结构化偏好，可归档、设置重要度并关联来源线程 / run。当前主要作为跨 run 偏好记忆基础设施存在，具体读取写入由各 Agent 按需调用。
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
- `002_add_pipeline_engine.py`（2026-03-30）：为 `pipeline_runs` 表添加 `engine` 字段，记录当前 run 使用 `pipeline` 或 `langgraph` 执行。
- `003_add_progress_text.py`（2026-04-02）：为 `agent_executions` 添加 `progress_text`，支持前端展示 Agent 阶段性灰字进度。
- `004_align_agent_db_spec_v2.py`（2026-04-09）：对齐当前 Agent 数据库规格，补齐用户隔离列、`pipeline_runs.artifacts_snapshot`、`video_deliveries` 草稿 / 平台状态字段、Agent 运行相关表与索引等。
- `005_fix_agent_memories_indexes.py`（2026-04-09）：修正 `agent_memories` 的唯一约束和索引兼容问题。
- `006_add_video_upload_analysis_report.py`（2026-04-13）：为 `video_uploads` 添加 `analysis_report`，缓存自动模式视频分析 skill 的完整报告。
- `007_add_voiceover_no_audio.py`（2026-04-17）：补齐视频模型原声与 VidGen TTS / 字幕的独立开关字段。
- `008_extend_social_account_status.py`（2026-04-20）：扩展抖音账号状态约束，加入 `reauthorization_required`。
- `009_add_public_api_jobs.py`（2026-04-20）：新增 `api_keys` 与 `external_video_jobs`，支持外部 `/v1/video-jobs` API Key 鉴权和任务映射。

此外，当前系统在应用启动时还会通过轻量级兼容迁移补齐部分历史列，例如：

- `pipeline_runs.user_id`
- `pipeline_runs.session_id`
- `video_uploads.session_id`
- `materials.user_id`
- `prompt_messages.user_id`
- `prompts.user_id`
- `pipeline_runs.artifacts_snapshot`
- `video_uploads.analysis_report`

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
- `ExternalVideoJob`
  跟踪外部 API job 与内部 pipeline run 的映射，支持状态查询、SSE、幂等重试和成片下载鉴权。
- `Prompt Review Checkpoint`
  当 run 进入 `waiting_prompt_review` 时，`pipeline_runs.artifacts_snapshot` 保存到 PromptEngineer 后的完整 artifacts，前端通过聊天消息中的 `directorPlan` payload 展示镜头表格，确认后从 checkpoint 继续执行。
- `ModelUsage`
  跟踪每次模型调用的 provider、model、operation 和 token 消耗。
- `SSE Stream`
  让前端可以实时看到 pipeline 的状态变化。
- `Prompt Chat SSE`
  `prompts` 路由下的提示词对话在真实模型模式下会直接转发 Qwen 兼容模式返回的流式文本分块，而不是等待完整回答结束后一次性返回。
- `Auto Chat SSE`
  `auto_sessions` 路由下的 assistant 普通对话现在也会直接转发 Qwen 兼容模式返回的流式文本分块；但受 DashScope 兼容模式 `tools` 与 `stream=True` 不能同时启用的限制，显式工具调用仍沿用 `tool_call / tool_result / done` 事件流。当前还会额外转发不进入最终 assistant 正文的 `status` 事件，用于在前端过程区展示“已收到、正在匹配 skill、正在调用模型、技能仍在执行”等灰字状态；`auto_sessions` chat SSE 会在等待下一条内部事件时每 10 秒发送一次状态心跳，无事件超时阈值为 180 秒。前端“中止对话”会断开当前 chat SSE；若此时 tool task 尚未完成，后端会取消对应 task，避免断开前端后继续等待工具结果。若 tool 已经返回 `run/run_id` 并创建了 `PipelineRun`，右侧执行状态会继续监听该 run，取消流程需使用右侧按钮。
  自 `2026-04-10` 起，显式工具调用默认也不再走“全量 tool schema 一次性下发”的 broad tool-calling，而是先自动发现当前可用 runtime skills 的 `SKILL.md` frontmatter，再按 `required_inputs / routing_hints` 做候选筛选，必要时只把候选 skill 摘要交给 LLM 做轻量路由；选中后才继续展开该 skill 的正文、schema、runtime 和直接引用的参考文件。只有少数候选仍有歧义的请求，才保留 broad tool-calling 作为 fallback。`generate_video` 额外有保守路由保护：包含“设计方案 / 策划方案 / 营销方案”等文字方案语义、且没有“开始生成 / 输出视频 / 启动流水线”等明确生产动作时，不会创建 `PipelineRun`。
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
- Agent 中间产物仓库记录（`RepositoryAsset`，包括提示词、配音参数、音频、字幕和分镜视频）
- 合成图像（Talking Head）
- 口型驱动视频
- 最终合成视频
- 仓库上传记录（参考视频）
- 仓库交付记录（成片保存）
- 抖音发布草稿与提交记录
- 时间轴资产
- 项目历史记录
- 模型消耗统计

`prompt_engineer`、`audio_subtitle`、`video_generator` 的用户可读产物会在对应 Agent 成功结束时自动写入 `repository_assets`。文本类产物写入 `text_content`，本地文件类产物复制到 `VIDEO_REPOSITORY_DIR/artifacts/...`，远程视频 URL 则保留原 URL。前端自动模式通过 `/api/projects/{project_id}/pipeline/{run_id}/artifacts` 展示当前 run，个人仓库通过 `/api/repository/assets` 查看历史产物。

这些产物既用于前端展示，也用于后续编辑、下载和历史回看。过期产物会被自动清理（默认 7 天保留期）。

## 12. 配置与部署

### 12.1 关键配置项

- `PIPELINE_ENGINE`：执行引擎选择（`pipeline` | `langgraph`，默认 `langgraph`）
- `QWEN_API_KEY` / `QWEN_API_URL`：Qwen 模型服务
- `QWEN_OMNI_MODEL`：主 LLM / 视觉理解链路使用的模型名（当前默认 `qwen3-omni-flash`）
- `QWEN_TTS_MODEL`：语音合成模型名（当前默认 `qwen3-tts-flash`；可配置为 `qwen3-tts-instruct-flash` 以启用 `instructions` 语气 / 节奏控制，音色仍需使用模型支持的 `voice_id`）
- `MEM0_ENABLED` / `MEM0_EMBEDDING_MODEL` / `MEM0_EMBEDDING_DIMS` / `MEM0_SEARCH_LIMIT`：Mem0 语义记忆开关、embedding 模型、向量维度和检索条数。只有 `MEM0_ENABLED=true` 且 `QWEN_API_KEY` 可用时才会初始化；失败会降级为无语义记忆。
- `KLING_API_KEY` / `WAVESPEED_API_KEY`：视频生成服务
- `VIDEO_GENERATION_MODEL`：默认视频生成模型选择（`seedance1.5-pro` | `seedance2.0` | `kling`，默认 `seedance1.5-pro`）
- `MAX_CONCURRENT_SHOTS`：同一 run 内视频生成 shot 并发数（默认 `1`）
- `VIDEO_GENERATION_HTTP_RETRIES` / `VIDEO_GENERATION_HTTP_RETRY_BACKOFF_SECONDS`：视频生成服务瞬时 HTTP 错误重试次数与退避秒数
- `VIDEO_GENERATION_TIMEOUT_SECONDS`：单个镜头视频生成轮询超时秒数（默认 `600`）
- `SEEDANCE_MODEL`：Seedance 1.5 Pro 图生视频模型标识（当前默认 `doubao-seedance-1-5-pro-251215`）
- `SEEDANCE_20_MODEL`：Seedance 2.0 图生视频模型标识（当前默认 `doubao-seedance-2-0-260128`）
- `HUMAN_IN_LOOP_PROMPT_REVIEW`：PromptEngineer 后是否默认暂停等待用户确认镜头方案（默认 `true`）
- `QA_REVIEW_ENABLED` / `QA_AUTO_RETRY_ENABLED` / `MAX_QA_RETRIES`：QA 审核开关、失败后是否自动按建议重试、最大 QA 触发重试次数（默认 `true` / `true` / `1`）
- `AGENT_MEMORY_ENABLED`：关系型 Agent 记忆配置项保留为默认 `true`；当前启动逻辑会创建 `AgentMemoryService`，实际是否读写取决于 Agent 代码是否调用 `context.memory_service`
- `IMAGE_COMPOSITOR_API_KEY` / `LTX_API_KEY`：Talking Head 相关服务
- `DOUYIN_CLIENT_KEY` / `DOUYIN_CLIENT_SECRET` / `DOUYIN_REDIRECT_URI`：抖音网站应用 OAuth 授权与发布配置。扫码授权页由抖音展示二维码，但生成授权 URL 仍需要 Client Key，回调用 `code` 换取 `access_token` 仍需要 Client Secret；`DOUYIN_REDIRECT_URI` 必须是已在抖音开放平台登记的 HTTPS 回调地址。
- `DOUYIN_DEFAULT_SCOPE`：抖音默认授权 scope
- `FRONTEND_BASE_URL`：抖音授权完成后前端工作台的基础地址
- `PLATFORM_RESOLUTIONS`：平台分辨率映射（generic、douyin、xiaohongshu、bilibili）
- `MAX_UPLOAD_SIZE_MB` / `MAX_IMAGE_SIZE_MB` / `MAX_AUDIO_SIZE_MB` / `MAX_TIMELINE_ASSET_SIZE_MB`：参考视频、图片、Talking Head 音频和时间线资产的上传大小限制。
- `ALLOWED_IMAGE_TYPES` / `ALLOWED_VIDEO_TYPES` / `ALLOWED_AUDIO_TYPES` / `ALLOWED_SUBTITLE_TYPES`：上传入口接受的声明 MIME 类型；实际写盘前还会检查扩展名和文件头。
- `USE_MOCK_*` 系列：开发态 Mock 开关（analyzer、llm、generator、tts、editor、compositor、lipsync）；`USE_MOCK_LLM`、`USE_MOCK_GENERATOR`、`USE_MOCK_VIDEO_EDITOR` 会直接影响对应服务装配。
- 目录配置：`UPLOAD_DIR`、`MATERIALS_ROOT`、`GENERATED_DIR`、`BGM_DIR`、`WATERMARKS_DIR`、`THUMBNAILS_DIR`

### 12.2 生命周期管理

系统启动时通过 FastAPI lifespan 调用 `bootstrap.startup_application(...)`，完成目录初始化、数据库初始化、运行中任务恢复、Agent / runtime skill 装配和定期清理任务注册；关闭时调用 `bootstrap.shutdown_application(...)` 取消后台清理任务。

## 13. 关键代码入口

如果后续需要继续查看系统实现，建议优先阅读以下文件：

### 后端核心

- `backend/app/main.py`
- `backend/app/bootstrap.py`
- `backend/app/core/http.py`
- `backend/app/core/config.py`
- `backend/app/core/security.py`
- `backend/app/core/logging.py`
- `backend/app/db/session.py`

### Agent 与 Pipeline

- `backend/app/agents/core/base.py`
- `backend/app/agents/core/tool_registry.py`
- `backend/app/agents/executors/pipeline.py`
- `backend/app/agents/executors/langgraph/executor.py`
- `backend/app/agents/executors/langgraph/nodes.py`
- `backend/app/agents/executors/langgraph/state.py`
- `backend/app/agents/stages/orchestrator.py`
- `backend/app/agents/stages/prompt_engineer.py`
- `backend/app/agents/stages/audio_subtitle.py`
- `backend/app/agents/stages/video_generator.py`
- `backend/app/agents/stages/video_editor.py`
- `backend/app/agents/stages/qa_reviewer.py`
- `backend/app/agents/chat/agent.py`

### 服务层

- `backend/app/services/llm_service.py`
- `backend/app/services/llm/qwen_client.py`
- `backend/app/services/social_accounts.py`
- `backend/app/services/api_keys.py`
- `backend/app/services/public_video_jobs.py`
- `backend/app/services/video_delivery.py`
- `backend/app/services/video_generator.py`
- `backend/app/services/video_generation/router.py`
- `backend/app/services/video_editing/composer.py`
- `backend/app/services/tts_service.py`
- `backend/app/services/video_analyzer.py`
- `backend/app/services/material_service.py`
- `backend/app/services/image_compositor.py`
- `backend/app/services/lipsync_generator.py`
- `backend/app/services/media_utils.py`
- `backend/app/services/artifact_cleanup.py`

### 路由层

- `backend/app/routers/auto_sessions.py`
- `backend/app/routers/api_keys.py`
- `backend/app/routers/public_video_jobs.py`
- `backend/app/routers/pipeline.py`
- `backend/app/routers/social_accounts.py`
- `backend/app/routers/generation.py`
- `backend/app/routers/materials.py`
- `backend/app/routers/talking_head.py`
- `backend/app/routers/examples.py`

### 新增模块（2026-04-07）

- `backend/app/agents/stages/qa_reviewer.py`
- `backend/app/agents/core/tool_registry.py`
- `backend/app/models/agent_memory.py`
- `backend/app/services/agent_memory.py`

### 提示词

- `backend/app/prompts/system_prompts.py`

### 前端核心

- `frontend/src/main.ts`
- `frontend/src/App.vue`
- `frontend/src/api/autoSessions.ts`
- `frontend/src/components/pipeline/AutoModeStudio.vue`
- `frontend/src/components/repository/RepositoryPage.vue`
- `frontend/src/components/dashboard/UsageDashboardPage.vue`
- `frontend/src/components/timeline/TimelineEditor.vue`
- `frontend/src/stores/projectStore.ts`
- `frontend/src/stores/pipelineStore.ts`
- `frontend/src/stores/timelineStore.ts`

## 14. 新增能力模块（2026-04-07）

本节记录本次新增的三个模块：Agent 核心能力补强、可靠性与容错、安全与权限。

### 14.1 Agent 核心能力补强

#### 14.1.1 QA 审核 Agent

新增 `QAReviewerAgent`（当前实现位于 `backend/app/agents/stages/qa_reviewer.py`，顶层兼容入口仍保留），在 `VideoEditorAgent` 之后自动执行成片质量审核。

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

新增 `AgentMemoryService`（`backend/app/services/agent_memory.py`）和 `AgentMemory` 数据模型（`backend/app/models/agent_memory.py`），为 Agent 提供跨流水线 run 的关系型、用户级持久化记忆。`bootstrap.initialize_agent_state(...)` 会把该服务挂到 `app.state.agent_memory`，pipeline 和 runtime skill 会把它传入 `AgentContext.memory_service`。

`agent_memories` 当前表结构重点字段：

- `user_id`
- `scope`（conversation / session / user / organization，默认 user）
- `namespace_key`（默认形如 `user:{user_id}:vidgen`）
- `memory_key`（例如 `voice.preferred_params`）
- `content_json`
- `summary`
- `source_type` / `source_thread_id` / `source_run_id`
- `importance`
- `metadata_json`
- `expires_at` / `archived_at`

唯一约束是 `(namespace_key, memory_key)`，并通过 `archived_at` 支持软删除。

内置语义接口：

- `remember_voice_params / recall_voice_params`：保存 / 读取最近一次成功的 TTS 语音参数
- `remember_platform_style / recall_platform_style`：保存 / 读取平台专属提示词风格偏好
- `remember_shot_duration_pattern / recall_shot_duration_pattern`：保存 / 读取成功的镜头时长分配模式

`AgentContext` 新增 `user_id`、`memory_service` 字段，各 Agent 可在 `execute()` 中通过 `context.memory_service` 读写记忆。

同时新增 `Mem0Service`（`backend/app/services/mem0_service.py`）作为语义记忆层。它在 `MEM0_ENABLED=true` 且 `QWEN_API_KEY` 存在时初始化，使用 Qwen 兼容 LLM 与 `text-embedding-v3` embedding；ChatAgent 会在普通对话前检索相关记忆，并在回复后异步写入会话记忆，Orchestrator 会检索平台风格偏好并显式写入 pipeline 上下文记忆。初始化失败时会记录 warning 并降级为无语义记忆。

相关配置项：`AGENT_MEMORY_ENABLED`（默认 `true`，当前为保留开关，启动逻辑仍会创建关系型服务）、`MEM0_ENABLED`（默认 `true`）、`MEM0_EMBEDDING_MODEL`、`MEM0_EMBEDDING_DIMS`、`MEM0_SEARCH_LIMIT`。

#### 14.1.3 Tool Registry（Agent 工具注册表）

新增 `ToolRegistry`（当前实现位于 `backend/app/agents/core/tool_registry.py`，顶层兼容入口仍保留），为 Agent 提供运行时动态工具发现与调用基础设施。

核心概念：

- `ToolDefinition`：描述单个工具（name、description、async fn、optional required_permission）
- `ToolRegistry.register / grant_permission / list_tools / invoke`

`AgentContext` 新增 `tool_registry` 字段。Pipeline executor 可在启动时注册工具并为各 Agent 授权，Agent 在 `execute()` 中通过 `context.tool_registry.invoke(tool_name, agent_name=self.name, ...)` 调用已授权工具。当前作为基础设施提供，各 Agent 的具体工具接入在后续迭代中逐步推进。

#### 14.1.4 Human-in-the-Loop 增强（Prompt 审核）

在普通图文生成模式下，PromptEngineer 完成后支持暂停等待用户审核镜头提示词方案。

新增 pipeline 状态值 `waiting_prompt_review`，触发条件：`input_config.review_prompts = true` 或全局配置 `HUMAN_IN_LOOP_PROMPT_REVIEW = true`（当前默认 `true`）。

暂停时，`orchestrator_plan` 和 `prompt_plan` 已持久化到 checkpoint，LangGraph 节点会把镜头方案作为 `directorPlan` payload 写入 assistant 消息。当前前端可展示镜头级提示词并提供“确认并生成视频”按钮；确认接口可接收 `edited_shots`，按 `shot_idx` 修改旁白、视频提示词或时长后再通过 `resume_from_prompt_review()` 继续执行 Audio / Video / Editor / QA 阶段。外部 `/v1/video-jobs/{job_id}/review` 也复用这一 checkpoint 续跑能力。

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

`VideoGeneratorAgent` 使用 `asyncio.Semaphore(settings.MAX_CONCURRENT_SHOTS)` 控制同一 pipeline run 内并行提交到外部 API 的镜头数上限（默认 2），防止短时间内发起大量图片上传 / 图生视频请求触发 API 限流或服务端主动断连。

视频生成服务层会对服务端断连、网络传输错误，以及 429/5xx 等临时 HTTP 状态做有限重试。相关配置项：`MAX_CONCURRENT_SHOTS`（默认 `2`）、`VIDEO_GENERATION_HTTP_RETRIES`（默认 `2`）、`VIDEO_GENERATION_HTTP_RETRY_BACKOFF_SECONDS`（默认 `2.0`）。

---

### 14.3 安全与权限

#### 14.3.1 当前已接入能力

当前安全能力主要集中在认证、用户资源隔离、上传入口校验和工具权限边界：

- 本地账号体系使用 PBKDF2 密码哈希与 session token 哈希存储。
- 外部 API Key 只保存 SHA-256 哈希和短前缀，`/v1/*` 不依赖 Cookie，统一通过 `Authorization: Bearer vg_...` 鉴权；当前支持 `video_jobs:create`、`video_jobs:read`、`video_jobs:review` 和 `*` scope，禁用 key 或缺少 scope 会返回 403。
- `auth_middleware` 为大部分 API 注入用户上下文，并保留登录、健康检查、静态产物等明确豁免入口。
- 项目、pipeline run、auto session、material、background template、social account 等资源访问通过归属校验 helper 控制。
- `services/upload_validation.py` 统一处理上传文件名清洗、扩展名白名单、声明 MIME 校验、大小限制和基础文件头校验；已接入参考视频上传、素材图片上传、Talking Head 图片 / 音频上传和时间线资产上传。
- `PipelineCreateRequest`、`ConfirmPromptReviewRequest`、`DeliveryActionRequest` 等 schema 增加枚举、长度、范围和列表长度约束，非法请求会在进入 Agent 流水线前返回 422。
- 抖音账号刷新失败时会将账号标记为 `reauthorization_required`，该状态已写入数据库约束，避免发布链路因约束不一致失败。
- `POST /api/social-accounts/douyin/connect` 会在返回授权 URL 前校验抖音 OAuth 配置；缺少 Client Key / Client Secret 或使用非 HTTPS 回调地址时返回 503 与中文诊断，前端会直接展示该诊断并在授权回调后刷新已连接账号列表。
- `ToolRegistry` 支持工具级 `required_permission`，执行前校验 Agent 是否具备相应权限。

#### 14.3.2 当前缺口

当前静态产物路径 `/generated`、`/repository` 和 `/examples` 仍为中间件豁免路径，更适合本地开发和内测；生产部署前应改为鉴权下载或短期 signed URL。Cookie 仍使用 `secure=False` 的本地开发配置，生产环境还需要 CSRF、rate limit、密钥加密存储、Agent 文件访问沙箱和更完整的内容安全策略。

---

### 14.4 数据模型变更

| 表 / 列 | 类型 | 变更说明 |
|---|---|---|
| `pipeline_runs.artifacts_snapshot` | `TEXT` | 新增。存储每次 checkpoint 的 `AgentContext.artifacts` JSON 快照 |
| `agent_memories` | 新表 / 对齐后表 | `id`、`user_id`、`scope`、`namespace_key`、`memory_key`、`content_json`、`summary`、`source_type`、`source_thread_id`、`source_run_id`、`importance`、`metadata_json`、`expires_at`、`archived_at`、`created_at`、`updated_at`；唯一约束 `(namespace_key, memory_key)` |
| `video_uploads.analysis_report` | `TEXT` | 新增。缓存自动模式 `analyze_video` runtime skill 生成的完整参考视频分析报告 |

`artifacts_snapshot` 和 `analysis_report` 等列通过迁移与启动时兼容补齐历史数据库。`agent_memories` 表由 SQLAlchemy `create_all` 在首次启动时自动创建，旧结构会通过 `004/005` 迁移对齐到当前模型。
