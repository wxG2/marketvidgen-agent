# capy

[English](./README.md)

[文档导航](./docs/README-zh-CN.md)

capy 是一个面向短视频生产的 AI 工作台，目前提供两种工作流：

- `一键生成`：通过对话式界面，直接用脚本和图片素材生成视频
- `手动模式`：按上传、分析、选素材、写提示词、生成、剪辑的步骤逐步完成

当前项目采用 Vue + Vite 前端和 FastAPI 后端。后端通过多 agent 流水线完成规划、复刻方案拆解、提示词设计、音频字幕生成、分镜视频生成、最终剪辑和质量审核；同时已经补齐 MCP Server、基于 Qdrant 的历史方案检索增强、Analytics API、结构化日志以及 Docker / CI 基础工程化。

## 给项目经理的快速导览

vidgen 不是单点的视频模型调用 Demo，而是一套围绕“项目、素材、会话、Agent 流水线、资产仓库、发布交付”组织起来的短视频生产系统。用户可以登录后创建项目，在自动模式里选择图片素材或参考视频，用自然语言提出创作要求，系统会把请求拆解为镜头方案、视频提示词、配音字幕、分镜视频片段和最终成片，并把关键中间产物沉淀到个人仓库，便于复看、复用和验收。

当前产品最完整的主链路是“自动模式一键生成”：素材选择和会话状态由前端持久化，ChatAgent 负责判断用户是在普通对话、视频分析、参考视频复刻还是正式生成，PipelineExecutor / LangGraphPipelineExecutor 再调度各阶段 Agent。默认编排引擎是 `langgraph`，默认视频模型选择是 `Seedance 1.5 Pro`，PromptEngineer 完成镜头方案后默认会暂停在 `waiting_prompt_review`，用户确认后才继续进入音频、视频、剪辑和 QA。

面向管理和验收，系统已经具备本地账号隔离、项目历史、模型用量统计、Agent 进度追踪、中间产物仓库、成片仓库、抖音授权发布草稿、管理员用户管理和外部 API Key 调用。最近还新增了 MCP Server（用于把内部能力开放给外部 Agent 客户端）、Analytics API（用于看各 Agent 成功率、耗时和 Token 消耗）以及结构化日志链路。面向工程扩展，系统将外部模型、媒体处理、Agent 状态、运行时 skill、语义记忆、观测指标和交付发布拆成独立模块，后续替换模型或增加平台时不需要重写整条业务流。

## 功能亮点

