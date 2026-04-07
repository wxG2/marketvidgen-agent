# 简历项目描述 — VidGen AI 短视频生产系统

> 以下提供两种格式：**精简版**（适合直接嵌入简历项目栏）和**详细版**（适合作品集或项目说明页）。

---

## 精简版（简历项目栏，建议直接使用）

**VidGen — AI 驱动的短视频自动化生产系统**
`个人项目 / 全栈独立开发` · `2026`

- 设计并实现了一套端到端短视频生产工作台，集素材管理、脚本生成、分镜规划、多模态提示词工程、TTS 配音、AI 图生视频、视频合成与多平台发布于一体，支持一键全自动与手动逐步两种生产模式
- 构建多 Agent 流水线编排系统，包含 Orchestrator、Prompt Engineer、Audio Subtitle、Video Generator、Video Editor、QA Reviewer 共 6 个专项 Agent；支持三种执行引擎（顺序 Pipeline / LangGraph 状态图 / Swarm 自主协调），可通过配置热切换
- 接入 Qwen Omni（多模态规划与提示词生成）、Qwen3-VL（视频解析）、Qwen3 TTS（语音合成）、Seedance 1.5 Pro / Kling v3（图生视频）、Flux Inpaint（图像合成）、LTX2.3（口型驱动）等 7 类外部模型服务，均实现 Mock / Real 双模式切换
- 实现视频复刻链路：系统可理解参考视频内容，自动提取关键帧并生成镜头级复刻方案，支持用户确认 / 调整 / 终止三态流转
- 实现可靠性保障机制：每 Agent 节点后持久化 Checkpoint、视频生成并发限流（Semaphore）、单镜头超时熔断（configurable，默认 10 分钟）、QA 自动审核与按需重跑
- 实现抖音 OAuth 授权与自动发布草稿链路；后端全异步（FastAPI + SQLAlchemy Async + aiosqlite），前端采用 React 19 + Zustand + SSE 实时推送 Agent 执行进度

**技术栈：** Python / FastAPI / LangGraph / SQLAlchemy / React 19 / TypeScript / Zustand / FFmpeg / Qwen API / Seedance / Kling

---

## 详细版（作品集 / 项目说明页）

### 项目背景

短视频内容生产面临"素材散、流程长、模型多"的核心痛点：从素材筛选到最终发布涉及脚本撰写、分镜规划、AI 生图/生视频、TTS 配音、视频合成、字幕烧录等十余个环节，现有工具通常只解决其中一两个步骤。

VidGen 的目标是把上述所有环节连接成一条可观测、可中断恢复、可局部重试的自动化流水线，同时保留人工在关键节点介入审核的能力（Human-in-the-Loop），让用户既可以"一键交付"，也可以"逐步精调"。

---

### 技术栈

| 层次 | 技术 |
|------|------|
| 前端 | React 19 · TypeScript · Vite · Zustand · TanStack Query · Tailwind CSS |
| 后端 | Python 3.11 · FastAPI · SQLAlchemy Async · aiosqlite · Pydantic v2 |
| Agent 编排 | LangGraph 0.6 · 自研 Swarm Runtime · 顺序 Pipeline |
| 多媒体处理 | FFmpeg · Pillow |
| 外部模型 | Qwen Omni（LLM/视觉）· Qwen3 TTS · Qwen3-VL · Seedance 1.5 Pro · Kling v3 · Flux Inpaint · LTX2.3 |
| 数据存储 | SQLite（可换 PostgreSQL）· 本地文件系统 |
| 实时通信 | SSE（Server-Sent Events） |
| 第三方平台 | 抖音 Open API（OAuth 2.0 + 视频发布） |

---

### 核心贡献

#### 1. 多 Agent 流水线架构

设计并实现包含 6 个专项 Agent 的生产流水线：

| Agent | 职责 |
|-------|------|
| OrchestratorAgent | 意图分类（图文生成 / 视频解析 / 复刻），分镜规划，时长可行性校验，素材分配 |
| PromptEngineerAgent | 基于图片与脚本生成 80–200 词英文电影级视频提示词，以及 TTS 语音参数 |
| AudioSubtitleAgent | 调用 TTS 生成旁白音频，并产出时间对齐字幕文件 |
| VideoGeneratorAgent | 并行提交各镜头图生视频任务，轮询等待完成，支持局部重生成 |
| VideoEditorAgent | 使用 FFmpeg 拼接片段、叠加字幕、混入 BGM、添加水印，输出成片 |
| QAReviewerAgent | 成片后执行双层质量审核（规则 + LLM），按需自动触发上游 Agent 重跑 |

