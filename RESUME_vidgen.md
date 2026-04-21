# 简历项目描述 — VidGen Agentic AI 短视频生产系统

> 以下提供两种格式：**精简版**（适合直接嵌入简历项目栏）和 **详细版**（适合作品集、面试项目页或 GitHub README 项目亮点）。

---

## 精简版（简历项目栏，建议直接使用）

**VidGen — 面向短视频生产的 Agentic AI 工作流平台**
`个人项目 / 全栈独立开发` · `2026`

- 设计并实现端到端 AI 短视频生产工作台，覆盖素材管理、需求解析、分镜规划、多模态提示词工程、TTS 配音、图生视频、FFmpeg 合成、QA 审核与抖音草稿发布，支持一键自动化与人工逐步审核两种生产模式
- 构建基于 LangGraph `StateGraph` 的多 Agent 编排系统，包含 Orchestrator、Prompt Engineer、Audio Subtitle、Video Generator、Video Editor、QA Reviewer 等专项 Agent，支持条件路由、QA 失败回退重试、节点级 Checkpoint 和 Human-in-the-Loop 暂停/恢复
- 实现 MCP Server 能力，将内部 `ToolRegistry` 与业务能力通过 FastMCP 暴露为 4 个工具：素材检索、Pipeline 状态查询、历史项目检索、Agent 工具发现；同时提供 `GET /mcp/tools` HTTP 发现端点，可被 Claude Desktop 等 MCP 客户端接入
- 实现 RAG 增强生成链路：基于 Qdrant + Qwen `text-embedding-v3` 提供历史生成方案索引/检索能力，Orchestrator 调用 LLM 前检索相似项目并注入 few-shot context；Qdrant 或 embedding 服务不可用时自动降级为空结果，不阻断主流程
- 建立 Agent 可观测性体系：结构化日志支持 `LOG_FORMAT=text|json`，请求级 `X-Request-ID` / `trace_id` 贯穿，Analytics API 聚合运行数、成功率、各 Agent 耗时、QA 打回率、Token 消耗与日维度趋势
- 完成工程化交付：Docker 多阶段构建、`docker-compose` 编排 backend / frontend / Qdrant、GitHub Actions 执行 ruff lint + pytest + Docker build；新增覆盖 ToolRegistry、Analytics、RAG、MCP、BaseAgent 的 50+ 测试用例

**技术栈：** Python / FastAPI / LangGraph / MCP / FastMCP / Qdrant / SQLAlchemy Async / structlog / Vue 3 / TypeScript / Vite / Docker / GitHub Actions / FFmpeg / Qwen API / Seedance / Kling

---

## 详细版（作品集 / 项目说明页）

### 项目背景

短视频内容生产面临“素材散、流程长、模型多、失败定位难”的核心痛点：从素材筛选到最终发布涉及需求理解、脚本撰写、分镜规划、AI 生图/生视频、TTS 配音、视频合成、字幕烧录、质量审核等十余个环节，现有工具通常只解决其中一两个步骤。

VidGen 的目标是把上述环节抽象成一条可观测、可中断恢复、可局部重试、可被外部 Agent 客户端调用的生产流水线。项目重点不只是“生成视频”，而是验证一套面向真实业务的 Agent 应用工程范式：协议化工具接入、RAG 上下文增强、长任务编排、可观测性、容器化部署和测试保障。

---

### 技术栈

