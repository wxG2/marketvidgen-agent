# 方小集 AI 混剪完整集成计划

## Summary
- 以 `/Users/youfang/.claude/plans/promo-flow-users-weixiang-agent-vidgen-cryptic-nova.md` 为实施骨架，补齐候选池用户选择、Bot 下载/通知、场景级素材入库、方小集 instruction 分层约束。
- 采用内嵌方案：把 VidGen 混剪核心能力迁入 `promo-flow/backend/app/services/remix/vendor/`，不通过 HTTP 调独立 VidGen 服务。
- 用户流程：进入混剪 Tab → 选择模板或输入自然语言 → Agent 澄清缺失项 → 系统召回候选池 → 用户选择素材 → 生成镜头方案 → 用户确认 → 后台渲染 → Bot 通知 → 下载或发布到素材广场审核。

## Backend Architecture
- 新增领域包：
  - `backend/app/domains/remix.py`：只放 Enum、Command、Output dataclass。
  - `backend/app/models/remix_job.py`、`backend/app/models/remix_prompt_template.py`、`backend/app/models/content_scene.py`：只放 ORM。
  - `backend/app/schemas/remix.py`：HTTP I/O，提供 `to_domain()` / `from_domain()`。
  - `backend/app/routers/remix.py`：只做鉴权、schema/domain 转换、调用 service。
  - `backend/app/services/remix/`：业务编排、召回、澄清、规划、渲染、通知、错误映射。
  - `backend/app/workers/remix.py`：长任务入口，只接收 `job_id/content_id`，自行创建 DB session。

- 新增 service 结构：
  - `core.py`：job CRUD、状态流转、权限校验、发布成 Content。
  - `clarifier.py`：自然语言需求解析与缺失项问题生成。
  - `recall.py`：调用现有 `search_contents` 召回视频素材，再结合 `content_scenes` 排序。
  - `planner.py`：把用户选中的素材和 scene 转成 VidGen planner 输入。
  - `assembler.py`：下载源视频、调用 vendor assembler、上传成片到 OSS。
  - `notifier.py`：完成/失败通知、Bot 发文件或下载链接。
  - `errors.py`：`RemixJobNotFoundError`、`RemixForbiddenError`、`RemixInvalidStateError` 等，并提供 `raise_remix_error()`。

## Data Model
- `remix_jobs`
  - `id`
  - `user_id`
  - `prompt`
  - `normalized_requirement JSONB`
  - `target_duration_seconds`
  - `orientation`
  - `bgm_mood`
  - `voiceover_script`
  - `status`
  - `clarification_questions JSONB`
  - `clarification_answers JSONB`
  - `candidate_pool JSONB`
  - `selected_content_ids JSONB`
  - `plan_artifact JSONB`
  - `review_adjustments`
  - `result_file_key`
  - `error_message`
  - `retry_count`
  - `max_retries`
  - `notified_at`
  - `published_content_id`
  - `created_at / updated_at`

- `remix_prompt_templates`
  - `id`
  - `title`
  - `prompt_text`
  - `category_id`
  - `cover_url`
  - `default_config JSONB`
  - `sort_order`
  - `is_active`
  - `created_at / updated_at`

- `content_scenes`
  - `id`
  - `content_id`
  - `scene_idx`
  - `start_seconds`
  - `end_seconds`
  - `duration_seconds`
  - `keyframe_key`
  - `description`
  - `emotion_tag`
  - `visual_quality_score`
  - `audio_mean_volume`
  - `scene_change_score`
  - `embedding_text`
  - `embedding Vector(1024)`
  - `analyzer_version`
  - `created_at`

- `contents` 追加：
  - `scene_status`: `pending | processing | completed | failed | skipped`
  - `scene_error`
  - `scene_processed_at`

## State Machine
- Job 状态：
  - `pending`
  - `awaiting_clarification`
  - `recalling`
  - `awaiting_candidate_selection`
  - `planning`
  - `awaiting_review`
  - `assembling`
  - `uploading_result`
  - `completed`
  - `failed`
  - `cancelled`

