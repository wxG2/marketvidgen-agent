# 用户入口清单

> 生成时间: 2026-05-08 | 只读扫描，未修改任何源代码

---

## 1. 前端 UI 入口

所有前端入口集中在 `frontend/src/components/pipeline/AutoModeStudio.vue`（自动模式）和 `App.vue`（项目/认证）。

---

### 1.1 发送对话消息（自动模式核心入口）
- **入口**: AutoModeStudio.vue 底部输入框"发送"按钮（`@submit.prevent="sendMessage()"`）
- **前端函数**: `sendMessage()` → `chatWithAgent(projectId, sessionId, {...})`
- **后端 API**: `POST /api/projects/{project_id}/auto-sessions/{session_id}/chat`（SSE 流式）
- **调用链**: `routers/auto_sessions.py:chat_with_agent` → `agents/chat/agent.py:ChatAgent.run_stream` → 内部工具路由（分析/生成/复刻）
- **涉及表**: `auto_chat_sessions`, `auto_chat_messages`, `pipeline_runs`, `agent_executions`

---

### 1.2 启动流水线（一键生成）
- **入口**: AutoModeStudio.vue 顶部"启动生成"按钮（`@click="launch"`，需先有脚本）
- **前端函数**: `launch()` → `POST /api/projects/{project_id}/pipeline`
- **后端 API**: `POST /api/projects/{project_id}/pipeline`
- **调用链**: `routers/pipeline.py:launch_pipeline` → `launch_pipeline_task()` → `asyncio.create_task(_run_pipeline)` → `PipelineExecutor.execute` → orchestrator → prompt_engineer → audio_subtitle → video_generator → video_editor → qa_reviewer
- **涉及表**: `pipeline_runs`, `agent_executions`, `prompts`, `generated_videos`, `usage_records`

---

### 1.3 确认分镜方案（HITL）
- **入口**: AutoModeStudio.vue 分镜确认面板"确认"/"提交调整"按钮（`@click="confirmPlan(true/false)"`）
- **前端函数**: `confirmPlan(approved)` → `confirmReplicationPlan(projectId, runId, approved, adjustmentText)`
- **后端 API**: `POST /api/projects/{project_id}/pipeline/{run_id}/confirm-plan`
- **调用链**: `routers/pipeline.py:confirm_plan` → 解除 `WaitingConfirmation` 阻塞 → 恢复流水线执行
- **涉及表**: `pipeline_runs`, `agent_executions`

---

### 1.4 确认镜头 Prompt 审核（HITL）
- **入口**: AutoModeStudio.vue Prompt 审核面板"确认生成"按钮（`@click="confirmPromptReviewAction"`）
- **前端函数**: `confirmPromptReviewAction()` → `confirmPromptReview(projectId, runId, editedShots?)`
- **后端 API**: `POST /api/projects/{project_id}/pipeline/{run_id}/confirm-prompt-review`
- **调用链**: `routers/pipeline.py:confirm_prompt_review` → 解除 `WaitingPromptReview` 阻塞 → 恢复 video_generator
- **涉及表**: `pipeline_runs`, `prompts`

---

### 1.5 单镜头重试
- **入口**: AutoModeStudio.vue 镜头卡片"重试"按钮（`@click="retryShotAction(shotIdx)"`）
- **前端函数**: `retryShotAction(shotIdx)` → `retryShotIndices(projectId, runId, [shotIdx])`
- **后端 API**: `POST /api/projects/{project_id}/pipeline/{run_id}/retry-shot`
- **调用链**: `routers/pipeline.py:retry_shot` → 重新触发 video_generator 单镜头
- **涉及表**: `pipeline_runs`, `generated_videos`

---