- 本地账号体系与 Cookie Session 登录，项目、素材、模板、历史按账号隔离
- 外部视频生成 API v1：调用方可用 `vg_` 前缀 API Key 一次性上传多张素材并创建视频任务，任务默认进入审核态，确认后继续生成并通过受保护接口下载成片
- 仪表盘新增独立 `API Keys` 页签：普通用户可自助创建、查看、停用外部调用凭证；管理员可按客户账号单独创建和管理 API Key
- 对话式一键生成界面，可在同一页面上传素材、选图、输入脚本并触发生成
- 提示词对话接口与自动模式中的普通 assistant 对话，在真实 Qwen 模式下都支持服务端流式返回，前端可逐段展示生成中的文本；自动模式还会在 skill 路由、参数提取和模型调用前输出灰字状态，便于确认请求是否已进入模型链路
- 自动模式 assistant 现在按 Claude 风格的目录式 runtime skills 工作：启动时只读取 `SKILL.md` frontmatter，命中后再按需加载 `SKILL.md` 正文、`schema.json` 和 `runtime.py`，避免每次都把全部 tool 定义发给模型
- 自动模式的 `generate_video` skill 只对“开始生成 / 输出视频 / 启动流水线”等明确生产意图触发；“生成营销视频设计方案”这类策划请求会留在普通对话里
- 普通视频生成链路中，`OrchestratorAgent` 是调度核心：它按状态机解析用户消息和图片，判断视频类型、平台、风格、目标时长，并把图片内容与图片路径传给后续内部节点；前端通过 pipeline SSE 展示这些状态迁移
- PromptEngineer 会产出镜头级导演方案并写入聊天消息；默认开启人工确认闸门，用户确认镜头方案后才继续生成视频
- 自动模式支持上传参考视频进入复刻模式，并在执行前确认复刻方案
- 个人中心角色背景模板库，支持预设角色模板、关键词自动生成人设草稿和任务后增量学习
- 手动模式，适合希望逐步控制每个环节的创作者
- 当前 Vue 自动模式界面默认视频生成模型为 `Seedance 1.5 Pro`，可在 `Seedance 1.5 Pro` / `Seedance 2.0` / `Kling v3` / `mock` 间选择
- MCP Server：通过 stdio 暴露 `list_materials`、`get_pipeline_status`、`search_project_history`、`list_agent_tools` 4 个工具，并提供 `GET /mcp/tools` 发现端点；其中 `search_project_history` 是基于数据库关键词的历史项目查询，不是向量检索
- 历史方案检索增强：Orchestrator 在生成方案前可通过 `RagService` 检索历史相似方案并注入 prompt 上下文；Qdrant 或 embedding 服务不可用时自动降级为空结果，不阻断主流程
- Analytics API：提供总览、各 Agent 成功率 / 耗时、QA 通过率、Token 消耗明细、Pipeline 趋势 5 个观测端点
- 结构化日志：支持 `LOG_FORMAT=text|json`，每个请求自动注入 `X-Request-ID`，方便排查长任务问题
- Docker / CI：已提供 backend / frontend / Qdrant 的 `docker-compose.yml`，并有 GitHub Actions 执行 lint、pytest、前端 build 和 Docker image build
- 多 agent 流水线：
  - `orchestrator`
  - `replication_planner`
  - `prompt_engineer`
  - `audio_subtitle`
  - `video_generator`
  - `video_editor`
  - `qa_reviewer`
- 项目级仪表盘，可查看 token 消耗与执行进度
- PromptEngineer、AudioSubtitle、VideoGenerator 的中间产物会自动写入仓库，并在自动模式右侧栏与个人仓库的 Agent 产物页可视化查看
- 成片完成后可查看抖音 / YouTube 卡片预览，并将视频保存到仓库
- 支持抖音账号 OAuth 授权；连接账号后，assistant 会自动生成抖音发布草稿，用户确认后再提交发布
- 支持关系型 Agent 记忆基础设施和 Mem0 语义记忆：普通对话可检索历史偏好，Orchestrator 可把平台与风格偏好作为上下文提示
- 支持 Qwen Omni、Qwen TTS，以及 WaveSpeed Kling、Volcengine Seedance 等视频生成能力

## 当前支持能力

- 仅输入素材生成提示词：
  当前已经支持基于上传素材进行分析与提示词生成，并正在向“仅素材自动生成完整脚本”这一闭环继续扩展。
- 输入素材和创作要求后自动生成分镜提示词：
  这是当前主流水线的核心能力，系统会区分“用户创作目标”和“最终旁白脚本”，结合图片内容自动生成 shot 级提示词，避免把“根据这些素材生成方案”这类元指令原样写入口播。
- 根据旁白脚本自动生成音频：
  后端已支持根据脚本文本直接生成配音与字幕时间轴；`voiceover_no_audio` 控制是否跳过 VidGen TTS/字幕，`video_model_no_audio` 单独控制 Seedance/Kling 模型原声，自动模式默认关闭模型原声。
- 多个短视频自动拼接并适配平台尺寸：
  系统可对多个短视频片段进行重排、裁剪、拼接、字幕合成，并输出抖音、小红书、B 站等目标平台尺寸。
- 流程可视化与中间产物入仓：
  前端支持展示 agent 进度、token 消耗以及中间结果，并允许用户取消当前流程；提示词方案、shot 级提示词、配音参数、音频、字幕和分镜视频会自动保存为 `RepositoryAsset`，可在右侧栏或个人仓库继续查看。