| 层次 | 技术 |
|------|------|
| 前端 | Vue 3 · TypeScript · Vite · Vue 响应式状态 · Tailwind CSS · SSE |
| 后端 | Python 3.11 · FastAPI · SQLAlchemy Async · aiosqlite · Pydantic v2 |
| Agent 编排 | LangGraph 0.6 · 顺序 Pipeline · BaseAgent 模板方法 |
| Agent 工具协议 | MCP · FastMCP · ToolRegistry · OpenAI / Claude tool schema |
| RAG / 记忆 | Qdrant · Qwen text-embedding-v3 · AgentMemoryService · Mem0 |
| 可观测性 | structlog · request_id / trace_id · Analytics API · UsageRecorder |
| 工程化 | Docker · docker-compose · GitHub Actions · ruff · pytest |
| 多媒体处理 | FFmpeg · Pillow |
| 外部模型 | Qwen Omni（LLM/视觉）· Qwen3 TTS · Qwen3-VL · Seedance 1.5 Pro · Kling v3 · Flux Inpaint · LTX2.3 |
| 第三方平台 | 抖音 Open API（OAuth 2.0 + 视频发布） |

---

### 核心贡献

#### 1. 多 Agent 流水线架构

设计并实现包含 6 个专项 Agent 的生产流水线：

| Agent | 职责 |
|-------|------|
| OrchestratorAgent | 意图分类（图文生成 / 视频解析 / 复刻），分镜规划，时长可行性校验，素材分配，并在生成前注入记忆与 RAG 上下文 |
| PromptEngineerAgent | 基于图片与脚本生成 80-200 词英文电影级视频提示词，以及 TTS 语音参数 |
| AudioSubtitleAgent | 调用 TTS 生成旁白音频，并产出时间对齐字幕文件 |
| VideoGeneratorAgent | 并行提交各镜头图生视频任务，轮询等待完成，支持局部重生成 |
| VideoEditorAgent | 使用 FFmpeg 拼接片段、叠加字幕、混入 BGM、添加水印，输出成片 |
| QAReviewerAgent | 成片后执行双层质量审核（规则 + LLM），按需自动触发上游 Agent 重跑 |

系统支持两种编排引擎，通过配置项切换，不改动 Agent 业务逻辑：
- **Pipeline**：顺序执行，逻辑清晰，便于本地调试和问题定位
- **LangGraph**：基于 `StateGraph` 的 DAG 编排，支持条件路由、QA 失败回退到指定上游 Agent、最终状态汇聚

#### 2. MCP Server 与工具协议化

- 使用 FastMCP 实现独立 MCP Server，将 VidGen 的核心业务能力暴露给外部 Agent 客户端
- 提供 4 个 MCP tool：`list_materials`、`get_pipeline_status`、`search_project_history`、`list_agent_tools`
- 新增 `GET /mcp/tools` HTTP 发现端点，返回工具名、描述和输入 JSON Schema，便于调试和非 MCP 客户端发现能力
- `list_agent_tools` 复用内部 `ToolRegistry`，展示工具注册、权限治理、OpenAI / Claude 双格式 schema 输出等能力

#### 3. RAG 检索增强

- 基于 Qdrant + Qwen `text-embedding-v3` 实现历史生成方案索引与相似检索，支持将 completed pipeline 的需求、平台、风格、方案摘要和评分写入向量库
- Orchestrator 在调用 LLM 生成方案前，按当前创意需求和平台检索相似历史项目，将结果格式化为 few-shot context 注入 prompt
- `AgentContext` 增加 `rag_service` 扩展字段，pipeline 启动时从应用状态注入；服务启动阶段自动初始化 Qdrant collection
- 检索、索引、collection 初始化均做 graceful degradation，Qdrant 或 embedding API 离线时返回空结果或 false，不影响主视频生成链路

#### 4. Agent 可观测性与 Analytics API

- 使用 structlog 统一结构化日志，支持开发态 text 与生产态 JSON 两种格式
- 在 FastAPI 中间件注入 `X-Request-ID`，并通过 structlog contextvars 将 request_id、method、path 等字段绑定到请求链路
- BaseAgent 执行日志记录 `agent`、`trace_id`、`duration_ms`、错误类型等结构化字段，便于按 Agent 维度定位长任务失败
- 新增 Analytics API：总览、各 Agent 耗时/成功率、QA 通过率、Token 消耗明细、Pipeline 日维度趋势，为面试展示“如何定位 Agent 问题”提供数据入口
- 全局异常处理器返回 generic error message，避免将内部异常字符串直接暴露给客户端