### 1.6 取消当前流水线
- **入口**: AutoModeStudio.vue 进度区域"取消"按钮（`@click="cancelCurrentRun"`）
- **前端函数**: `cancelCurrentRun()` → `POST /api/projects/{project_id}/pipeline/{run_id}/cancel`
- **后端 API**: `POST /api/projects/{project_id}/pipeline/{run_id}/cancel`
- **调用链**: `routers/pipeline.py:cancel_pipeline` → 取消 `_pipeline_tasks[run_id]` asyncio.Task
- **涉及表**: `pipeline_runs`

---

### 1.7 保存成片到仓库
- **入口**: AutoModeStudio.vue 结果区"保存成片"按钮（`@click="saveDelivery"`）
- **前端函数**: `saveDelivery()` → `savePipelineVideo(projectId, runId, {title})`
- **后端 API**: `POST /api/projects/{project_id}/pipeline/{run_id}/delivery/save`
- **调用链**: `routers/pipeline.py:save_delivery` → `services/video_delivery.py:save_video_to_repository` → `models/repository_asset.py`
- **涉及表**: `video_deliveries`, `repository_assets`

---

### 1.8 生成抖音发布草稿
- **入口**: AutoModeStudio.vue 结果区"生成发布草稿"按钮（`@click="draftPublish"`）
- **前端函数**: `draftPublish()` → `createAutoSessionPublishDraft(projectId, sessionId, {platform:'douyin'})`
- **后端 API**: `POST /api/projects/{project_id}/auto-sessions/{session_id}/publish-drafts`
- **调用链**: `routers/auto_sessions.py:create_publish_draft` → `services/video_delivery.py`
- **涉及表**: `auto_chat_sessions`, `video_deliveries`

---

### 1.9 发布到抖音
- **入口**: AutoModeStudio.vue "发布到抖音"按钮（pipeline delivery 面板）
- **前端函数**: → `POST /api/projects/{project_id}/pipeline/{run_id}/delivery/publish-douyin`
- **后端 API**: `POST /api/projects/{project_id}/pipeline/{run_id}/delivery/publish-douyin`
- **调用链**: `routers/pipeline.py:publish_to_douyin` → `services/video_delivery.py:publish_video_to_douyin` → 抖音开放平台 API
- **涉及表**: `video_deliveries`, `social_accounts`

---

### 1.10 连接抖音账号（OAuth）
- **入口**: AutoModeStudio.vue "连接抖音"按钮（`@click="connectDouyin"`）
- **前端函数**: `connectDouyin()` → `startDouyinConnect()` → `window.open(authorization_url)`
- **后端 API**: `POST /api/social-accounts/douyin/connect` + `GET /api/social-accounts/douyin/callback`
- **调用链**: `routers/social_accounts.py` → 抖音 OAuth2 授权流
- **涉及表**: `social_accounts`

---

### 1.11 创建新对话会话
- **入口**: AutoModeStudio.vue 侧边栏"新建会话"按钮（`@click="newSession"`）
- **前端函数**: `newSession()` → `POST /api/projects/{project_id}/auto-sessions`
- **后端 API**: `POST /api/projects/{project_id}/auto-sessions`
- **调用链**: `routers/auto_sessions.py:create_session`
- **涉及表**: `auto_chat_sessions`

---

### 1.12 创建项目
- **入口**: App.vue 项目选择区（新建项目）
- **后端 API**: `POST /api/projects`
- **调用链**: `routers/projects.py:create_project`
- **涉及表**: `projects`

---

### 1.13 用户登录/注册
- **入口**: `components/auth/AuthPage.vue` 登录/注册表单
- **后端 API**: `POST /api/auth/login` / `POST /api/auth/register`
- **调用链**: `routers/auth.py` → `bcrypt` 验签 → 签发 JWT
- **涉及表**: `users`

---

## 2. REST API 入口（完整列表）

### 认证（无 prefix，直接 /api/）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/auth/register` | 注册新用户 | 无 |
| POST | `/api/auth/login` | 登录，返回 JWT | 无 |
| POST | `/api/auth/logout` | 登出 | JWT |
| GET | `/api/auth/me` | 获取当前用户信息 | JWT |
| GET | `/api/admin/users` | 列出所有用户 | JWT(admin) |
| PATCH | `/api/admin/users/{user_id}` | 修改用户信息 | JWT(admin) |