系统支持三种编排引擎，通过单一配置项热切换，不改动任何 Agent 代码：
- **Pipeline**：顺序执行，逻辑清晰，易于调试
- **LangGraph**：基于 `StateGraph` 的 DAG 编排，支持条件路由（QA 失败路由回相应上游 Agent）
- **Swarm**：Lead Agent 担任动态调度者，维护任务板与依赖关系，支持运行中调整计划

#### 2. 视频复刻链路

实现从"参考视频"到"同款生成方案"的完整链路：
- 优先将完整视频直传 Qwen3-VL 做全局理解，失败时自动回退到关键帧模式
- 生成镜头级复刻方案后暂停（`waiting_confirmation`），前端展示方案供用户确认/调整/终止
- 对模型返回的异常结构（shots / audio_design 返回为数组等）自动清洗，保障方案展示不中断

#### 3. 可靠性保障

- **Checkpoint**：每个 Agent 完成后将 `artifacts` 快照写入数据库，服务重启后可从最近节点恢复
- **超时熔断**：每个镜头生成任务使用 `asyncio.wait_for` 包裹，超时（默认 600 秒）后报错而非永久挂起
- **并发限流**：`asyncio.Semaphore` 控制同时并发的镜头生成数（默认 5），防止外部 API 限流
- **QA 自动重试**：QA 审核失败时按 `recommendation` 字段（`retry_video_generator / retry_audio / retry_editor`）自动重跑对应 Agent，超过次数上限后强制交付

#### 4. Human-in-the-Loop

两处用户介入点：
- **复刻确认**：Orchestrator 生成复刻方案后暂停，用户确认/调整后继续
- **提示词审核**（可选）：PromptEngineer 完成后暂停（`waiting_prompt_review`），用户可修改镜头提示词再继续

两处均支持"确认继续 / 提交修改意见重跑 / 直接终止"三态，终止后保留已展示内容。

#### 5. Agent 跨 Run 记忆与工具注册表

- `AgentMemoryService`：用户级 key-value 持久化存储，Agent 可跨流水线 run 记住偏好（语音参数、平台风格、时长模式）
- `ToolRegistry`：统一工具注册与权限控制，Agent 可在运行时按名称发现和调用已授权工具，为后续 tool-use 架构演进奠定基础

#### 6. 安全与输入校验

- 文件上传：MIME 类型白名单、文件大小上限、文件名路径遍历检测（`../`、null byte、URL 编码变体）
- Agent 文件沙箱：`validate_agent_file_access` 将 Agent 的文件系统访问限制在允许目录内
- LLM 输出净化：去除 null byte，截断超长输出，防止 token stuffing

#### 7. 全链路可观测性

- 每个 `PipelineRun` 和 `AgentExecution` 均持久化状态、耗时、输入、输出和错误信息
- 通过 SSE 向前端实时推送 Agent 进度（包括 Agent 内部阶段性灰字进度）
- 按 provider / model / operation 维度记录所有模型调用的 token 消耗，前端看板可视化

---

### 主要成果

- 完整实现从"用户选图 + 输入脚本"到"成片发布抖音"的端到端自动化链路，端到端流程覆盖 10+ 个生产环节
- 构建支持三种编排范式的多 Agent 框架，通过单一配置切换，验证了 LangGraph 状态图在条件路由（QA 重试）场景的工程可行性
- 接入 7 类外部 AI 模型服务，全部实现 Mock / Real 双模式，开发态无需任何 API Key 即可完整运行
- 实现可观测、可恢复、可局部重试的生产级 Agent 系统：Checkpoint 断点续跑 + 超时熔断 + QA 自动重跑，保障长时任务的可靠交付
- 代码库通过 pytest 覆盖核心流程（健康检查、项目 CRUD、Pipeline 运行时、复刻链路、背景模板、仓库管理等），所有主链路测试通过
