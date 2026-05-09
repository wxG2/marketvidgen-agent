# API 路由说明

本目录下每个文件对应一组 FastAPI 路由，负责接收 HTTP 请求并调用 Service 层完成业务逻辑。

> **注意**：部分路由通过工厂函数（如 `get_pipeline_router(executor)`）注入依赖后返回；这些依赖由 `app.bootstrap` 创建，再在 `main.py` 中统一注册。

---

## 认证

### `auth.py`

用户注册、登录、登出及账号管理。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 注册新用户 |
| POST | `/auth/login` | 密码登录，返回 session token |
| POST | `/auth/logout` | 登出，销毁当前 session |
| GET | `/auth/me` | 获取当前登录用户信息 |
| GET | `/admin/users` | （管理员）获取所有用户列表 |
| PATCH | `/admin/users/{user_id}` | （管理员）修改用户状态或角色 |

### `api_keys.py`

当前登录用户的外部 API Key 管理。明文 key 只在创建时返回，后端保存哈希和短前缀。scope 支持 `video_jobs:create`、`video_jobs:read`、`video_jobs:review` 和 `*`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/api-keys` | 创建外部调用 API Key |
| GET | `/api/api-keys` | 获取当前用户 API Key 列表 |
| POST | `/api/api-keys/{api_key_id}/disable` | 禁用指定 API Key |
| GET | `/api/admin/api-keys` | （管理员）获取所有用户 API Key 列表 |
| POST | `/api/admin/api-keys` | （管理员）为指定用户创建 API Key |
| POST | `/api/admin/api-keys/{api_key_id}/disable` | （管理员）禁用任意 API Key |

---

## 系统

### `system.py`

健康检查与内部维护接口。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/admin/cleanup-artifacts` | （管理员）手动触发过期中间产物清理 |

---

## 项目

### `projects.py`

项目的 CRUD 操作及统计信息。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects` | 创建新项目 |
| GET | `/api/projects` | 获取当前用户的所有项目列表 |
| GET | `/api/projects/{project_id}` | 获取项目详情 |
| PATCH | `/api/projects/{project_id}` | 更新项目名称或当前步骤 |
| DELETE | `/api/projects/{project_id}` | 删除项目（级联删除所有关联数据） |
| GET | `/api/projects/{project_id}/usage` | 获取项目的 LLM token 用量汇总 |
| GET | `/api/projects/{project_id}/history` | 获取项目的 pipeline 运行历史 |

---

## 素材库

### `materials.py`

素材文件的管理，包括上传、扫描、分类查询和项目素材选择。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/materials/scan` | 扫描本地素材目录，将文件索引到数据库 |
| POST | `/api/materials/upload` | 上传素材文件到全局素材库 |
| POST | `/api/projects/{project_id}/materials/upload` | 上传素材并直接关联到指定项目 |
| GET | `/api/materials/categories` | 获取所有素材分类列表 |
| GET | `/api/materials` | 分页查询素材，支持按分类和关键词筛选 |
| DELETE | `/api/materials/categories/{category}` | 删除某个分类下的全部素材 |
| DELETE | `/api/materials/{material_id}` | 删除指定素材 |
| GET | `/api/materials/{material_id}/thumbnail` | 获取素材缩略图文件 |
| GET | `/api/materials/{material_id}/preview` | 获取素材预览文件（视频/图片） |
| POST | `/api/projects/{project_id}/materials/select` | 将素材加入项目选择列表 |
| DELETE | `/api/projects/{project_id}/materials/select/{material_id}` | 从项目选择列表中移除素材 |
| GET | `/api/projects/{project_id}/materials/selected` | 获取项目已选素材列表（含排序） |

---

## 参考视频上传

### `upload.py`

用户上传参考视频（用于风格分析），支持流式播放。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{project_id}/upload` | 上传参考视频文件 |
| GET | `/api/projects/{project_id}/upload` | 获取项目当前的参考视频信息 |
| GET | `/api/uploads/{upload_id}/stream` | 流式播放参考视频（支持 Range 请求） |

---

## 视频分析

### `analysis.py`

对已上传的参考视频进行 AI 内容分析（异步后台任务）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{project_id}/analyze` | 触发对参考视频的 AI 分析任务 |
| GET | `/api/projects/{project_id}/analysis` | 查询分析结果（摘要、场景标签、推荐分类） |

---

## Prompt 管理

### `prompts.py`