#### 5. 视频复刻与 Human-in-the-Loop

- 实现从“参考视频”到“同款生成方案”的完整链路：优先直传完整视频给 Qwen3-VL 做全局理解，失败时回退到关键帧模式
- 生成镜头级复刻方案后进入 `waiting_confirmation` 状态，前端展示方案供用户确认、调整或终止
- PromptEngineer 支持可选提示词审核暂停，用户可修改镜头 prompt 后继续执行
- 两类人工介入点均支持“确认继续 / 提交修改意见重跑 / 直接终止”三态，保留已生成内容，适配真实生产中的可控 Agent 需求

#### 6. 长任务可靠性保障

- **Checkpoint**：每个 Agent 完成后将 `artifacts` 快照写入数据库，服务重启后可从最近节点恢复
- **超时熔断**：每个镜头生成任务使用 `asyncio.wait_for` 包裹，超时（默认 600 秒）后报错而非永久挂起
- **并发限流**：`asyncio.Semaphore` 控制同时并发的镜头生成数（默认 5），降低外部 API 限流风险
- **QA 自动重试**：QA 审核失败时按 `recommendation` 字段（`retry_video_generator / retry_audio / retry_editor`）自动重跑对应 Agent，超过次数上限后强制交付

#### 7. 工程化交付与测试

- 后端 Dockerfile 采用 builder + runtime 多阶段构建，runtime 镜像包含 FFmpeg，适配视频合成场景
- 前端 Dockerfile 使用 Vite build + Nginx runtime，并配置 SPA fallback 与 API 反向代理
- `docker-compose` 编排 backend、frontend、Qdrant 三服务，并为 backend / Qdrant 配置 healthcheck
- GitHub Actions 在 PR 触发 backend lint/test、frontend lint/build、Docker image build，覆盖代码质量、测试和镜像构建链路
- 新增 50+ 单元测试，覆盖 ToolRegistry 注册/权限/双格式 schema/调用，Analytics 端点，RAG 离线降级与 prompt formatting，MCP 发现端点 schema，BaseAgent 模板方法、取消和异常处理

#### 8. 安全与输入校验

- 文件上传：MIME 类型白名单、文件大小上限、文件名路径遍历检测（`../`、null byte、URL 编码变体）
- 用户资源隔离：项目、素材、会话、流水线 run、发布账号等资源均按账号归属校验
- 工具权限：`ToolRegistry` 支持工具级 `required_permission`，为 Agent tool-use 权限治理和最小权限调用预留接口

---

### 主要成果

- 完整实现从“用户选图 + 输入脚本”到“成片发布抖音”的端到端自动化链路，覆盖 10+ 个短视频生产环节
- 在 LangGraph 多 Agent 编排基础上补齐 MCP Server、RAG 检索增强、Agent Analytics、结构化日志、容器化和 CI/CD，形成更接近生产级 Agent 应用的工程闭环
- 接入 7 类外部 AI 模型服务，全部实现 Mock / Real 双模式，开发态无需任何 API Key 即可跑通主链路
- 实现可观测、可恢复、可局部重试的长任务 Agent 系统：Checkpoint 断点续跑 + 超时熔断 + 并发限流 + QA 自动重跑，保障长时任务可靠交付
- 新增覆盖 Agent 工具、RAG、MCP、Analytics 和 BaseAgent 生命周期的测试集，为后续重构和面试演示提供稳定回归保障

---

### 面试核心话术

> 项目不是简单调用视频生成 API，而是把短视频生产拆成可编排、可观测、可恢复的多 Agent 工作流。我在 LangGraph 编排层上补齐了 MCP 工具协议、Qdrant RAG 上下文增强、Agent 级 Analytics、结构化日志、Docker/CI/CD 和测试覆盖，重点解决真实 Agent 应用中的工具治理、长任务可靠性、失败定位和工程交付问题。