- 人工确认镜头方案：
  默认开启 `HUMAN_IN_LOOP_PROMPT_REVIEW=true`。PromptEngineer 完成后，系统会把镜头设计方案写入自动模式聊天消息，并把 pipeline 暂停为 `waiting_prompt_review`；当前前端支持查看镜头表格，并在确认前编辑旁白和视频提示词。
- QA 审核与有限自动重试：
  `QAReviewerAgent` 在剪辑后执行硬规则与 LLM 质量检查，覆盖缺失片段、时长偏差、音视频同步和提示词质量；默认开启 QA，失败时可按建议重跑上游节点，次数由 `MAX_QA_RETRIES` 控制。
- 多平台交付：
  自动模式完成后，系统会额外生成抖音与 YouTube 的卡片化预览，并支持保存成片到本地视频仓库。
- 抖音账号与发布：
  当前已从“固定 `.env` token 发布”切换为“用户级抖音账号授权 + 发布草稿确认 + 按授权账号发布”。
- 外部 API：
  当前已提供 `/v1/video-jobs`，使用 `Authorization: Bearer vg_...` 鉴权。调用方以 `multipart/form-data` 提交 `spec` JSON 和 `images` 文件；后端会自动创建私有项目、导入素材并启动同一条 pipeline。普通生成会停在 `shot_plan` 审核，带参考视频的复刻生成会停在 `replication_plan` 审核，确认后继续执行，最终视频只通过 `/v1/video-jobs/{job_id}/result` 下载。
- MCP 与开放工具发现：
  当前已提供 stdio MCP Server，可暴露 `list_materials`、`get_pipeline_status`、`search_project_history`、`list_agent_tools` 4 个工具；同时也提供 `GET /mcp/tools` 作为 HTTP 发现端点，方便调试和非 MCP 客户端查看 tool schema。
- 历史项目检索增强：
  当前 `RagService` 已实现基于 Qdrant + Qwen `text-embedding-v3` 的 retrieve / prompt formatting 能力，并保留 `index_pipeline_run(...)` 写入接口；`OrchestratorAgent` 在普通生成前会尝试检索相似历史方案作为 few-shot context。当前主流程尚未在 pipeline 完成后自动写入历史方案索引，若未配置 `QWEN_API_KEY`、Qdrant 不可达或初始化失败，系统会自动退回无检索模式。
- Agent 可观测性 API：
  当前已提供 `/api/analytics/overview`、`/api/analytics/agents`、`/api/analytics/qa`、`/api/analytics/token-usage`、`/api/analytics/pipeline-trends`，用于查看运行数、成功率、Agent 耗时、QA 结果和模型 Token 消耗。
- 容器化与基础 CI：
  当前仓库已包含 backend / frontend Dockerfile、多服务 `docker-compose.yml` 和 GitHub Actions CI；适合本地联调或向面试、演示环境快速交付。

## 架构说明

### 前端

- Vue 3
- TypeScript
- Vite
- Vue 响应式状态

主要入口文件：

- [frontend/src/main.ts](./frontend/src/main.ts)
- [frontend/src/App.vue](./frontend/src/App.vue)
- [frontend/src/components/pipeline/AutoModeStudio.vue](./frontend/src/components/pipeline/AutoModeStudio.vue)
- [frontend/src/components/dashboard/UsageDashboardPage.vue](./frontend/src/components/dashboard/UsageDashboardPage.vue)
- [frontend/src/components/repository/RepositoryPage.vue](./frontend/src/components/repository/RepositoryPage.vue)

### 后端

- FastAPI
- SQLAlchemy Async
- 默认使用 SQLite
- 可选接入 Qdrant，用于 Mem0 语义记忆与 RAG 历史方案检索
- 通过 `httpx` 调用第三方模型服务和 Qdrant REST API
- 支持 structlog 结构化日志、MCP Server、Analytics API

主要入口文件：

- [backend/app/main.py](./backend/app/main.py)
- [backend/app/README.md](./backend/app/README.md)
- [backend/app/agents/README.md](./backend/app/agents/README.md)
- [backend/app/services/llm/qwen_client.py](./backend/app/services/llm/qwen_client.py)
- [backend/app/mcp/server.py](./backend/app/mcp/server.py)
- [backend/app/routers/analytics.py](./backend/app/routers/analytics.py)
- [docs/README-zh-CN.md](./docs/README-zh-CN.md)

