# 项目代码地图

> 生成时间: 2026-05-08 | 只读盘点，未修改任何源代码

---

## 1. 顶层目录结构

| 路径 | 职责 |
|------|------|
| `backend/` | FastAPI 后端应用，Python 3.11 |
| `frontend/` | Vue 3 + Vite 前端应用 |
| `data/` | 本地数据存储：SQLite 数据库、视频上传、生成结果 |
| `docs/` | 项目文档（中文 Markdown） |
| `materials/` | 素材库：分类图片/视频原材料 |
| `examples/` | 示例素材和参考成品 |
| `scripts/` | 开发工具脚本 |
| `.github/workflows/` | GitHub Actions CI/CD 流水线 |
| `docker-compose.yml` | 编排 3 个服务：Qdrant、backend、frontend |
| `.env.example` | 全量环境变量模板，含注释说明 |
| `README.md` | 英文项目说明 |
| `README-zh-CN.md` | 中文项目说明 |

---

## 2. backend/app 内部结构

### 根文件与基础设施（核心入口）

| 文件 | 职责 | 标注 |
|------|------|------|
| `main.py` | FastAPI app 创建、路由注册、lifespan 管理、静态文件挂载 | **核心入口** |
| `bootstrap.py` | 依赖注入：创建 analyzer/generator/compositor/TTS/LLM/executor 实例 | **核心入口** |
| `core/config.py` | Pydantic Settings，从 `.env` 加载所有配置（145 行） | |
| `core/security.py` | JWT 鉴权、token 验证、当前用户依赖注入（202 行） | |
| `core/http.py` | 全局异常处理、CORS 中间件配置（88 行） | |
| `core/logging.py` | structlog 结构化日志初始化（72 行） | |
| `db/session.py` | SQLAlchemy 异步引擎、session 工厂、Base 声明类（59 行） | **核心入口** |

---

### agents/core/ — Agent 基础设施

| 文件 | 职责 | 标注 |
|------|------|------|
| `base.py` | 定义 `BaseAgent` 抽象类、`AgentContext`、`AgentResult` | **核心入口** |
| `tool_registry.py` | 工具注册表、`ToolDefinition`、技能发现与 Claude/OpenAI 格式转换 | **核心入口** |

---

### agents/stages/ — 各 Agent 具体实现

| 文件 | 职责 | 标注 |
|------|------|------|
| `orchestrator.py` | 解析用户需求、解析图片资产、构建执行上下文（488 行） | 核心 |
| `orchestrator_utils.py` | 图片摘要、平台/风格归一化工具函数 | 辅助 |
| `requirement_parser.py` | 从用户输入提取平台/风格/时长意图 | 辅助 |
| `requirement_utils.py` | prompt 构建和需求合并工具（251 行） | 辅助 |
| `prompt_engineer.py` | 生成分镜方案、旁白脚本、时序和视觉 prompt（442 行） | 核心 |
| `prompt_engineer_utils.py` | 时长计算和 prompt 格式化工具 | 辅助 |
| `audio_subtitle.py` | 调用 TTS 生成音频，生成 SRT 字幕 | 核心 |
| `video_generator.py` | 按分镜调用图生视频 API 生成各镜头片段 | 核心 |
| `video_editor.py` | 调用 FFmpeg 合成最终视频（拼接+混音+字幕） | 核心 |
| `qa_reviewer.py` | LLM 驱动的生成质量审核，输出通过/重试建议（282 行） | 核心 |
| `replication_planner.py` | 从参考视频规划复刻方案（1033 行，最大文件） | 核心 |
| `llm_diagnostics.py` | LLM 错误分类和短错误消息生成 | 辅助 |

---

### agents/executors/ — 流水线执行引擎

| 文件 | 职责 | 标注 |
|------|------|------|
| `shared.py` | `PipelineExecutorSupportMixin`：数据库 checkpoint 存储 | 辅助 |
| `pipeline.py` | 顺序执行器：orchestrator→prompt→audio+video→editor→qa（361 行） | **核心入口** |
| `langgraph/executor.py` | LangGraph 图执行器，DAG 方式运行各 Agent 节点 | **核心入口** |
| `langgraph/nodes.py` | LangGraph 各节点函数定义（237 行） | 核心 |
| `langgraph/state.py` | 图执行全局状态 Pydantic 模型 | 核心 |
| `langgraph/exceptions.py` | 自定义异常：`WaitingConfirmation`、`WaitingPromptReview` | 辅助 |

---

### agents/chat/ — 对话式 Agent