---

### 项目（prefix: `/api/projects`）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/projects` | 创建项目 | JWT |
| GET | `/api/projects` | 列出当前用户项目 | JWT |
| GET | `/api/projects/{id}` | 获取项目详情 | JWT |
| GET | `/api/projects/{id}/usage` | 项目用量汇总 | JWT |
| GET | `/api/projects/{id}/history` | 项目历史记录 | JWT |
| PATCH | `/api/projects/{id}` | 更新项目 | JWT |
| DELETE | `/api/projects/{id}` | 删除项目 | JWT |

---

### 流水线（prefix: `/api`）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/projects/{id}/pipeline` | 启动新流水线 | JWT |
| GET | `/api/projects/{id}/pipelines` | 列出流水线记录 | JWT |
| GET | `/api/projects/{id}/pipeline/{run_id}` | 获取流水线详情 | JWT |
| GET | `/api/projects/{id}/pipeline/{run_id}/agents` | 获取各 Agent 执行记录 | JWT |
| GET | `/api/projects/{id}/pipeline/{run_id}/artifacts` | 获取流水线产物 | JWT |
| GET | `/api/projects/{id}/pipeline/{run_id}/usage` | 获取 Token 用量 | JWT |
| GET | `/api/projects/{id}/pipeline/{run_id}/delivery` | 获取成片投递信息 | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/delivery/save` | 保存成片到仓库 | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/delivery/publish-douyin` | 发布到抖音 | JWT |
| GET | `/api/projects/{id}/pipeline/{run_id}/stream` | SSE 实时进度推送 | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/retry-agent` | 重试指定 Agent | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/cancel` | 取消流水线 | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/confirm-plan` | 确认分镜方案（HITL） | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/confirm-prompt-review` | 确认 Prompt 审核（HITL） | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/retry-shot` | 重试单镜头 | JWT |
| POST | `/api/projects/{id}/pipeline/{run_id}/estimate-cost` | 估算成本 | JWT |
| POST | `/api/projects/{id}/generate-script` | 根据图片生成旁白脚本 | JWT |
| POST | `/api/projects/{id}/preflight-check` | 启动前资源检查 | JWT |

---

### 自动模式会话（prefix: `/api`）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| GET | `/api/projects/{id}/auto-sessions` | 列出会话 | JWT |
| POST | `/api/projects/{id}/auto-sessions` | 创建会话 | JWT |
| GET | `/api/projects/{id}/auto-sessions/{sid}` | 获取会话详情 | JWT |
| PATCH | `/api/projects/{id}/auto-sessions/{sid}` | 更新会话状态 | JWT |
| POST | `/api/projects/{id}/auto-sessions/{sid}/messages` | 添加消息（非流式） | JWT |
| POST | `/api/projects/{id}/auto-sessions/{sid}/chat` | 对话（SSE 流式） | JWT |
| PATCH | `/api/projects/{id}/auto-sessions/{sid}/messages/{mid}` | 修改消息 | JWT |
| GET | `/api/projects/{id}/auto-sessions/{sid}/materials` | 获取会话素材 | JWT |
| POST | `/api/projects/{id}/auto-sessions/{sid}/materials` | 选择素材到会话 | JWT |
| DELETE | `/api/projects/{id}/auto-sessions/{sid}/materials/{mid}` | 移除会话素材 | JWT |
| POST | `/api/projects/{id}/auto-sessions/{sid}/publish-drafts` | 生成发布草稿 | JWT |

---

### 素材库（prefix: `/api`）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/materials/scan` | 扫描本地素材目录 | JWT |
| POST | `/api/materials/upload` | 上传全局素材 | JWT |
| POST | `/api/projects/{id}/materials/upload` | 上传项目素材 | JWT |
| GET | `/api/materials/categories` | 获取素材分类 | JWT |
| GET | `/api/materials` | 分页查询素材 | JWT |
| DELETE | `/api/materials/categories/{cat}` | 删除分类 | JWT |
| DELETE | `/api/materials/{mid}` | 删除素材 | JWT |
| GET | `/api/materials/{mid}/thumbnail` | 获取素材缩略图 | JWT |
| GET | `/api/materials/{mid}/preview` | 获取素材预览 | JWT |
| POST | `/api/projects/{id}/materials/select` | 选择素材到项目 | JWT |
| DELETE | `/api/projects/{id}/materials/select/{mid}` | 取消选择 | JWT |
| GET | `/api/projects/{id}/materials/selected` | 获取已选素材 | JWT |