## Agent 流水线

一键生成流程由 `PipelineExecutor` 或默认的 `LangGraphPipelineExecutor` 统一编排：

1. `OrchestratorAgent`
   作为 Intake / Context Agent，负责把用户自由输入解析成平台、时长、风格、BGM、旁白脚本或创作目标等结构化参数，并整理图片内容、视觉角色和营销上下文。
2. `ReplicationPlannerAgent`
   当输入包含参考视频时优先执行，生成复刻方案并进入确认链路。
3. `PromptEngineerAgent`
   作为导演 Agent，根据 `orchestrator_plan` 中的导演输入上下文生成最终镜头方案、每个镜头的提示词、旁白片段和语音参数。
4. Prompt Review
   默认暂停等待用户确认镜头方案，确认后继续。
5. `AudioSubtitleAgent`
   生成配音音频和字幕时间轴。
6. `VideoGeneratorAgent`
   根据导演 Agent 产出的图片路径和提示词逐镜头生成视频片段。
7. `VideoEditorAgent`
   按顺序重排、裁剪并拼接视频片段，同时合入音频和字幕。
8. `QAReviewerAgent`
   检查缺失片段、时长偏差、音视频同步和整体质量，并在配置允许时触发有限重试。

核心编排文件：

- [backend/app/agents/pipeline.py](./backend/app/agents/pipeline.py)

集中管理的 system prompt：

- [backend/app/prompts/system_prompts.py](./backend/app/prompts/system_prompts.py)

## 模型与服务提供方

当前项目已接入或预留了以下模型能力：

- `Qwen Omni`
  用于调度、提示词规划、剪辑决策等结构化多模态推理
- `Qwen Omni video input`
  用于自动模式 `analyze_video` runtime skill 的参考视频解析
- `Qwen3 TTS`
  用于文本转语音
- `WaveSpeed Kling`
  在配置后可用于图生视频
- `Seedance 1.5 Pro`
  是当前配置下默认启用的图生视频提供方
- `Seedance 2.0`
  通过同一个 Volcengine Ark `ARK_API_KEY` 接入，可在自动模式生成模型中手动选择

相关代码文件：

- [backend/app/services/llm_service.py](./backend/app/services/llm_service.py)
- [backend/app/services/tts_service.py](./backend/app/services/tts_service.py)
- [backend/app/services/video_generation/router.py](./backend/app/services/video_generation/router.py)

## MCP、观测与工程化

### MCP Server

- 当前 MCP server 位于 `backend/app/mcp/server.py`，可通过 stdio 方式运行并接入 Claude Desktop 等 MCP 客户端
- 已暴露 4 个工具：`list_materials`、`get_pipeline_status`、`search_project_history`、`list_agent_tools`
- 同时提供 `GET /mcp/tools` HTTP 发现端点，返回工具名、描述和输入 schema，便于调试

如果你已经激活后端虚拟环境，可单独启动 MCP server：

```bash
cd backend
source venv/bin/activate
python -m app.mcp.server
```

### Analytics API

当前后端提供以下观测端点：

- `GET /api/analytics/overview`
- `GET /api/analytics/agents`
- `GET /api/analytics/qa`
- `GET /api/analytics/token-usage`
- `GET /api/analytics/pipeline-trends`

这些接口主要用于查看 Pipeline 运行数、Agent 成功率 / 耗时、QA 通过率、Token 消耗和趋势图数据。

### 日志、容器与 CI

- 后端已切换为 structlog 结构化日志，支持 `LOG_FORMAT=text|json`
- 每个 HTTP 请求会自动注入 `X-Request-ID` 响应头，便于关联日志和问题排查
- 仓库已提供 backend / frontend / Qdrant 三服务 `docker-compose.yml`
- GitHub Actions 会执行代码文件行数检查、backend lint + pytest、frontend lint + build，以及 Docker image build