项目内的人机对话历史和最终 prompt 的管理，含 AI 优化能力。需注入 `LLMService`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/prompts/templates` | 获取内置 prompt 模板列表 |
| GET | `/api/projects/{project_id}/chat` | 获取项目对话历史 |
| POST | `/api/projects/{project_id}/chat` | 发送消息，触发 AI 回复（流式 SSE） |
| POST | `/api/projects/{project_id}/prompts/generate` | 根据对话历史和选定素材 AI 生成 prompt 列表 |
| GET | `/api/projects/{project_id}/prompts` | 获取项目已生成的 prompt 列表 |
| PATCH | `/api/projects/{project_id}/prompts/{prompt_id}` | 修改某条 prompt 内容 |
| GET | `/api/projects/{project_id}/prompt-bindings` | 获取 prompt 与素材的绑定关系（用于前端展示） |

---

## 视频生成

### `generation.py`

调用 AI 视频生成接口（Kling/Seedance 等），管理生成结果。需注入 `VideoGenerator`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{project_id}/generate` | 批量触发所有已选 prompt 的视频生成任务 |
| GET | `/api/projects/{project_id}/generations` | 获取项目所有生成任务的状态列表 |
| POST | `/api/projects/{project_id}/generations/{gen_id}/select` | 标记某个生成结果为选中（用于后续剪辑） |
| POST | `/api/projects/{project_id}/generations/{gen_id}/deselect` | 取消选中 |
| GET | `/api/projects/{project_id}/selected-videos` | 获取项目已选中的视频列表 |
| POST | `/api/projects/{project_id}/generate-single/{prompt_id}` | 针对单条 prompt 触发生成 |
| GET | `/api/generations/{gen_id}/video` | 下载/播放生成的视频文件 |

---

## 数字人口播

### `talking_head.py`

数字人视频生成的全流程管理，分为人物照片管理、合成图预览、对口型视频生成三个阶段。需注入 `ImageCompositor` 和 `LipSyncGenerator`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{project_id}/model-images` | 上传数字人人物照片 |
| GET | `/api/projects/{project_id}/model-images` | 获取项目已上传的人物照片列表 |
| DELETE | `/api/model-images/{image_id}` | 删除人物照片 |
| GET | `/api/model-images/{image_id}/file` | 获取人物照片文件 |
| POST | `/api/projects/{project_id}/talking-head-audio` | 上传口播音频片段（TTS 或手动录制） |
| GET | `/api/talking-head-audio/{audio_id}/file` | 获取音频文件 |
| POST | `/api/projects/{project_id}/talking-head` | 创建数字人任务（指定人物照片和背景素材） |
| GET | `/api/projects/{project_id}/talking-head` | 获取项目所有数字人任务列表 |
| GET | `/api/talking-head/{task_id}` | 获取单个数字人任务详情 |
| POST | `/api/talking-head/{task_id}/composite` | 触发人物+背景合成图生成 |
| GET | `/api/talking-head/{task_id}/composite-preview` | 获取合成预览图文件 |
| PATCH | `/api/talking-head/{task_id}/prompt` | 更新动作提示词和音频配置 |
| POST | `/api/talking-head/{task_id}/generate` | 触发对口型视频生成 |
| GET | `/api/talking-head/{task_id}/video` | 获取生成的口播视频文件 |

---

## 视频生产流水线

### `pipeline.py`

多 Agent 协作视频生产流水线的启动、监控和控制。需注入 `PipelineExecutor`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{project_id}/pipeline` | 启动一次 pipeline 运行（传入完整配置） |
| GET | `/api/projects/{project_id}/pipelines` | 获取项目所有 pipeline 运行记录 |
| GET | `/api/projects/{project_id}/pipeline/{run_id}` | 获取某次 pipeline 运行详情 |
| GET | `/api/projects/{project_id}/pipeline/{run_id}/agents` | 获取各 Agent 的执行明细 |
| GET | `/api/projects/{project_id}/pipeline/{run_id}/artifacts` | 获取本次运行已入仓的提示词、音频、字幕和分镜视频等中间产物 |
| GET | `/api/projects/{project_id}/pipeline/{run_id}/usage` | 获取本次运行的 token 用量 |
| GET | `/api/projects/{project_id}/pipeline/{run_id}/delivery` | 获取视频投递状态 |
| GET | `/api/projects/{project_id}/pipeline/{run_id}/final-video` | 鉴权读取 pipeline 最终成片文件，用于前端直接预览 |
| POST | `/api/projects/{project_id}/pipeline/{run_id}/delivery/save` | 将生成视频保存到本地仓库 |
| POST | `/api/projects/{project_id}/pipeline/{run_id}/delivery/publish-douyin` | 发布视频到抖音 |
| GET | `/api/projects/{project_id}/pipeline/{run_id}/stream` | SSE 流式获取 pipeline 进度事件 |
| POST | `/api/projects/{project_id}/pipeline/{run_id}/retry-agent` | 从指定 Agent 重试 |
| POST | `/api/projects/{project_id}/pipeline/{run_id}/cancel` | 取消正在运行的 pipeline |
| POST | `/api/projects/{project_id}/pipeline/{run_id}/confirm-plan` | 用户确认 AI 生成的策划方案，继续执行 |
| POST | `/api/projects/{project_id}/pipeline/{run_id}/message` | 向暂停等待中的 pipeline 发送消息 |
| POST | `/api/projects/{project_id}/generate-script` | 单独调用 AI 生成视频脚本（不启动完整 pipeline） |
| POST | `/api/projects/{project_id}/preflight-check` | 启动前预检：验证素材、配置是否满足生成条件 |

### `public_video_jobs.py`