| 文件 | 职责 | 标注 |
|------|------|------|
| `agent.py` | 自动模式对话 Agent，含工具路由和多轮管理（1203 行） | **核心入口** |

---

### agents/skills/ — 运行时技能系统

| 文件 | 职责 | 标注 |
|------|------|------|
| `spec.py` | 技能规格 dataclass | 辅助 |
| `loader.py` | 从子目录动态加载技能（283 行） | 核心 |
| `analyze_video.py` | 视频分析技能声明 | 辅助 |
| `generate_video.py` | 视频生成技能声明 | 辅助 |
| `replicate_video.py` | 视频复刻技能声明 | 辅助 |
| `analyze-video/runtime.py` | 视频分析技能运行时处理器 | 辅助 |
| `generate-video/runtime.py` | 视频生成技能运行时处理器 | 辅助 |
| `replicate-video/runtime.py` | 视频复刻技能运行时处理器 | 辅助 |

---

### models/ — SQLAlchemy ORM 模型

| 文件 | 职责 |
|------|------|
| `user.py` | 用户认证和个人信息 |
| `project.py` | 项目容器 |
| `pipeline.py` | `PipelineRun` 和 `AgentExecution` 执行记录 |
| `agent_state.py` | Agent 执行状态 checkpoint（390 行） |
| `agent_memory.py` | Mem0 语义记忆记录 |
| `video_upload.py` | 参考视频上传记录 |
| `video_analysis.py` | 视频分析结果 |
| `material.py` | 素材库条目 |
| `material_selection.py` | 单次生成的已选素材 |
| `model_image.py` | 图像模型引用 |
| `prompt.py` | 生成的分镜 prompt |
| `generated_video.py` | 生成的视频片段 |
| `talking_head.py` | 口播头像生成记录 |
| `timeline.py` | 时间轴和剪辑合成 |
| `usage.py` | Token/费用用量追踪 |
| `background_template.py` | 背景模板库 |
| `social_account.py` | 社交媒体账号凭证 |
| `video_delivery.py` | 视频投递到社交平台记录 |
| `repository_asset.py` | 长期视频仓库存储 |
| `auto_chat.py` | 自动模式对话会话 |
| `api_key.py` | API Key 管理 |
| `external_video_job.py` | 外部视频任务引用 |

---

### routers/ — FastAPI 路由层

| 文件 | 职责 | 标注 |
|------|------|------|
| `auth.py` | 登录/登出/token 刷新 | |
| `api_keys.py` | API Key 增删查 | |
| `projects.py` | 项目 CRUD | |
| `upload.py` | 视频文件上传 | |
| `analysis.py` | 视频分析触发和状态查询 | |
| `pipeline.py` | 流水线编排、执行、进度监控（1400 行） | **核心入口** |
| `auto_sessions.py` | 自动模式对话会话管理和流水线编排（1124 行） | **核心入口** |
| `materials.py` | 素材库浏览和选择（358 行） | |
| `prompts.py` | prompt 生成和编辑（236 行） | |
| `generation.py` | 视频生成状态和结果查询（268 行） | |
| `timeline.py` | 时间轴编辑和合成（220 行） | |
| `talking_head.py` | 口播头像生成（478 行） | |
| `background_templates.py` | 背景模板库和选择（350 行） | |
| `repository.py` | 视频仓库资产管理（349 行） | |
| `public_video_jobs.py` | 外部视频任务提交（345 行） | |
| `social_accounts.py` | 社交媒体账号管理 | |
| `examples.py` | 示例素材和模板 | |
| `system.py` | 系统健康检查和配置 | |
| `analytics.py` | 用量统计和指标（292 行） | |

---

### services/ — 业务逻辑层

