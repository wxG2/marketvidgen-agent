# VidGen 系统重构计划：对齐产品介绍文档与系统设计文档

## Context

VidGen 当前处于"活跃开发/后期原型"阶段，已有可运行的多 Agent 管线、Chat 界面、20+ 数据模型和 29 个测试文件。但与产品介绍文档（7 大功能模块、8 个页面）和系统设计文档（四层架构、7 Agent 流水线、PostgreSQL/Redis/MinIO 基础设施）之间存在显著差距。本计划旨在分阶段将系统对齐到设计文档的目标状态，同时最大化复用现有代码。

---

## 核心差距总览

| 维度 | 当前状态 | 目标状态 |
|------|---------|---------|
| Agent | orchestrator/prompt_engineer/audio_subtitle/video_generator/video_editor/qa_reviewer/replication_planner | ReferenceAgent/ScriptAgent/StoryboardAgent/GenerationAgent/AssemblyAgent/QAAgent/AnalyticsAgent |
| 数据库 | SQLite only | PostgreSQL(prod) + SQLite(dev) + Redis + MinIO |
| GraphState | 10 字段 `LangGraphPipelineState` | 15+ 字段含类型化子结构 |
| 前端路由 | 无 Router，单 App.vue 控制 | Vue Router 4 + 8 个页面 |
| 前端状态 | 自定义 reactive stores | Pinia |
| 前端 UI | Tailwind 自定义组件 | Element Plus + Tailwind |
| 实时通信 | SSE | WebSocket |
| 任务队列 | 进程内异步 | Celery + Redis |
| 对象存储 | 本地文件系统 | MinIO |
| 可观测性 | structlog(基础) | structlog + OpenTelemetry + Prometheus |

---

## Phase M1: 骨架重构 (Week 1-2)

### 后端

1. **新建目录结构**
   - 创建 `backend/app/tools/` — 工具实现（从 agents 解耦）
   - 创建 `backend/app/graph/` — LangGraph StateGraph 定义
   - 创建 `backend/app/infra/` — Redis、MinIO、Qdrant 适配器

2. **重新设计 GraphState**
   - 文件: `backend/app/graph/state.py`（新建）
   - 定义完整 `GraphState` TypedDict + 子结构 (`ReferenceAnalysis`, `Script`, `ScriptSection`, `Shot`, `ClipAsset`, `VideoAsset`, `AnalyticsReport`, `ErrorInfo`)
   - 替换现有 `backend/app/agents/executors/langgraph/state.py`

3. **增强 ToolRegistry**
   - 文件: `backend/app/agents/core/tool_registry.py`（现有，可复用）
   - 注册设计文档 2.2.3 的 12 个工具为 stub

4. **创建 ReferenceAgent 骨架**
   - 文件: `backend/app/agents/stages/reference_agent.py`
   - 签名: `async def reference_agent(state: GraphState) -> GraphState`
   - 复用 `replication_planner.py` 中关键帧提取逻辑

5. **数据库多方言支持**
   - 修改 `backend/app/database.py` — 根据 `DATABASE_URL` 切换 SQLite/PostgreSQL
   - 创建新 Alembic 迁移适配 PostgreSQL

6. **Docker 开发环境**
   - 新建 `docker-compose.dev.yml`（PostgreSQL + Redis + Qdrant + MinIO）
   - 新建 `Makefile`（make dev/test/lint/migrate）

### 前端

7. **添加 Vue Router 4** — `frontend/src/router/index.ts`，8 个路由定义
8. **添加 Pinia** — 转换现有 stores
9. **添加 Element Plus** — 安装配置到 `main.ts`
10. **切换到 pnpm** — 删除 package-lock.json

### 可复用代码
- `BaseAgent` + `AgentContext` (`agents/core/base.py`) ✅
- `ToolRegistry` (`agents/core/tool_registry.py`) ✅
- 认证代码 (`auth.py`, `routers/auth.py`) ✅
- `database.py` 命名约定 ✅

---

## Phase M2: 主链路贯通 (Week 3-5)

### 7 Agent 实现