- 状态规则：
  - prompt 足够明确：`pending → recalling`
  - prompt 模糊：`pending → awaiting_clarification`
  - 澄清完成：`awaiting_clarification → recalling`
  - 召回完成：`recalling → awaiting_candidate_selection`
  - 用户选择 2 到 8 条素材：`awaiting_candidate_selection → planning`
  - 方案生成：`planning → awaiting_review`
  - 用户确认：`awaiting_review → assembling → uploading_result → completed`
  - 用户要求调整：`awaiting_review → planning`
  - 失败：任意阶段 → `failed`，可按错误类型自动重试一次。
  - 完成或最终失败后，触发飞书 Bot 通知。

## API Contract
- 用户端：
  - `GET /api/v1/remix-prompt-templates`
  - `POST /api/v1/remix-jobs`
  - `GET /api/v1/remix-jobs`
  - `GET /api/v1/remix-jobs/{id}`
  - `POST /api/v1/remix-jobs/{id}/clarify`
  - `GET /api/v1/remix-jobs/{id}/candidates`
  - `POST /api/v1/remix-jobs/{id}/candidate-selection`
  - `POST /api/v1/remix-jobs/{id}/review`
  - `POST /api/v1/remix-jobs/{id}/push-to-feishu`
  - `GET /api/v1/remix-jobs/{id}/download-url`
  - `POST /api/v1/remix-jobs/{id}/publish`

- 管理端：
  - `GET /api/v1/admin/remix-prompt-templates`
  - `POST /api/v1/admin/remix-prompt-templates`
  - `PUT /api/v1/admin/remix-prompt-templates/{id}`
  - `DELETE /api/v1/admin/remix-prompt-templates/{id}`

- 权限：
  - 普通用户只能访问自己的 remix job。
  - admin 可查看全部 job 和管理模板。
  - 所有错误响应遵循方小集统一格式：`error_code`、`message`、`request_id`。

## Material Ingestion
- 视频素材上传并完成现有 AI 分析后，触发 `analyze_content_scenes(content_id)`。
- `scene_analyzer` 流程：
  - 用 OSS 预签名链接下载视频到 `REMIX_WORKSPACE_DIR/scene_tmp/{content_id}`。
  - 调 VidGen vendored `VideoProfiler` 和 `FFmpegKeyframeExtractor` 做场景切分。
  - 上传关键帧到 OSS：`scene-keyframes/{content_id}/{scene_idx}.jpg`。
  - 调 Qwen 多模态生成 scene 描述、情绪、质量分。
  - 可选生成 scene embedding。
  - 批量写入 `content_scenes`。
  - 更新 `Content.scene_status`。
- 历史视频通过独立 backfill 脚本补跑；首期可只强制分析新上传视频。

## Remix Planning & Rendering
- 召回阶段：
  - 先调用现有 `search_contents` 找到视频 Content 候选。
  - 再读取 `content_scenes`，按 prompt、scene 描述、embedding、类目、关键词综合排序。
  - 写入 `RemixJob.candidate_pool`，等待用户选择。
- 规划阶段：
  - 仅使用用户选择的 `selected_content_ids`。
  - 优先使用 `content_scenes` 作为 shot 输入。
  - 若素材未完成 scene 分析，由 `REMIX_REQUIRE_SCENES` 控制：拒绝、降级整段，或实时 profile。
  - Planner 输出 `plan_artifact` 后做系统合法化：时间戳不越界、转场枚举合法、总时长接近目标时长。
- 渲染阶段：
  - 下载源视频到 workspace。
  - 按 `plan_artifact.segments` 裁剪、拼接、转场。
  - 使用 `qwen3-tts-instruct-flash` 生成分段配音。
  - 字幕按每段真实 TTS 音频时长贴到最终时间轴。
  - 上传成片到 OSS：`remix/{user_id}/{job_id}.mp4`。
  - 清理 workspace。

## Bot & Download
- 完成通知：
  - 任务完成后通过飞书 Bot 私聊用户。
  - 小文件复用现有文件发送逻辑。
  - 大文件复用现有预签名下载链接逻辑。
- 失败通知：
  - 最终失败后发送失败卡片，附方小集 job 详情深链。
- 前端下载：
  - “浏览器下载 mp4”：获取预签名 URL 并打开。
  - “飞书 Bot 发我”：调用 `push-to-feishu`，复用通知/发文件逻辑。
- 不新增一套下载阈值，复用现有 `MAX_FORWARD_FILE_BYTES` 和 `LARGE_FILE_DOWNLOAD_URL_EXPIRES_S`。