## 项目结构

```text
vidgen/
├── .github/
│   └── workflows/
├── backend/
│   ├── alembic/
│   ├── app/
│   │   ├── agents/
│   │   ├── core/
│   │   ├── db/
│   │   ├── mcp/
│   │   ├── models/
│   │   ├── prompts/
│   │   ├── routers/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── llm/
│   │   │   ├── video_editing/
│   │   │   └── video_generation/
│   │   └── utils/
│   ├── tests/
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── uv.lock
├── docs/
│   ├── api/
│   ├── architecture/
│   ├── archive/
│   ├── development/
│   ├── plans/
│   ├── portfolio/
│   └── reports/
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── composables/
│   │   ├── lib/
│   │   ├── stores/
│   │   └── types/
│   ├── Dockerfile
│   ├── package.json
│   └── vite.config.ts
├── scripts/
├── docker-compose.yml
├── README.md
└── README-zh-CN.md
```

## 本地启动

### 0. 推荐：使用 Docker Compose

如果你想快速拉起完整联调环境，推荐直接使用仓库根目录的 `docker-compose.yml`：

```bash
cp .env.example .env
docker compose up --build
```

默认端口：

- 前端：`http://localhost`
- 后端：`http://localhost:8000`
- Qdrant：`http://localhost:6333`

其中 `docker-compose` 会自动启动：

- `frontend`：Vite 构建产物 + Nginx
- `backend`：FastAPI + FFmpeg
- `qdrant`：供 Mem0 / RAG 使用的向量检索服务

如果你只想体验主流程，也可以保留 `.env.example` 中的 mock 配置，不填真实模型 key 直接运行。

### 1. 启动后端

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

后端会从项目根目录的 `.env` 读取环境变量。
如果你希望在手动启动模式下启用 Mem0 语义记忆和 RAG 检索，建议另外先启动一个本地 Qdrant；如果没有 Qdrant，系统会记录 warning 并自动退回无向量检索模式，不影响主流程。

也可以使用根目录脚本：

- `./scripts/backend-install-dev.sh`：安装后端开发依赖
- `./scripts/backend-dev.sh`：启动后端开发服务
- `./scripts/backend-test.sh`：运行后端测试
- `./scripts/backend-lint.sh`：运行后端 lint

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

默认开发地址：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

首次进入前端时会先看到登录 / 注册页。首个注册账号会自动成为管理员。

## 外部 API 调用

登录后可在前端“仪表盘 -> API Keys”页签创建和管理外部调用凭证：普通用户可管理自己的 key，管理员可切到“客户密钥”视图为指定用户创建和停用 key。明文 API Key 只在创建成功时展示一次，服务端只保存哈希；后续列表只显示前缀、状态、scope 和最后使用时间。默认 scope 为 `video_jobs:create`、`video_jobs:read`、`video_jobs:review`；也可以通过后端接口显式传入更小权限集合。

如果你需要脚本化集成，仍然可以直接调用 `/api/api-keys` 和 `/api/admin/api-keys`：

```bash
curl -X POST http://localhost:8000/api/api-keys \
  -H "Content-Type: application/json" \
  --cookie "vidgen_session=..." \
  -d '{"name":"partner integration"}'
```

创建视频任务使用 `/v1/video-jobs`，认证头为 `Authorization: Bearer vg_...`。`spec` 是 JSON 字符串，`images` 是 1-100 个图片文件；可选 `reference_video` 会进入复刻方案确认链路，可选 `watermark` 会作为水印素材。

```bash
curl -X POST http://localhost:8000/v1/video-jobs \
  -H "Authorization: Bearer vg_xxx" \
  -H "Idempotency-Key: customer-order-123" \
  -F 'spec={"prompt":"用这些素材生成一条抖音大健康营销视频","platform":"douyin","duration_seconds":30}' \
  -F "images=@./image-1.png" \
  -F "images=@./image-2.png"
```