1. **ReferenceAgent** (完整实现)
   - 新建 `backend/app/tools/video_download.py` (yt-dlp)
   - 新建 `backend/app/tools/shot_detect.py` (PySceneDetect)
   - 复用 `services/keyframe_extractor.py` + `services/qwen_client.py`
   - 输出 `ReferenceAnalysis` 写入 GraphState

2. **ScriptAgent**
   - 从 `prompt_engineer.py` 提取脚本生成逻辑
   - 添加 Qdrant few-shot 检索（扩展 `rag_service.py`）
   - JSON mode 输出 + `Script` schema 校验
   - HITL 用户确认门

3. **StoryboardAgent**
   - 从 `prompt_engineer.py` 提取分镜逻辑
   - 实现生成策略决策树 (t2v/i2v/vace_swap/reuse_clip)
   - 输出 `List[Shot]`

4. **GenerationAgent**
   - 新建 `backend/app/tools/comfyui_client.py` (ComfyUI HTTP API)
   - 重构 `services/video_generator.py` 支持 Seedance + ComfyUI 双通路
   - 按策略分批调度 + 镜头级 ClipQA

5. **AssemblyAgent**
   - 合并 `AudioSubtitleAgent` + `VideoEditorAgent` 逻辑
   - 复用 `services/tts_service.py` + `services/video_editor_service.py`
   - 新建 `backend/app/tools/asr_align.py` (whisper-timestamped)
   - BGM 混音 + 响度归一

### LangGraph 管线

6. **新建 StateGraph** — `backend/app/graph/pipeline.py`
   - 拓扑: reference → script → storyboard → generation → assembly → END
   - 保留旧执行器在 `PIPELINE_ENGINE=legacy` 特性标记后

7. **新建执行器** — `backend/app/graph/executor.py`
   - 对接现有 `routers/pipeline.py`

8. **MinIO 客户端** — `backend/app/infra/minio_client.py`

---

## Phase M3: QA 与 HITL (Week 6)

1. **增强 QAAgent** — 基于现有 `qa_reviewer.py`
   - 新增: 时长偏差检测、音视频同步检测(80ms)、CLIP 视觉一致性(≥0.7)、字幕可读性
   - 实现 `qa_router` 回退路由 (→storyboard/generation/assembly/human_review)

2. **Redis Checkpoint** — `backend/app/infra/redis_client.py`
   - 实现 `RedisCheckpointer` 用于 `graph.compile(checkpointer=...)`
   - Key: `vidgen:checkpoint:{task_id}:{node}:{phase}`，7 天 TTL

3. **HITL 机制增强**
   - 扩展现有 `WaitingConfirmation` 异常体系
   - WebSocket 通知 + 恢复端点

4. **StateGraph 条件边** — QA 回退路由 + human_review 节点

---

## Phase M4: 流量回看 (Week 7-8)

1. **AnalyticsAgent** — `backend/app/agents/stages/analytics_agent.py`（全新）
   - 异步定时任务，独立于主 StateGraph
   - 新建 `backend/app/tools/traffic_fetch.py`（抖音开放平台 API）
   - 复用现有 social account OAuth 基础设施

2. **Qdrant 集合扩展**
   - `script_examples`: 脚本特征 + 流量标签
   - `reference_videos`: 参考视频特征
   - `material_assets`: 素材语义检索

3. **数据模型** — 新增 `analytics` 表 + `videos` 表 + Alembic 迁移

4. **Celery Worker** — `backend/app/celery_app.py`
   - 24h/72h/168h 定时抓取已发布视频流量数据
   - 加入 docker-compose.dev.yml

---

## Phase M5: 工程化 (Week 9-10)

1. **MCP Server 增强** — 暴露 7 Agent 能力为 MCP 工具
2. **OpenTelemetry** — `backend/app/infra/telemetry.py`，FastAPI 中间件 + 管线 trace 传播
3. **docker-compose.prod.yml** — 完整生产环境编排
4. **CI/CD 增强**
   - `.github/workflows/ci.yml` 添加 mypy、Alembic 迁移验证
   - Tag-based 部署流水线 (staging/production)
   - 前端单元测试 job

---