| 文件 | 职责 | 标注 |
|------|------|------|
| `llm_service.py` | LLM 抽象接口 + Qwen 实现（268 行） | **核心入口** |
| `llm/qwen_client.py` | Qwen API 封装：视觉/文本/结构化生成（435 行） | **核心入口** |
| `llm/transport.py` | Qwen HTTP 传输抽象（179 行） | 辅助 |
| `llm/payloads.py` | Qwen 请求 payload 构建器（100 行） | 辅助 |
| `llm/errors.py` | Qwen 错误处理和重试逻辑（22 行） | 辅助 |
| `video_analyzer.py` | 视频分析抽象接口（含 mock 和 Qwen3VL 实现） | 核心 |
| `video_generation/base.py` | 视频生成基类、`GenerationTask`/`GenerationStatus`（116 行） | 核心 |
| `video_generation/providers.py` | Seedance 和 Kling 提供商实现（356 行） | 核心 |
| `video_generation/router.py` | 按模型名路由到对应提供商（93 行） | 辅助 |
| `tts_service.py` | TTS 抽象接口（含 mock 和 Qwen TTS 实现） | 核心 |
| `video_editing/composer.py` | FFmpeg 视频合成（拼接/混音/字幕/BGM，463 行） | 核心 |
| `video_editing/helpers.py` | 字幕对齐和转场工具（283 行） | 辅助 |
| `keyframe_extractor.py` | 从视频提取关键帧（209 行） | 核心 |
| `image_compositor.py` | 口播图像合成 | 辅助 |
| `lipsync_generator.py` | 口播唇形同步生成 | 辅助 |
| `material_service.py` | 素材资产管理 | 辅助 |
| `upload_validation.py` | 文件校验和重复检测（217 行） | 辅助 |
| `artifact_cleanup.py` | 临时文件清理 | 辅助 |
| `pipeline_artifact_repository.py` | 资产去重和持久化存储（400 行） | 核心 |
| `video_delivery.py` | 社交平台视频投递和预览生成（408 行） | 核心 |
| `public_video_jobs.py` | 外部视频任务提交和轮询（321 行） | 辅助 |
| `usage_service.py` | Token 用量追踪和费用记录（364 行） | 核心 |
| `agent_memory.py` | Mem0 语义记忆读写（212 行） | 辅助 |
| `mem0_service.py` | Mem0 客户端初始化 | 辅助 |
| `rag_service.py` | 向量检索增强生成上下文（251 行） | 辅助 |
| `social_accounts.py` | 社交账号凭证存储（272 行） | 辅助 |
| `background_template_learning.py` | 背景模板偏好学习 | 辅助 |
| `api_keys.py` | API Key 管理 | 辅助 |
| `director_plan_chat.py` | 导演规划对话接口 | 辅助 |
| `media_utils.py` | 媒体文件工具函数 | 辅助 |

---

### prompts/ — LLM 系统 Prompt

| 文件 | 职责 |
|------|------|
| `system_prompts.py` | Orchestrator/PromptEngineer/Audio/Editor/QAReviewer 的 system prompt |
| `chat_agent_prompts.py` | ChatAgent 的 system prompt |

---

### mcp/ — Model Context Protocol

| 文件 | 职责 |
|------|------|
| `server.py` | MCP 服务器实现，暴露 VidGen 能力给外部 Agent（265 行） |
| `router.py` | MCP 路由，挂载到 FastAPI |

---

### schemas/ — Pydantic 请求/响应模型

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 流水线执行、投递和产物 schema（290 行） |
| `auto_chat.py` | 自动模式对话 schema |
| `project.py` | 项目增删改查 schema |
| `prompt.py` | Prompt 相关 schema |
| `timeline.py` | 时间轴和剪辑 schema |
| `generation.py` | 生成请求/响应 schema |
| `material.py` | 素材选择 schema |
| `video.py` | 视频上传/分析 schema |
| `talking_head.py` | 口播生成 schema |
| `auth.py` | 认证请求/响应 schema |
| `social_account.py` | 社交账号集成 schema |
| `background_template.py` | 背景模板 schema |

---

## 3. frontend/src 内部结构

### App.vue / main.ts（根文件）

| 文件 | 行数 | 职责 |
|------|------|------|
| `App.vue` | 339 | 主组件：管理认证、项目状态和页面路由切换 |
| `main.ts` | — | Vue app 初始化和挂载 |

---

### components/（核心文件 > 100 行）

| 文件 | 行数 | 职责 |
|------|------|------|
| `pipeline/AutoModeStudio.vue` | 1278 | 自动模式集成 UI：上传/分析/素材/生成/投递全流程 |
| `dashboard/UsageDashboardPage.vue` | 404 | Token 用量指标和统计看板 |
| `dashboard/ApiKeyManagementPanel.vue` | 341 | API Key 创建和管理 UI |
| `repository/RepositoryPage.vue` | 291 | 视频仓库和资产管理 UI |
| `timeline/TimelineEditor.vue` | 218 | 多轨时间轴剪辑编辑器 |
| `prompt/PromptWorkspace.vue` | 222 | Prompt 编辑和对话历史 |
| `generation/GenerationPanel.vue` | 168 | 视频生成状态和进度 |
| `materials/MaterialBrowser.vue` | 190 | 素材库浏览和选择 UI |

---

### api/ — Axios 请求封装