外部任务会返回 `job_id`。使用 `GET /v1/video-jobs/{job_id}` 查询状态；当状态为 `requires_review` 时，调用 `POST /v1/video-jobs/{job_id}/review` 审核分镜或复刻方案；完成后通过 `GET /v1/video-jobs/{job_id}/result` 下载 mp4。状态接口和审核数据会脱敏，不返回本机绝对路径。

更完整的发放和第三方接入说明见：

- [THIRD_PARTY_API_INTEGRATION.zh-CN.md](./docs/api/THIRD_PARTY_API_INTEGRATION.zh-CN.md)

## 环境变量

在 `vidgen/.env` 中填写你要启用的模型服务配置。

常用配置如下：

```env
PIPELINE_ENGINE=langgraph
DATABASE_URL=sqlite+aiosqlite:///./data/vidgen.db
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
MAX_UPLOAD_SIZE_MB=500
MAX_IMAGE_SIZE_MB=50
MAX_AUDIO_SIZE_MB=100
MAX_TIMELINE_ASSET_SIZE_MB=500
QWEN_API_KEY=
QWEN_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_OMNI_MODEL=qwen3-omni-flash
QWEN_TTS_MODEL=qwen3-tts-flash
LOG_FORMAT=text
MEM0_ENABLED=true
MEM0_EMBEDDING_MODEL=text-embedding-v3
MEM0_QDRANT_HOST=localhost
MEM0_QDRANT_PORT=6333

WAVESPEED_API_KEY=
WAVESPEED_API_URL=https://api.wavespeed.ai/api/v3
KLING_MODEL=kling-v3

ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
VIDEO_GENERATION_MODEL=seedance1.5-pro
SEEDANCE_MODEL=doubao-seedance-1-5-pro-251215
SEEDANCE_20_MODEL=doubao-seedance-2-0-260128
MAX_CONCURRENT_SHOTS=1
VIDEO_GENERATION_HTTP_RETRIES=2
VIDEO_GENERATION_HTTP_RETRY_BACKOFF_SECONDS=2.0
VIDEO_GENERATION_TIMEOUT_SECONDS=600
HUMAN_IN_LOOP_PROMPT_REVIEW=true
QA_REVIEW_ENABLED=true
QA_AUTO_RETRY_ENABLED=true
MAX_QA_RETRIES=1

FFMPEG_BIN=ffmpeg
DOUYIN_CLIENT_KEY=
DOUYIN_CLIENT_SECRET=
DOUYIN_REDIRECT_URI=https://your-domain.example/api/social-accounts/douyin/callback
DOUYIN_DEFAULT_SCOPE=user_info,video.create
FRONTEND_BASE_URL=http://127.0.0.1:5173

USE_MOCK_ANALYZER=true
USE_MOCK_LLM=true
USE_MOCK_GENERATOR=true
USE_MOCK_TTS=true
USE_MOCK_VIDEO_EDITOR=true
USE_MOCK_COMPOSITOR=true
USE_MOCK_LIPSYNC=true
```

上面这组配置更适合“本地先跑通界面和流程”的开发模式；如果要接入真实模型，把对应 `USE_MOCK_*` 改成 `false`，并补齐相应 provider key。
如果没有配置对应 key，部分服务会根据当前设置自动退回到 mock 实现。
上传入口会按文件类型做扩展名、声明 MIME、文件头和大小校验；参考视频默认上限由 `MAX_UPLOAD_SIZE_MB` 控制，图片由 `MAX_IMAGE_SIZE_MB` 控制，Talking Head 音频由 `MAX_AUDIO_SIZE_MB` 控制，时间线资产由 `MAX_TIMELINE_ASSET_SIZE_MB` 控制。

如果要启用“连接抖音账号并发布”，需要在 `.env` 中额外配置：

- `DOUYIN_CLIENT_KEY`：抖音开放平台网站应用的 Client Key，用于生成扫码授权 URL
- `DOUYIN_CLIENT_SECRET`：抖音开放平台网站应用的 Client Secret，用于回调用 `code` 换取 `access_token`
- `DOUYIN_REDIRECT_URI`：已在抖音开放平台网站应用中登记的 HTTPS 授权回调地址