外部视频生成 API v1。使用 `Authorization: Bearer vg_...` 鉴权，不依赖 Cookie。创建任务时会自动创建私有项目、上传素材、创建 `PipelineRun` 并复用现有 pipeline 执行器；普通生成强制进入 `shot_plan` 审核，复刻生成进入 `replication_plan` 审核。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/video-jobs` | 以 multipart 形式提交 `spec` JSON 和多张 `images`，可选 `reference_video` / `watermark`，创建外部视频任务 |
| GET | `/v1/video-jobs/{job_id}` | 查询外部任务状态、当前 agent、审核数据和下载入口 |
| GET | `/v1/video-jobs/{job_id}/events` | SSE 流式获取外部任务状态和 agent 进度 |
| POST | `/v1/video-jobs/{job_id}/review` | 审核分镜或复刻方案，支持普通生成按 shot 编辑后继续 |
| GET | `/v1/video-jobs/{job_id}/result` | 任务完成后下载 mp4 成片 |

---

## AutoChat 自动对话

### `auto_sessions.py`

AI 辅助创作的对话会话管理，集成了会话参数配置、消息收发、素材关联和发布草稿生成。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/{project_id}/auto-sessions` | 获取项目下所有会话列表（摘要信息） |
| POST | `/api/projects/{project_id}/auto-sessions` | 新建一个 AutoChat 会话 |
| GET | `/api/projects/{project_id}/auto-sessions/{session_id}` | 获取会话详情（含消息历史、已选素材、当前 pipeline 状态） |
| PATCH | `/api/projects/{project_id}/auto-sessions/{session_id}` | 更新会话配置（标题、平台、转场、BGM 等参数） |
| POST | `/api/projects/{project_id}/auto-sessions/{session_id}/messages` | 直接添加一条消息（无 AI 回复） |
| POST | `/api/projects/{project_id}/auto-sessions/{session_id}/chat` | 发送用户消息，触发 AI 流式回复（SSE） |
| PATCH | `/api/projects/{project_id}/auto-sessions/{session_id}/messages/{message_id}` | 编辑某条消息内容 |
| GET | `/api/projects/{project_id}/auto-sessions/{session_id}/materials` | 获取会话已选素材列表 |
| POST | `/api/projects/{project_id}/auto-sessions/{session_id}/materials` | 向会话添加素材 |
| DELETE | `/api/projects/{project_id}/auto-sessions/{session_id}/materials/{material_id}` | 从会话移除素材 |
| POST | `/api/projects/{project_id}/auto-sessions/{session_id}/publish-drafts` | 从会话最新 AI 消息中提取发布草稿 |

---

## 背景模板

### `background_templates.py`

品牌/角色/风格模板的管理，支持 AI 辅助填写。需注入 `LLMService`。

| 方法 | 前缀 | 说明 |
|---|---|---|
| CRUD | `/api/background-templates` | 背景模板的创建、查询、更新、删除 |
| POST | `/api/background-templates/generate` | 根据关键词 AI 生成模板内容 |
| POST | `/api/background-templates/{id}/refine` | 根据已有模板内容 AI 优化 |

---

## 社交账号

### `social_accounts.py`

抖音账号的 OAuth 授权绑定与管理。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/social-accounts` | 获取当前用户绑定的社交账号列表 |
| POST | `/api/social-accounts/douyin/connect` | 发起抖音 OAuth 授权，返回授权跳转 URL |
| GET | `/api/social-accounts/douyin/callback` | 抖音 OAuth 回调，交换 token 并保存账号 |
| POST | `/api/social-accounts/{id}/refresh` | 刷新指定账号的 access token |
| PATCH | `/api/social-accounts/{id}/default` | 设为默认发布账号 |
| DELETE | `/api/social-accounts/{id}` | 解绑并删除账号 |

---

## 时间线编辑

### `timeline.py`

项目时间线（轨道/片段）的读取与保存，支持上传本地媒体文件。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/{project_id}/timeline` | 获取项目完整时间线（所有轨道片段） |
| PUT | `/api/projects/{project_id}/timeline` | 保存时间线（全量覆盖写入片段布局） |
| POST | `/api/projects/{project_id}/timeline/assets` | 上传本地文件（视频/音频/字幕）到时间线素材库 |
| GET | `/api/timeline/assets/{asset_id}/file` | 下载时间线素材文件 |
| DELETE | `/api/timeline/assets/{asset_id}` | 删除时间线素材 |

---

## 仓库

### `repository.py`

查询和管理 pipeline 产出的视频上传记录与发布投递记录，支持将已发布视频重新导入为参考视频。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/uploads` | 获取所有视频上传记录（可按项目筛选） |
| POST | `/uploads/{upload_id}/import` | 将某条上传记录重新导入为参考视频 |
| DELETE | `/uploads/{upload_id}` | 删除上传记录及文件 |
| GET | `/deliveries` | 获取所有视频投递记录 |
| GET | `/assets` | 获取当前账号下 pipeline Agent 自动保存的中间产物 |
| POST | `/deliveries/{delivery_id}/import` | 将已投递视频导入为参考视频 |

---

## 示例素材

### `examples.py`

返回内置示例素材列表（用于演示和引导）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/examples` | 获取系统内置示例素材列表 |