---

### 视频上传与分析

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/projects/{id}/upload` | 上传参考视频 | JWT |
| GET | `/api/projects/{id}/upload` | 获取上传记录 | JWT |
| GET | `/api/uploads/{uid}/stream` | 流式下载上传视频 | JWT |
| POST | `/api/projects/{id}/analyze` | 触发视频分析（后台任务） | JWT |
| GET | `/api/projects/{id}/analysis` | 获取分析结果 | JWT |

---

### 视频生成（手动模式）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/projects/{id}/generate` | 批量生成镜头视频 | JWT |
| GET | `/api/projects/{id}/generations` | 列出生成记录 | JWT |
| POST | `/api/projects/{id}/generations/{gid}/select` | 标记为已选 | JWT |
| POST | `/api/projects/{id}/generations/{gid}/deselect` | 取消标记 | JWT |
| GET | `/api/projects/{id}/selected-videos` | 获取已选视频 | JWT |
| POST | `/api/projects/{id}/generate-single/{prompt_id}` | 单 Prompt 重新生成 | JWT |
| GET | `/api/generations/{gid}/video` | 获取生成视频文件 | JWT |

---

### Prompt（手动模式）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| GET | `/api/prompts/templates` | 获取 Prompt 模板 | JWT |
| GET | `/api/projects/{id}/chat` | 获取对话历史 | JWT |
| POST | `/api/projects/{id}/chat` | 发送对话消息（SSE） | JWT |
| POST | `/api/projects/{id}/prompts/generate` | 生成分镜 Prompt | JWT |
| GET | `/api/projects/{id}/prompts` | 获取已生成 Prompt | JWT |
| PATCH | `/api/projects/{id}/prompts/{pid}` | 编辑 Prompt | JWT |
| GET | `/api/projects/{id}/prompt-bindings` | 获取 Prompt-素材绑定 | JWT |

---

### 时间轴

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| GET | `/api/projects/{id}/timeline` | 获取时间轴 | JWT |
| PUT | `/api/projects/{id}/timeline` | 更新时间轴 | JWT |
| POST | `/api/projects/{id}/timeline/assets` | 添加时间轴资产 | JWT |
| GET | `/api/timeline/assets/{aid}/file` | 获取资产文件 | JWT |
| DELETE | `/api/timeline/assets/{aid}` | 删除资产 | JWT |

---

### 口播头像（Talking Head）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/projects/{id}/model-images` | 上传人物图 | JWT |
| GET | `/api/projects/{id}/model-images` | 列出人物图 | JWT |
| DELETE | `/api/model-images/{iid}` | 删除人物图 | JWT |
| GET | `/api/model-images/{iid}/file` | 获取人物图文件 | JWT |
| POST | `/api/projects/{id}/talking-head-audio` | 生成 TTS 音频 | JWT |
| GET | `/api/talking-head-audio/{aid}/file` | 获取音频文件 | JWT |
| POST | `/api/projects/{id}/talking-head` | 提交口播生成任务 | JWT |
| GET | `/api/projects/{id}/talking-head` | 列出口播任务 | JWT |
| GET | `/api/talking-head/{tid}` | 获取口播任务详情 | JWT |
| POST | `/api/talking-head/{tid}/composite` | 触发视频合成（后台轮询） | JWT |
| GET | `/api/talking-head/{tid}/composite-preview` | 获取合成预览 | JWT |