抖音连接采用 OAuth 扫码授权：用户不需要手动粘贴 token，打开授权页后由抖音展示二维码；但服务端仍必须配置 Client Key / Client Secret，否则无法生成有效扫码链接或把回调 `code` 换成发布所需的 `access_token`。此外，你还需要在抖音开放平台为当前应用申请 `DOUYIN_DEFAULT_SCOPE` 中对应的视频发布能力，并把回调地址配置为上面的 `DOUYIN_REDIRECT_URI`。

注意：抖音开放平台要求网站应用授权回调使用已登记的 HTTPS 地址，`http://127.0.0.1` 这类本地回调会被拒绝。本地开发可使用公网 HTTPS 隧道，或直接配置部署环境的 HTTPS 回调地址。

未配置或回调地址不合法时，系统仍会展示抖音卡片预览；点击“连接抖音账号”会返回可读诊断，不会完成账号授权与发布。

## 当前产品流程

### 一键生成

- 登录账号
- 创建或打开一个项目
- 保持在自动模式对话工作台
- 可选：在个人中心选择一个预设角色，或输入关键词让 AI 自动生成角色背景草稿后保存
- 上传素材文件夹或单张图片
- 可选：上传参考视频进入复刻模式
- 在左侧会话栏中切换历史会话，或新开一个会话继续创作
- 选择图片素材，或从仓库选择已有图片 / 视频作为当前会话素材
- 输入创作要求或明确的旁白脚本并发送
- 如果是复刻模式，先确认或调整系统给出的复刻方案
- 系统先生成镜头级导演方案和视频提示词；默认需要确认镜头方案后才继续生成短视频片段和最终合成视频
- 如启用配音，则同时生成配音和字幕；剪辑完成后进入 QA 审核，必要时按建议有限重试上游节点
- 在同一界面查看 agent 流程进度
- 在右侧栏查看提示词、音频、字幕、视频片段等中间产物；这些产物也会进入个人仓库的 Agent 产物页
- 成片完成后查看抖音 / YouTube 卡片预览
- 成片会自动保存到视频仓库
- 如果当前平台是抖音且账号已连接，assistant 会自动生成一条抖音发布草稿消息
- 用户可在草稿卡片中修改标题、文案、话题和封面标题后确认发布

### 手动模式

- 上传参考视频
- 执行视频分析
- 查看推荐素材
- 编辑提示词
- 生成视频片段
- 进入时间轴剪辑

## 常用命令

```bash
# 后端语法检查
python3 -m compileall backend/app

# 代码文件行数治理检查
./scripts/check-code-file-lines.sh

# 行数检查脚本自测
./scripts/check-code-file-lines.sh --self-test

# 前端生产构建
cd frontend && npm run build
```

## 代码文件规模治理

- 业务源码文件默认不应超过 500 行；超过时应优先考虑按架构边界拆分，而不是继续堆在同一个文件里。
- 500 行是架构提醒线，不是机械禁令。确实需要临时保留的大文件，必须写入 [scripts/code-file-line-exceptions.txt](./scripts/code-file-line-exceptions.txt)，并说明后续拆分方向。
- CI 会运行 [scripts/check-code-file-lines.sh](./scripts/check-code-file-lines.sh)。被 Git 跟踪的业务源码如果超过 500 行且不在例外清单中，会导致检查失败。
- 新代码优先拆成 router、service、schema、agent stage、component、composable、store、helper 等清晰边界，避免让单个文件无限增长。

## 说明