| 文件 | 行数 | 职责 |
|------|------|------|
| `client.ts` | 41 | Axios 实例、重试逻辑、全局错误处理 |
| `pipeline.ts` | 133 | 流水线编排接口 |
| `autoSessions.ts` | 108 | 自动模式会话接口 |
| `materials.ts` | 77 | 素材浏览和选择接口 |
| `talkingHead.ts` | 72 | 口播生成接口 |
| `prompts.ts` | 44 | Prompt 生成和对话接口 |
| 其余 api/ 文件 | <50 | 对应各资源的增删改查封装 |

---

### types/ — TypeScript 类型定义

| 文件 | 行数 | 职责 |
|------|------|------|
| `pipeline.ts` | 303 | 流水线执行、投递、任务类型（最大类型文件） |
| `assets.ts` | 107 | 素材、图像、资产类型 |
| `prompts.ts` | 75 | Prompt 和对话消息类型 |
| `timeline.ts` | 58 | 时间轴剪辑和合成类型 |
| `repository.ts` | 45 | 仓库资产类型 |
| `auth.ts` | 41 | 用户认证和 JWT 类型 |

---

### stores/ — 响应式状态管理

| 文件 | 职责 |
|------|------|
| `projectStore.ts` | 项目和当前步骤状态 |
| `pipelineStore.ts` | 流水线执行和自动模式状态 |
| `timelineStore.ts` | 时间轴剪辑和合成状态 |

---

### lib/ — 工具库

| 文件 | 行数 | 职责 |
|------|------|------|
| `sseClient.ts` | 138 | SSE 客户端，用于流式响应 |
| `utils.ts` | — | 通用工具函数 |

---

### composables/ — Vue 组合式函数

| 文件 | 行数 | 职责 |
|------|------|------|
| `useApiKeyManagement.ts` | 270 | API Key 管理逻辑 |
| `useToast.ts` | — | Toast 通知组合函数 |

---

## 4. 配置文件清单

| 文件 | 职责 |
|------|------|
| `.env.example` | 全量环境变量模板（API Key、mock 开关、存储路径） |
| `.env` | 实际运行时配置（不入 git） |
| `docker-compose.yml` | 编排 Qdrant、backend、frontend 三服务 |
| `.github/workflows/ci.yml` | CI：backend lint/test、frontend lint/build、Docker 镜像构建 |
| `backend/pyproject.toml` | Python 项目元数据、依赖、pytest/ruff/coverage 配置 |
| `backend/requirements.txt` | 生产依赖 |
| `backend/requirements-dev.txt` | 开发依赖 |
| `backend/alembic.ini` | Alembic 数据库迁移配置，指向 `backend/alembic/` |
| `backend/uv.lock` | uv 锁文件 |
| `frontend/package.json` | Node 依赖：Vue 3、Vite、Tailwind、Axios、TypeScript |
| `frontend/vite.config.ts` | Vite 配置：Vue 插件、Tailwind、开发代理 `/api → :8000` |
| `frontend/tsconfig.json` | TypeScript 配置入口，引用 app 和 node 子配置 |
| `frontend/tsconfig.app.json` | 应用侧 TS 配置 |
| `frontend/tsconfig.node.json` | Node 侧 TS 配置 |
| `frontend/eslint.config.js` | ESLint 规则配置 |
| `frontend/nginx.conf` | 生产环境 Nginx 静态文件和 API 反代配置 |

---

## 5. 文档与脚本清单

### docs/

| 文件 | 职责 |
|------|------|
| `README-zh-CN.md` | 中文文档导航索引 |
| `VidGen_产品介绍文档 (1).md` | 产品定位、适用场景、功能概览、8 大页面说明 |
| `VidGen_系统设计文档 (1).md` | 四层架构、7 Agent 设计、数据库表结构、部署方案 |
| `audit/CODE_MAP.md` | 本文件：项目代码地图 |
| `api/` | 第三方 API 集成文档 |
| `architecture/` | 系统架构对比文档 |
| `development/` | 开发规范：LangGraph、Skill、DB |
| `guide/` | 本地开发和 Python 依赖指南 |
| `plans/` | 功能规划文档 |
| `reports/` | 复刻报告 |
| `portfolio/` | 简历/作品集素材 |
| `archive/` | 历史方案和审计记录 |

### scripts/

| 文件 | 职责 |
|------|------|
| `backend-dev.sh` | 启动 FastAPI 开发服务器（热重载） |
| `backend-test.sh` | 运行 pytest 测试套件 |
| `backend-lint.sh` | 运行 ruff 代码检查 |
| `backend-install-dev.sh` | 安装开发依赖 |
| `check-code-file-lines.sh` | 检查业务代码文件是否超过 500 行限制（159 行） |
| `code-file-line-exceptions.txt` | 超行数白名单（含 4 个已批准超限文件） |