---

### 背景模板

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| GET | `/api/background-templates` | 列出模板 | JWT |
| POST | `/api/background-templates/import-presets` | 导入预设模板 | JWT |
| POST | `/api/background-templates/generate-from-keywords` | LLM 生成模板 | JWT |
| POST | `/api/background-templates` | 创建模板 | JWT |
| GET | `/api/background-templates/{tid}` | 获取模板 | JWT |
| PATCH | `/api/background-templates/{tid}` | 更新模板 | JWT |
| DELETE | `/api/background-templates/{tid}` | 删除模板 | JWT |
| GET | `/api/background-templates/{tid}/learning-logs` | 获取偏好学习日志 | JWT |

---

### 社交账号

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| GET | `/api/social-accounts` | 列出已绑定社交账号 | JWT |
| POST | `/api/social-accounts/douyin/connect` | 发起抖音 OAuth | JWT |
| GET | `/api/social-accounts/douyin/callback` | 抖音 OAuth 回调 | 无（OAuth 回调） |
| POST | `/api/social-accounts/{sid}/refresh` | 刷新 Token | JWT |
| PATCH | `/api/social-accounts/{sid}/default` | 设为默认账号 | JWT |
| DELETE | `/api/social-accounts/{sid}` | 删除账号 | JWT |

---

### 仓库（prefix: `/api/repository`）

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| GET | `/api/repository/uploads` | 列出仓库上传 | JWT |
| POST | `/api/repository/uploads/{uid}/import` | 导入上传到项目 | JWT |
| DELETE | `/api/repository/uploads/{uid}` | 删除上传记录 | JWT |
| GET | `/api/repository/deliveries` | 列出仓库成片 | JWT |
| GET | `/api/repository/assets` | 列出仓库资产 | JWT |
| POST | `/api/repository/deliveries/{did}/import` | 导入成片到项目 | JWT |

---

### API Key 管理

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| POST | `/api/api-keys` | 创建 API Key | JWT |
| GET | `/api/api-keys` | 列出当前用户 API Key | JWT |
| POST | `/api/api-keys/{kid}/disable` | 停用 API Key | JWT |
| GET | `/api/admin/api-keys` | 列出所有 Key（管理员） | JWT(admin) |
| POST | `/api/admin/api-keys` | 创建管理员 Key | JWT(admin) |
| POST | `/api/admin/api-keys/{kid}/disable` | 停用任意 Key | JWT(admin) |

---

### 统计与系统

| 方法 | 路径 | 用途 | 认证 |
|------|------|------|------|
| GET | `/api/analytics/overview` | 整体用量统计 | JWT |
| GET | `/api/analytics/agents` | Agent 执行统计 | JWT |
| GET | `/api/analytics/qa` | QA 评分统计 | JWT |
| GET | `/api/analytics/token-usage` | Token 用量明细 | JWT |
| GET | `/api/analytics/pipeline-trends` | 流水线趋势 | JWT |
| GET | `/api/examples` | 获取示例素材列表 | 无 |
| GET | `/api/health` | 健康检查 | 无 |
| POST | `/api/admin/cleanup-artifacts` | 清理临时文件 | JWT(admin) |

---

## 3. 外部 API v1 入口（第三方调用）