- 本地开发默认数据库是 SQLite。
- 生成结果、本地素材库和运行数据默认已加入 Git 忽略。
- 默认自动模式编排引擎是 `langgraph`；也可以通过 `PIPELINE_ENGINE=pipeline` 切到顺序执行器。
- 当前默认的视频生成路径使用 `Seedance 1.5 Pro`；自动模式的“模型”下拉项可切换 `Seedance 2.0` 或 `Kling`，对应服务仍需在 `.env` 中配置 `ARK_API_KEY` 或 WaveSpeed/Kling API key。
- 自动模式的时长、模型原声、系统配音、转场、BGM 和视频生成行为不再暴露为顶部按钮，统一由 `frontend/src/components/pipeline/AutoModeStudio.vue` 中的 `AUTO_PIPELINE_CODE_SWITCHES` 控制；默认关闭模型原声并开启 VidGen 系统配音。
- 自动模式生成 skill 使用 `user_request` 表达创作目标，`narration_script/script` 只用于用户明确提供的最终口播文案。
- 当前自动模式优先服务视频生成任务：当会话已选素材且用户明确要求生成 / 制作 / 输出视频时，ChatAgent 会直接启动 `generate_video`，并把原始用户消息作为 `user_request` 进入 pipeline；随后由 `OrchestratorAgent` 内部做需求理解和素材上下文整理。
- `HUMAN_IN_LOOP_PROMPT_REVIEW=true` 时，普通生成默认会先停在镜头方案确认；如果只想直接生成，可在请求里显式传 `review_prompts=false` 或关闭全局配置。
- `MEM0_ENABLED=true` 还需要 `QWEN_API_KEY` 和 `mem0ai` 依赖可用；初始化失败时系统会记录 warning 并退回无语义记忆模式。
- 自动模式当前流程失败后，可在聊天框输入 `continue` / `retry` / `继续`，前端会调用失败 Agent 重试接口，从最近失败阶段继续执行；如果分镜视频已经生成，会复用已生成片段并继续补音频、剪辑和 QA。
- 自动模式对话中的“中止对话”只中断当前 chat/SSE 与尚未完成的 tool 调用；已经创建的 `PipelineRun` 会继续在右侧执行状态中通过 pipeline SSE 更新，需要停止时再点“取消流程”。
- 个人中心当前采用更偏 `capybara` 风格的角色工作台：其他预设角色以图标卡展示，右侧只展示当前选中角色的背景信息用于确认。
- 角色关键词自动生成功能依赖后端 LLM；如果未配置真实模型，会回退到基于内置预设模板的本地生成逻辑。
- 抖音发布目前是“已连接抖音账号后，由 assistant 自动生成发布草稿，用户确认后提交”，不是静默自动发布。
- 自动模式里只有“不需要调用 runtime skill 的普通对话”会直接走 Qwen 原生流式分块；一旦自动路由命中某个 skill，系统会先按需展开该 skill 的 `SKILL.md / schema.json / runtime.py`，前端会看到不进入最终正文的灰字 `status`、`tool_call / tool_result` 事件流。
- 视频分析 skill 若模型返回空文本会直接报错提示检查模型调用 / 视频输入 / 服务配置，不再返回“视频分析完成”占位文案。
- 自动模式的视频分析 skill 通过 `LLMService.generate_text(..., video_paths=[...])` 调 Qwen 多模态能力；手动模式 `/api/projects/{project_id}/analyze` 的 `Qwen3VLAnalyzer` 真实实现仍是待接入状态，未开 mock 时会返回未实现错误。
- 抖音接口提交成功只表示 vidgen 已经把内容提交到开放平台，视频仍可能进入平台审核或仅自己可见阶段。
- 当前仓库更偏向本地开发与功能验证，尚未针对生产部署做完整加固。
- `PipelineCreateRequest` 已对平台、时长、生成模型、转场、BGM、音量等字段做 Pydantic 约束；不合法请求会在进入 Agent 流水线前返回 422。
- RAG 历史检索与 Mem0 默认依赖 Qdrant；若你只做基础演示，可以不启动 Qdrant，系统会自动降级为无语义检索模式。当前 RAG 检索注入已接入 Orchestrator，pipeline 完成后的自动索引写入仍需补齐。
- `GET /mcp/tools` 和 `/api/analytics/*` 都已经注册在主应用中，可直接用于演示开放能力和 Agent 可观测性。
- 若要在生产或容器环境中收集日志，建议把 `LOG_FORMAT` 设为 `json`，便于接入日志平台。

## License
MIT
在公开发布前，请补充你希望使用的开源协议。