## Frontend Plan
- 路由：
  - `/remix`：用户混剪任务列表。
  - `/remix/new`：新建混剪。
  - `/remix/:id`：任务详情、候选选择、方案确认、成片下载。
- 新增：
  - `frontend/src/hooks/useRemixJobs.ts`
  - `frontend/src/hooks/useRemixJob.ts`
  - `frontend/src/services/remix.ts`
  - `frontend/src/components/remix/TemplateGallery.tsx`
  - `frontend/src/components/remix/ClarificationChat.tsx`
  - `frontend/src/components/remix/CandidatePool.tsx`
  - `frontend/src/components/remix/RemixPlanTimeline.tsx`
  - `frontend/src/components/remix/RemixTaskList.tsx`
- 页面不能直接 import `api`，所有请求走 hook。
- `TabBar` 和 `Sidebar` 增加“混剪”入口。
- `RemixDetail` 每 5 秒轮询，进入 `completed/failed/cancelled` 后停止。
- 所有前端类型镜像后端 schema，用户可见文案全部中文。

## Implementation Phases
- PR1：后端基础骨架
  - domain、schema、model、migration、router、service façade。
  - `remix_jobs` CRUD 和 owner 权限测试。
- PR2：模板与澄清
  - `remix_prompt_templates`、admin CRUD、用户只读接口。
  - `clarifier.py`、`awaiting_clarification` 状态、前端模板和澄清气泡。
- PR3：场景级素材入库
  - `content_scenes`、`Content.scene_status`、scene analyzer worker。
  - 新上传视频自动分析，历史视频 backfill 脚本。
- PR4：候选池召回与用户选择
  - `recall.py`、candidate pool 接口、候选池前端。
  - 用户选择素材后才进入 planner。
- PR5：VidGen vendor 迁入与 planner 接通
  - 迁入 planner、profiler、keyframe extractor、prompt。
  - 生成并合法化 `plan_artifact`。
- PR6：assembler、TTS、字幕、OSS 输出
  - 迁入 assembler、clip extractor、tts。
  - approve 后生成 mp4 并上传 OSS。
- PR7：Bot 通知、下载、发布
  - 完成/失败通知。
  - Bot 发文件、浏览器下载。
  - 发布为 pending Content，并触发现有 AI 分析。
- PR8：前端完善与部署
  - 三页面、TabBar、Sidebar、状态轮询、错误态。
  - Docker 安装 ffmpeg、workspace volume、env example、文档。

## Test Plan
- 后端：
  - schema 校验：prompt 长度、候选素材数量、状态流转非法请求。
  - 权限：用户 A/B 互相看不到 job；admin 可查看全部。
  - 澄清：模糊 prompt 进入 `awaiting_clarification`，回答后进入 `recalling`。
  - 候选池：只返回 approved video 且可用素材。
  - scene analyzer：mock FFmpeg/Qwen/OSS，验证写入 `content_scenes`。
  - planner：mock LLM，验证计划合法化。
  - renderer：mock FFmpeg/TTS/OSS，验证状态到 `completed`。
  - publish：生成 pending Content，并调用公共 content AI 调度服务，不调用 router 私有函数。
  - Bot：mock Feishu，验证完成/失败通知和大文件链接逻辑。
- 前端：
  - `npm run build` 通过。
  - 模板选择填充 prompt。
  - 澄清气泡可选择并提交。
  - 候选池选择不足 2 条时不能继续。
  - 方案确认、调整、下载、发布按钮状态正确。
- 手工验收：
  - 新上传视频完成 scene 分析。
  - 输入“帮我剪个 30 秒厨房用品快闪，节奏明快”。
  - 完成候选选择、方案确认、渲染、Bot 通知、下载、发布审核全链路。

## Assumptions
- 首期只支持视频素材混剪。
- 成片默认私有，只有用户主动发布后才进入素材审核。
- 候选池由系统召回，但素材选择由用户确认。
- `qwen3-tts-instruct-flash` 的音色使用官方 voice id，语气通过 instruction/tone 控制。
- 不迁移 VidGen 的完整 PipelineRun、Project、LangGraph 体系，只迁移混剪所需核心代码。
- 首期继续使用 BackgroundTasks/asyncio task；任务量上来后再迁移 Celery/ARQ。