prefix: `/v1`，认证方式：`X-API-Key` Header（`api_keys` 表校验）

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/v1/video-jobs` | 提交外部视频生成任务（异步，返回 202） |
| GET | `/v1/video-jobs/{job_id}` | 查询任务状态 |
| GET | `/v1/video-jobs/{job_id}/events` | SSE 实时进度订阅 |
| POST | `/v1/video-jobs/{job_id}/review` | 人工审核任务结果（通过/拒绝） |
| GET | `/v1/video-jobs/{job_id}/result` | 获取最终视频结果文件 |

**调用链**: `routers/public_video_jobs.py` → `PipelineExecutor` → 同主流水线链路 → `ExternalVideoJob` 表

---

## 4. MCP Server 入口

启动方式: `python -m app.mcp.server`（stdio transport，兼容 Claude Desktop）
挂载路径: `/mcp`（通过 `app/mcp/router.py` 注册到 FastAPI）

| Tool 名称 | 描述 | 输入参数 | 调用的内部函数 |
|-----------|------|---------|--------------|
| `list_materials` | 列出素材库图片/视频 | `category: str`, `media_type: str`, `limit: int=20` | 查询 `materials` 表 |
| `get_pipeline_status` | 获取流水线实时状态和 Agent 进度 | `pipeline_run_id: str` | 查询 `pipeline_runs` + `agent_executions` 表 |
| `search_project_history` | 按关键词搜索历史项目（RAG 风格检索） | `keyword: str`, `limit: int=5` | 查询 `projects` + `pipeline_runs` 表（ilike 搜索） |
| `list_agent_tools` | 列出 ToolRegistry 中所有注册工具 | 无 | `build_default_registry().list_tool_definitions()` |

---

## 5. CLI 脚本入口

| 脚本 | 命令 | 用途 |
|------|------|------|
| `scripts/backend-dev.sh` | `bash scripts/backend-dev.sh` | 启动 FastAPI 开发服务器（热重载） |
| `scripts/backend-test.sh` | `bash scripts/backend-test.sh` | 运行 pytest 测试套件 |
| `scripts/backend-lint.sh` | `bash scripts/backend-lint.sh` | 运行 ruff 代码检查 |
| `scripts/backend-install-dev.sh` | `bash scripts/backend-install-dev.sh` | 安装开发依赖 |
| `scripts/check-code-file-lines.sh` | `bash scripts/check-code-file-lines.sh` | 检查源文件是否超过 500 行限制 |
| `python -m app.mcp.server` | 直接运行 | 以 stdio transport 启动 MCP Server |

---

## 6. 后台异步任务入口

VidGen 无 Celery，所有异步任务通过 `asyncio.create_task` 在 FastAPI 进程内调度。

| 触发路由 | 任务函数 | 描述 |
|---------|---------|------|
| `POST /api/projects/{id}/pipeline` | `_run_pipeline()` | 主流水线执行（orchestrator→qa 链路） |
| `POST /api/projects/{id}/pipeline/{rid}/confirm-plan` | `_run_pipeline()` 恢复 | 从 HITL 等待点续跑流水线 |
| `POST /api/projects/{id}/pipeline/{rid}/confirm-prompt-review` | `_run_pipeline()` 恢复 | 从 Prompt 审核等待点续跑 |
| `POST /api/projects/{id}/pipeline/{rid}/retry-agent` | `asyncio.create_task` | 重试指定 Agent 节点 |
| `POST /api/projects/{id}/pipeline/{rid}/retry-shot` | `asyncio.create_task` | 重试单镜头生成 |
| `POST /api/projects/{id}/analyze` | `BackgroundTasks.add_task(_run_analysis)` | 视频分析（FastAPI BackgroundTasks） |
| `POST /api/projects/{id}/generate` | `asyncio.create_task(_poll_generations)` | 轮询图生视频任务状态 |
| `POST /api/projects/{id}/generate-single/{pid}` | `asyncio.create_task(_poll_generations)` | 轮询单镜头生成状态 |
| `POST /api/talking-head/{tid}/composite` | `asyncio.create_task(_poll_composite)` | 轮询口播合成任务 |
| `POST /api/projects/{id}/talking-head` | `asyncio.create_task(_poll_lipsync)` | 轮询唇形同步任务 |
| `POST /v1/video-jobs` | `asyncio.create_task` | 外部视频任务异步执行 |
| `GET /v1/video-jobs/{id}/review → approve` | `asyncio.create_task` | 审核通过后触发后续流程 |