## Phase M6: 前端工作台 (Week 11-12)

### 8 个页面

| 页面 | 文件 | 来源 |
|------|------|------|
| 登录页 | `views/LoginView.vue` | 重构自 `components/auth/AuthPage.vue` |
| 工作台首页 | `views/DashboardView.vue` | 重构自 `components/dashboard/` |
| 创建任务页 | `views/CreateTaskView.vue` | 全新 |
| 任务进度页 | `views/TaskProgressView.vue` | 全新 — 7 节点 WebSocket 进度 |
| 视频预览编辑页 | `views/VideoPreviewView.vue` | 全新 — Video.js + 镜头时间轴 |
| 流量回看页 | `views/AnalyticsView.vue` | 重构自 `components/analysis/` |
| 素材库管理 | `views/MaterialsView.vue` | 重构自 `components/materials/` |
| 团队权限中心 | `views/TeamView.vue` | 全新 |

### 基础设施

- **WebSocket 客户端** — `frontend/src/lib/wsClient.ts`，替换 SSE
- **Video.js 播放器** — `frontend/src/components/player/ShotTimeline.vue`
- **App.vue 重构** — 替换为 `<RouterView />` + layout wrapper

---

## Phase M7: 部署上线 (Week 13-14)

1. **Kubernetes 配置** — `infra/k8s/`（Deployments, StatefulSets, Services, Ingress）
2. **蓝绿部署** — tag 触发 (v*.*.* / v*.*.*-rc.*)
3. **生产加固** — 限流、JWT 刷新 Token 轮换、MinIO 签名 URL、Redis 哨兵

---

## Agent 映射关系（现有 → 目标）

| 现有 Agent | 目标 Agent | 策略 |
|-----------|-----------|------|
| ReplicationPlannerAgent | ReferenceAgent | **重构**: 复用视频下载+关键帧提取，新增 shot_detect/BGM 分析 |
| PromptEngineerAgent | ScriptAgent + StoryboardAgent | **拆分**: 脚本生成 → Script，分镜 → Storyboard |
| AudioSubtitleAgent + VideoEditorAgent | AssemblyAgent | **合并**: TTS+字幕+FFmpeg 合成 |
| VideoGeneratorAgent | GenerationAgent | **重构**: 新增 ComfyUI 调度，保留 Seedance 通路 |
| QAReviewerAgent | QAAgent | **增强**: 新增音频同步/CLIP 一致性/字幕检测 |
| OrchestratorAgent | (分解) | 需求解析 → ReferenceAgent，规划 → ScriptAgent |
| (无) | AnalyticsAgent | **全新**: 流量数据闭环 |

---

## 风险缓解

1. **向后兼容**: 保留旧 `LangGraphPipelineExecutor` 在 `PIPELINE_ENGINE=legacy` 标记后
2. **数据库迁移安全**: `database.py` 现有 `_is_sqlite` 检查可处理方言差异
3. **ComfyUI 可用性**: 通过 `VIDEO_GENERATOR_PROVIDER` 配置双通路 (seedance/comfyui)
4. **前端渐进迁移**: Vue Router 可与现有单页结构并存，逐页迁移

---

## 验证方式

每个阶段完成后的验证:
- **M1**: `make dev` 一键启动开发环境，单 Agent 可独立运行
- **M2**: 输入 URL → 产出 MP4（无 QA），端到端 pytest 通过
- **M3**: QA 失败可自动回退重试，任务可断点续跑
- **M4**: 模拟流量数据入库，影响下一轮 ScriptAgent 的 few-shot 检索
- **M5**: MCP 工具可调用，CI 全绿，监控看板可查
- **M6**: 用户可通过 Web 完成创建任务 → 查看进度 → 预览编辑 → 流量回看闭环
- **M7**: K8s 部署运行，蓝绿切换可执行

## GitHub 发布准备

- 更新 README.md 和 README-zh-CN.md 对齐新架构
- 添加 LICENSE 文件
- 清理 `.env.example` 对齐新基础设施
- 确保 `docker-compose.dev.yml` 一键可启动
- 添加 CONTRIBUTING.md 和开发者指南
