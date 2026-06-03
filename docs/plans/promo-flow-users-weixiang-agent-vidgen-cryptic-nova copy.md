# 方小集集成 vidgen AI 混剪功能 — 集成方案

## Context

**为什么做**：方小集（promo-flow）目前只解决"素材上传 → AI 分析 → 审核 → 分发"。新增需求：让用户用自然语言描述意图（例如"做一个 30s 的智能家居安装快闪"），系统自动从素材库召回相关视频，由 LLM 编排剪辑计划，用户确认后由 ffmpeg 拼成成片，可下载也可一键发布回素材广场。

**为什么不走 HTTP**：你明确不想跨进程调 vidgen，而是把 vidgen 的 `remix_planner` / `remix_assembler` 这两个核心 Agent 代码搬进 promo-flow 后端 monorepo，与现有的 Qwen 客户端、pgvector 搜索、OSS 存储共生。这样：
- 素材源直接复用 `search_contents`，不用再上传一次
- 鉴权 / 审计 / 用户隔离全部走方小集既有体系
- 不引入新的运维节点（不需要部署 vidgen backend）

**预期产出**：方小集新增「混剪」tab，用户在该 tab 内完成「输入需求 → 看召回 → 看混剪计划 → 确认 → 看结果 → 下载或发布」全流程。

---

## 整体架构

```
┌──────── 前端（React） ────────┐
│  TabBar: 广场 | [混剪] | 我的 │
│  Pages: Remix / RemixCreate / RemixDetail │
└───────────────┬─────────────────────────┘
                │ /api/v1/remix-jobs
┌───────────────▼─────────────────────────┐
│ FastAPI router  routers/remix.py         │
└───────────────┬─────────────────────────┘
                │
   ┌────────────▼────────────────────────────────────────┐
   │ services/remix/  （新模块）                          │
   │  core.py    业务编排 + RemixJob CRUD               │
   │  recall.py  prompt → search_contents → 素材池      │
   │  planner.py 调 vendor.remix_planner                │
   │  assembler.py 调 vendor.remix_assembler             │
   │  runner.py  asyncio 任务 + Semaphore（仿 ai_runner）│
   │  vendor/    从 vidgen 迁过来的剪辑/TTS/ffmpeg 代码  │
   └────────────┬────────────────────────────────────────┘
                │
   ┌────────────▼──────────────┬─────────────────────────┐
   │ services/search           │ services/infrastructure │
   │  search_contents (现有)   │  ai.py (Qwen 现有)      │
   │                           │  storage.py (OSS 现有)  │
   └───────────────────────────┴─────────────────────────┘
                │
        ┌───────▼────────┐
        │  PostgreSQL    │  新表 remix_jobs
        │  + pgvector    │  Content.id 外键引用
        └────────────────┘
```

**状态机**：`pending → recalling → planning → awaiting_review →（user approve）→ assembling → uploading_result → completed`；失败任意阶段 → `failed`；用户在 `awaiting_review` 可拒绝并重新规划（回到 `planning`）。

---

## 后端改动

### 1) 新增 ORM 模型 `backend/app/models/remix_job.py`

字段（必要最小集）：
- `id: int PK`
- `user_id: int FK → users.id`（隔离用）
- `prompt: str`（用户输入的自然语言需求）
- `target_duration_seconds: int`
- `bgm_mood: str | None`（参 vidgen `PublicRemixConfig`）
- `voiceover_script: str | None`
- `status: RemixJobStatus`（枚举见下）
- `material_content_ids: JSON[int]`（召回后锁定的素材 Content ID 列表，便于复盘 / 重规划）
- `plan_artifact: JSON`（vidgen planner 输出 — segments / shots / transitions / 关键帧时间）
- `review_adjustments: str | None`（用户拒绝时填的修改建议，用于二次规划 prompt）
- `result_file_key: str | None`（OSS key，例如 `remix/{user_id}/{job_id}.mp4`）
- `error_message: str | None`
- `published_content_id: int | None FK → contents.id`（若用户点了"发布"）
- `created_at / updated_at`

参考既有 ORM 风格：`backend/app/models/content.py:22-105`。

### 2) Domain 与 Schema

- `backend/app/domains/remix.py`（新）
  - `class RemixJobStatus(str, Enum)`：`pending / recalling / planning / awaiting_review / assembling / uploading_result / completed / failed`
  - Commands：`CreateRemixJobCommand`、`ReviewRemixJobCommand(approved: bool, adjustments: str | None)`、`PublishRemixJobCommand(title: str, category_id: int | None, tags: list[str])`
  - Outputs：`RemixJobOutput`、`RemixJobListOutput`、`RemixPlanOutput`（含 `segments: list[{content_id, content_thumbnail_url, start, end, transition, subtitle}]`）

- `backend/app/schemas/remix.py`（新）：HTTP I/O，提供 `to_domain()` / `from_domain()`，与 `schemas/content.py` 保持一致。

### 3) Router `backend/app/routers/remix.py`（新）

| Method | Path | 入参 | 出参 | 权限 |
|---|---|---|---|---|
| POST | `/api/v1/remix-jobs` | `RemixJobCreateIn` | `RemixJobOut`（202） | 登录用户 |
| GET | `/api/v1/remix-jobs` | `?status, page, page_size` | `RemixJobListOut` | 仅 owner（管理员可看全部） |
| GET | `/api/v1/remix-jobs/{id}` | path | `RemixJobOut` + `RemixPlanOutput` | owner / admin |
| POST | `/api/v1/remix-jobs/{id}/review` | `RemixJobReviewIn` | `RemixJobOut` | owner |
| POST | `/api/v1/remix-jobs/{id}/publish` | `RemixJobPublishIn` | `ContentOut`（创建的新 Content） | owner |
| GET | `/api/v1/remix-jobs/{id}/download-url` | path | `{url, expires_at}` | owner |

挂载到 `backend/app/routers/router.py:1-15`（追加 `from .remix import router as remix_router` + `api_router.include_router(remix_router)`）。

### 4) Services 编排 `backend/app/services/remix/`（新）

**`recall.py`**：把 prompt 转成素材池
```python
# 伪代码示意，不是最终代码
from app.services.search import search_contents
from app.domains.content import SearchContentCommand

async def recall_materials(db, prompt: str, limit: int = 30):
    cmd = SearchContentCommand(
        query=prompt, limit=limit,
        content_type="video", enable_rerank=True,
        allow_query_limit_override=True,
    )
    out = await search_contents(db, command=cmd)
    # 过滤已有 ai_status=completed 且有 file_key 的视频
    return [r.content for r in out.results if r.content.file_key]
```

**`planner.py`**：调用 vendored planner
- 把 `[ContentOutput]` 转成 vidgen 期望的"参考视频"结构：每个 item 需要 `local_path | signed_url`、`tags`、`ai_summary`、`duration`（用 `ffprobe` 现取）
- 调 `vendor.remix_planner.run(prompt, materials, target_duration, adjustments?)`，得到 `RemixPlan`
- 持久化到 `RemixJob.plan_artifact` → 状态转 `awaiting_review`

**`assembler.py`**：用户 approve 后
- 下载所有用到的素材到本地 `REMIX_WORKSPACE_DIR/{job_id}/` （用 `storage.generate_presigned_download_url` + httpx）
- 调 `vendor.remix_assembler.run(plan, workspace_dir)` → 输出 mp4
- 用 `storage.upload_file(...)` 上传到 OSS（key = `remix/{user_id}/{job_id}.mp4`），写回 `result_file_key`
- 清理本地工作区
- 状态 → `completed`

**`runner.py`**：异步驱动（仿 `backend/app/services/infrastructure/ai_runner.py:32-53`）
- `asyncio.Semaphore(settings.REMIX_CONCURRENCY)` 限并发（默认 2，ffmpeg 重计算）
- `schedule_remix_task(job_id)` 把 `recall → planner` 串成一个 asyncio Task；用户 approve 后再 `schedule_assemble_task(job_id)` 串 `assembler`
- 失败统一 set `status=failed, error_message=str(exc)`

**`core.py`**：CRUD + 业务方法
- `create_remix_job(db, command, user)`：插表 → `schedule_remix_task` → 返回 RemixJobOutput
- `get / list_for_user / get_with_plan`
- `review_remix_job(db, command, job_id, user)`：若 approve → `schedule_assemble_task`；若 reject → 写 `review_adjustments` → `schedule_remix_task`（重规划）
- `publish_remix_job(db, command, job_id, user)`：从 `result_file_key` 创建一条 Content（status=pending）→ 触发既有 `_schedule_ai_analysis`（`backend/app/routers/content.py:107`）→ 走 reviewer 审核 → 发布广场
- `build_download_url(job_id)`：调 `generate_presigned_download_url(result_file_key, expires=3600)`（`services/infrastructure/storage.py:86`）

### 5) Vendored vidgen 代码 `backend/app/services/remix/vendor/`

从 `/Users/weixiang/agent/vidgen/backend/app/` 抽取：
- `agents/stages/remix_planner.py` → `vendor/planner.py`
- `agents/stages/remix_assembler.py` → `vendor/assembler.py`
- `agents/stages/audio_subtitle.py`（TTS + 字幕烧录部分）→ `vendor/audio_subtitle.py`
- `services/tts_service.py` → `vendor/tts.py`
- `services/llm/qwen_client.py` 中混剪用到的方法 → `vendor/qwen.py`（若 promo-flow 现有 `services/infrastructure/ai.py` 已能复用就不复制）
- `services/clip_extractor.py`（如有）→ `vendor/clip_extractor.py`
- 相关 prompt 文本 → `vendor/prompts.py`

**改造要点**：
- 砍掉对 vidgen 的 `Project / PipelineRun / ExternalVideoJob` 数据模型依赖 —— 把所有需要的状态从 `RemixJob` 行直接传入函数参数（context dataclass）。
- 不要 import vidgen 的 `app.core.config`；改成读 `backend/app/core/config.py` 中新增的字段（`FFMPEG_BIN, REMIX_WORKSPACE_DIR, QWEN_TTS_API_KEY` 等）。
- 文件命名加 `# vendored from vidgen@<commit>` 注释保留 provenance，便于后续同步更新。

### 6) 数据库迁移

`backend/alembic/versions/xxxx_add_remix_jobs.py`：创建 `remix_jobs` 表与索引（`(user_id, created_at desc)`、`(status)`）。

### 7) 配置 `backend/app/core/config.py` 追加

- `REMIX_CONCURRENCY: int = 2`
- `REMIX_WORKSPACE_DIR: Path = Path("/var/promo-flow/remix-workspace")`
- `REMIX_OSS_PREFIX: str = "remix/"`
- `FFMPEG_BIN: str = "ffmpeg"`
- `QWEN_TTS_API_KEY: str = ""`（若与已有 `DASHSCOPE_API_KEY` 同 Key 则复用）
- `REMIX_DEFAULT_DURATION_SECONDS: int = 30`

---

## 前端改动

### 1) TabBar 重构 `frontend/src/components/layout/TabBar.tsx`

当前是"左 tab + 中心上传按钮 + 右 tab"硬编码 JSX（L5-8 + L31-37）。需要改成 3 tab + 中心按钮的布局，例如左→中按钮→右两个 tab，或保持 4 区域 grid。最少改 5-8 行 `tabs` 数组 + JSX 结构。

新增 tab：`{ path: '/remix', label: '混剪', icon: Wand2 }`（`lucide-react` 已在用）。

### 2) 路由 `frontend/src/App.tsx`

Layout 子路由内追加：
```tsx
<Route path="/remix" element={<PrivateRoute><Remix /></PrivateRoute>} />
<Route path="/remix/new" element={<PrivateRoute><RemixCreate /></PrivateRoute>} />
<Route path="/remix/:id" element={<PrivateRoute><RemixDetail /></PrivateRoute>} />
```

### 3) 新页面

- `frontend/src/pages/Remix.tsx` — 用户的混剪作业列表，按状态分 tab（全部 / 等待确认 / 已完成 / 失败），仿 `pages/MyUploads.tsx:16-64` 的 status tab 结构；右上角按钮跳 `/remix/new`。
- `frontend/src/pages/RemixCreate.tsx` — 表单：prompt（textarea）+ 目标时长 slider + BGM 风格 select + 旁白脚本（可选 textarea）+ 提交按钮。提交后跳 `/remix/:id`。
- `frontend/src/pages/RemixDetail.tsx` — 核心页：
  - 顶部展示当前 status + 进度（用 `LoadingDots` + 文字阶段名）
  - `awaiting_review` 时：渲染 `plan_artifact.segments` 时间线（每段卡片含 Content 缩略图、起止秒、转场、字幕文本），按钮「确认开拼」「调整后重新规划」（弹框输 adjustments）
  - `completed` 时：`<video>` 播放器 + 「下载 mp4」按钮 + 「发布到素材广场」按钮（弹框填标题 + 分类 + 标签 → POST `/publish`）
  - 失败：错误展示 + 重试入口
- 轮询：`useEffect` 内 `setInterval(refresh, 5000)`，参 `pages/Audit.tsx:339-349`。

### 4) Hooks 与 Services

- `frontend/src/hooks/useRemixJobs.ts` — 列表 hook，封装分页 + 状态过滤
- `frontend/src/hooks/useRemixJob.ts` — 单个详情 hook，含轮询逻辑
- `frontend/src/services/remix.ts` — 6 个方法对应 6 个 endpoint，使用现有 axios 实例（`services/api.ts:1-33`）

### 5) 类型镜像 `frontend/src/types/index.ts`

追加 `RemixJob`、`RemixJobStatus`、`RemixSegment`、`RemixPlan`，与后端 `domains/remix.py` 完全对齐（参考前端类型规约：project.instructions.md L78 "mirror backend schemas"）。

---

## 部署 / 配置

### Dockerfile（`backend/Dockerfile`）
- `RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*`
- 增加 workspace 卷：`docker-compose.yml` backend 服务追加 `volumes: ['./data/remix-workspace:/var/promo-flow/remix-workspace']`

### `.env` 模板新增
```
REMIX_CONCURRENCY=2
REMIX_WORKSPACE_DIR=/var/promo-flow/remix-workspace
REMIX_OSS_PREFIX=remix/
FFMPEG_BIN=ffmpeg
QWEN_TTS_API_KEY=
REMIX_DEFAULT_DURATION_SECONDS=30
```

### OSS bucket 策略
- 新前缀 `remix/`，与已有 `materials/`、`thumbnails/` 同一 bucket 即可
- 下载链接走预签名 1h 有效（`storage.generate_presigned_download_url`），不暴露公网直链

---

## 关键复用清单

| 用途 | 复用文件 | 关键函数 |
|---|---|---|
| 素材召回 | `backend/app/services/search/core.py:105` | `search_contents(db, command)` |
| 召回结果转 URL | `backend/app/services/infrastructure/storage.py:86` | `generate_presigned_download_url(file_key, expires)` |
| Qwen LLM 调用 | `backend/app/services/infrastructure/ai.py:36-39` | `_dashscope_compat` AsyncOpenAI 实例 |
| 异步任务模型 | `backend/app/services/infrastructure/ai_runner.py:32-53` | `schedule_ai_task` (Semaphore 模式) |
| Embedding | `backend/app/services/infrastructure/ai.py:278` | `generate_embedding` |
| OSS 上传 | `backend/app/services/infrastructure/storage.py` | `upload_file` / `put_object` |
| 创建 Content | `backend/app/routers/content.py:107,134-135` | `_schedule_ai_analysis` 钩子 |
| 鉴权 | `backend/app/core/deps.py:13-57` | `get_current_user` |
| 前端 axios | `frontend/src/services/api.ts:1-33` | 单例 + Bearer 注入 |
| 前端轮询 | `frontend/src/pages/Audit.tsx:339-349` | `setInterval(refresh, 10000)` 模式 |
| 前端进度条 | `frontend/src/components/content/UploadProgressDialog.tsx` | 复用同款 props.progress 渲染 |

---

## 实施分阶段（建议 PR 切分）

1. **PR1 — 后端骨架**：models / domains / schemas / alembic / 空 router + 空 service，跑通 CRUD（mock 状态推进）。
2. **PR2 — Vendor 迁入**：从 vidgen 抽 planner / assembler / tts / prompts 到 `vendor/`，写适配层让 vidgen 代码能拿到 promo-flow 配置；单元测试用 fixture 视频跑通 planner（不接真 LLM 时用 mock plan）。
3. **PR3 — 召回 + planner 接通**：实现 `recall.py` + `planner.py` + `runner.py`，POST 创建后能跑到 `awaiting_review`。
4. **PR4 — Assembler 接通**：实现 `assembler.py`，approve 后能产出 mp4 并上传 OSS。
5. **PR5 — 前端**：TabBar + 3 页面 + hooks + 类型镜像。
6. **PR6 — 发布回素材广场**：`publish` 端点 + 前端按钮。
7. **PR7 — Docker / CI**：ffmpeg 进镜像、env 模板、volume 挂载、文档。

---

## 验证

**单元 / 集成测试**（`backend/tests/test_remix_*.py`）：
- `test_remix_recall.py`：mock `search_contents`，验证 `recall_materials` 返回 ≥ 1 个 video Content
- `test_remix_planner.py`：fixture videos + mock Qwen response，验证 `plan_artifact` 结构
- `test_remix_review_flow.py`：approve / reject 路径状态机
- `test_remix_publish.py`：publish 后是否真的写入 `contents` 表 + 触发 AI 分析

**端到端手工验证**（参 `docs/guides/local-dev-setup.md`）：
1. `docker compose up -d && just bootstrap`
2. 浏览器登录，确认底部 TabBar 出现「混剪」
3. 点「混剪 → 新建」，prompt 输 `"做一个 30 秒的厨房用品快闪，节奏明快"`
4. 5 秒内进入 `awaiting_review`，UI 展示 ≥ 3 个素材组成的时间线
5. 点「确认开拼」，2-5 分钟内 status 变 `completed`
6. 点视频可播，点「下载」拿到 mp4，点「发布」后回素材广场看到一条 pending Content
7. 切换 reviewer 账号在 `/audit` 能看到并通过

**ffmpeg 安装验证**：`docker compose exec backend ffmpeg -version` 应返回版本。

**回归**：现有 `/api/v1/contents`、`/api/v1/search`、`/audit` 全部不受影响（新模块完全隔离在 `services/remix/`）。

---

## 已知风险与开放问题

- **vidgen 的 planner 依赖完整的 `Project / PipelineRun` 数据**，剥离时如果出现循环依赖，可能要在 vendor 内加薄壳 dataclass 模拟。如果剥离工作量超过 3 天，可考虑改用「在 promo-flow 内启一个轻量 vidgen runtime 子进程通过 stdin/stdout 通信」的折中方案。
- **ffmpeg 是 CPU 密集**，并发 2 时 30s 视频约 1-3 分钟。如果上线后用户量大，要在 OSS 后加一个独立 worker 节点或 GPU 编码。
- **OSS 跨域**：若浏览器要直接播 OSS 视频，bucket 需要配置 CORS。预签名 URL 默认不解决 CORS，按需在 bucket 控制台加规则。
- **召回质量**：纯 search 召回可能挑出主题接近但风格混乱的素材。后续可在 planner 之前加一步"LLM 二次筛选 + 排序"，但首版保持简单。

---

## v2 增量需求

调研结论：promo-flow 当前**没有任何"推荐查询/提示词模板"基础设施**，**没有点对点"通知上传者"的实际调用点**（但底层 `send_markdown_to_user / send_file_to_user` 已就绪），**没有视频内部场景切分**（整条视频一份摘要）。三块都需要新建能力，但底层基础都齐了。

### A. 预置模板 + Agent 澄清式确定性交互

#### A.1 后端 — 模板表

新增模型 `backend/app/models/remix_prompt_template.py`：
- `id PK / title(64) / prompt_text(text) / category_id FK→categories.id NULL / cover_url(256) NULL / sort_order int / is_active bool / created_at / updated_at`
- 索引：`(is_active, sort_order)`

迁移 `backend/alembic/versions/<hash>_add_remix_prompt_templates.py`，命名风格参 `383e805dd6b2_add_thumbnail_key_to_contents.py`。

Admin 端 endpoint（复用 `routers/admin.py:89` 类目维护风格）：
- `GET/POST/PUT/DELETE /api/v1/admin/remix-prompt-templates`

用户端只读：
- `GET /api/v1/remix-prompt-templates?category_id={id}` —— 用户进 `RemixCreate` 页时拉一次

#### A.2 后端 — Agent 澄清状态

`RemixJob.status` 枚举追加：`awaiting_clarification`（位于 `pending` 与 `recalling` 之间）

`RemixJob` 表新增字段：
- `clarification_questions: JSON | None` —— Agent 输出的待澄清项数组，结构：
  ```json
  [
    {
      "key": "target_duration_seconds",
      "question": "目标时长？",
      "options": [{"label": "15s", "value": 15}, {"label": "30s", "value": 30}, {"label": "60s", "value": 60}]
    },
    {
      "key": "orientation",
      "question": "横版还是竖版？",
      "options": [{"label": "横版 16:9", "value": "landscape"}, {"label": "竖版 9:16", "value": "portrait"}]
    },
    {"key": "voiceover", "question": "需要配音吗？", "options": [{"label": "是", "value": true}, {"label": "否", "value": false}]}
  ]
  ```
- `clarification_answers: JSON | None` —— 用户答完后写入

新增 service 步骤 `services/remix/clarifier.py`：
- `analyze_prompt(prompt: str, defaults: dict) -> ClarifierResult` —— 调 Qwen（复用 `services/infrastructure/ai.py`）让 LLM 自己产出"已确定参数 + 待澄清问题"。Prompt 模板放 `backend/app/prompts/templates/remix_clarify.j2`，结构与 `content_analysis.j2` 一致。
- 若 LLM 判定"所有关键参数都明确" → 直接进 `recalling`
- 否则写 `clarification_questions` → 状态 `awaiting_clarification` → 等用户回答

新增 endpoint：
- `POST /api/v1/remix-jobs/{id}/clarify` body：`{answers: {key: value, ...}}` —— 写入 `clarification_answers`，回 `analyze_prompt` 用合并后参数再跑一次（理论一轮够，避免循环；若 LLM 仍不满意则用 defaults 兜底）→ 进 `recalling`

#### A.3 前端 — 模板卡片 + 澄清气泡

`frontend/src/pages/RemixCreate.tsx` 页头追加 `<TemplateGallery />`：
- 横向滚动卡片，标题 + 一句话 prompt_text 摘要，可按 category 筛
- 点击 → 把 `prompt_text` 灌进 textarea（允许用户继续编辑）
- 数据源：`GET /api/v1/remix-prompt-templates`

`frontend/src/pages/RemixDetail.tsx` 状态分支增加 `awaiting_clarification`：
- 在页面顶部以"聊天气泡"形式逐条渲染 `clarification_questions`
- 每条问题下方是 `<button>` 组（不是下拉），点选 → 视觉上变成"已回答"气泡
- 全部答完后底部出现「继续」按钮 → POST `/clarify`
- 视觉风格参考 `components/ui/Toast.tsx` 的卡片样式 + Tailwind `rounded-2xl` + 头像/对话样式

`frontend/src/types/index.ts` 镜像 `RemixPromptTemplate`、`ClarificationQuestion`、`ClarificationOption`。

#### A.4 模板种子数据

新增 `backend/scripts/seed_remix_templates.py`，按现有类目体系给出 12-20 个内置模板（例：「30 秒厨房用品快闪」「60 秒装修案例展示」「15 秒小红书风格新品种草」「90 秒企业品牌片」）。在 `docs/guides/local-dev-setup.md` 里加一条"seed remix templates"。

---

### B. 多用户任务隔离 + 飞书 Bot 通知 + Bot 发文件

#### B.1 多用户存储

原方案的 `RemixJob.user_id FK→users.id` 已经支持多用户隔离 —— **明确以下三点**：
- `GET /api/v1/remix-jobs` 在 `services/remix/core.py:list_for_user` 中**强制按 `user_id=current_user.id` 过滤**（仅 admin 可加 `?all=true`）
- `GET / POST / PUT` 单个作业的 endpoint 全部走 `require_owner_or_admin(job.user_id)` 保护（在 `core/deps.py` 仿 `require_role` 写一个）
- 索引：`(user_id, created_at desc)`、`(user_id, status)` —— 前端按状态 tab 高频查询

#### B.2 完成 → 飞书 Bot 通知（点对点）

复用现成函数（**无需新增 SDK 代码**）：
- `services/infrastructure/feishu.py:90 send_markdown_to_user(open_id, title, markdown)`
- `services/infrastructure/feishu.py:184 send_file_to_user(open_id, file_name, file_obj)`

在 `services/remix/runner.py` 的 `_finalize_job` 中：
1. 拿 `job.user.feishu_open_id`（已有字段 `models/user.py:19`）
2. 渲染卡片：标题「你的 AI 混剪已完成 ✨」+ 摘要（用户 prompt + 时长 + 用到的素材数）+ 落地按钮"在方小集查看"（深链 `/remix/{id}`）
3. **小文件（< MAX_FORWARD_FILE_BYTES，默认 10MB）** → 流式从 OSS 拉 mp4 → `SpooledTemporaryFile` → `send_file_to_user`（仿 `services/content/core.py:811-868` 整段实现）
4. **大文件** → 调 `storage.generate_presigned_download_url(result_file_key, expires=LARGE_FILE_DOWNLOAD_URL_EXPIRES_S)` → `send_markdown_to_user` 带链接

**失败也通知**：在 `_handle_failure` 里 `send_markdown_to_user`「混剪失败，错误：xxx」，附「重试」深链。

新增 prompt 模板 `backend/app/prompts/templates/remix_completed_notify.j2` + `remix_failed_notify.j2`，与现有 `download_content_intro.j2` 同目录、同 j2 风格。

#### B.3 前端"下载"按钮行为

`RemixDetail.tsx` 完成态的「下载」按钮提供两种动作（参 `MyUploads.tsx` 已有的两按钮 row 样式）：
- 「浏览器下载 mp4」 → 调 `GET /api/v1/remix-jobs/{id}/download-url` 拿 1h 预签名 URL，`window.open`
- 「飞书 Bot 发我」 → 调新 endpoint `POST /api/v1/remix-jobs/{id}/push-to-feishu`，后端复用 B.2 的发文件逻辑（去除需要重新等任务完成的部分），适合用户不想下载到本地、想直接在飞书里转发的场景

#### B.4 失败重试机制

`RemixJob` 加 `retry_count: int default 0`、`max_retries: int default 1`。`runner.py` 失败时若 `retry_count < max_retries` 自动重排一次（仅对网络/ffmpeg transient 错误），否则进 `failed` + 发 Bot 通知。

---

### C. 素材入库新增"场景级元数据"

#### C.1 新表 `content_scenes`

`backend/app/models/content_scene.py`：
- `id PK`
- `content_id FK→contents.id (cascade delete)`
- `scene_idx int`（从 0 起递增）
- `start_seconds float`
- `end_seconds float`
- `duration_seconds float`
- `keyframe_key str(256)`（OSS key，非本地路径 —— 这是与 vidgen vendor 的关键差异）
- `description str(512) | None`（LLM 生成的"这段在演什么"）
- `emotion_tag str(32) | None`（参 vidgen `ShotProfile.emotion_tag`）
- `visual_quality_score float | None`
- `audio_mean_volume float | None`
- `embedding Vector(1024) | None`（可选，让"找类似镜头"成为可能；维度与 Content.embedding 一致以便统一向量库）
- `scene_change_score float | None`
- `created_at`
- 索引：`Index('ix_scenes_content', content_id, scene_idx unique)`，`Index('ix_scenes_emotion', emotion_tag)`，pgvector 索引仅当 embedding 落库后才建

迁移文件命名 `<hash>_add_content_scenes.py`。

#### C.2 复用 vidgen 的 VideoProfiler（don't rebuild）

把 `/Users/weixiang/agent/vidgen/backend/app/services/video_editing/video_profiler.py` + `keyframe_extractor.py`（含 `FFmpegKeyframeExtractor`）一并放进 `backend/app/services/remix/vendor/`。

适配层 `backend/app/services/content/scene_analyzer.py`（**注意：这个文件落在 `services/content/`，不是 `services/remix/`** —— 它服务于素材入库流程，而非混剪流程；只是借用 vendor 里 vidgen 迁来的代码）：
1. 用 `storage.generate_presigned_download_url` 拿到视频签名 URL
2. 用 ffmpeg 把视频拉到 `REMIX_WORKSPACE_DIR/scene_tmp/{content_id}/source.mp4`（同样的 workspace 卷，避免新增挂载）
3. 调 `VideoProfiler.profile_video(local_path, content_id)` → `VideoProfile.shots: list[ShotProfile]`
4. 对每个 shot：把本地 `keyframe_path` `storage.upload_file` 到 OSS（key 规则 `scene-keyframes/{content_id}/{scene_idx}.jpg`）→ 拿到 `keyframe_key`
5. 调 Qwen 多模态对每段（keyframe + 时间段）生成 `description / emotion_tag`，prompt 模板 `prompts/templates/scene_describe.j2`
6. 可选：对每段 `description` 调 `generate_embedding` 写入 `content_scenes.embedding`（开关 `settings.SCENE_EMBEDDING_ENABLED`）
7. 批量 INSERT `content_scenes` 行
8. 清理本地临时文件

#### C.3 触发时机

仿 `routers/content.py:286 _schedule_ai_analysis` 的模式，在它**完成之后**（即 `Content.ai_status=completed` 且 `Content.content_type=video`）追加调度 `_schedule_scene_analysis(content_id)`。

理由：场景分析依赖 OSS 上的视频文件，又比较慢，与 AI 摘要解耦更稳。

`Content` 表新增 `scene_status: SceneStatus(pending|processing|completed|failed|skipped)` + `scene_error: str | None` + `scene_processed_at: datetime | None`，与现有 `ai_status` 三件套对称。

`SceneStatus.skipped` 用于：图片类型、视频但 duration < `SCENE_MIN_DURATION_SECONDS`（如 < 5s 不切）。

新 worker semaphore `settings.SCENE_ANALYSIS_CONCURRENCY=1`（ffmpeg 重计算，独占即可）。

#### C.4 让混剪 planner 真正用到场景数据

`services/remix/planner.py` 的 `prepare_materials` 步骤：
- 对每个召回到的 Content，**优先**用 `content_scenes` 现成数据（直接 SQL JOIN）；
- 若该 Content 还没有 scenes（`scene_status != completed`），降级行为：要么 fallback 到 vidgen vendor 的实时 profile（慢但兜底），要么直接整段使用（用 Content.duration 作为唯一 shot）；策略由 `settings.REMIX_REQUIRE_SCENES=false` 控制
- 把 `content_scenes` 行结构转换成 vendor planner 期望的 `ShotProfile`（字段几乎一对一，仅 `keyframe_path` 字段映射为本地缓存路径 —— 由 assembler 阶段从 OSS 现下到本地 workspace 时即时填充）

#### C.5 也让 search 用到

`backend/app/services/search/retriever.py` 长期可扩展一个"镜头级召回"通道（在 `recall_vector` 基础上加 `recall_scene_vector`），让用户"找一段下雨夜晚的镜头"这类极细颗粒度查询成为可能。**v2 不强制实现，但表结构 + embedding 已经预留好**。

---

### v2 对原方案各章节的具体修改点

| 原方案章节 | 改动 |
|---|---|
| 状态机 | `pending → awaiting_clarification(若需澄清) → recalling → planning → awaiting_review → assembling → uploading_result → completed`；失败任意阶段 → `failed`，自动重试 ≤ `max_retries`；完成/失败 → 触发 Bot 通知 |
| `RemixJob` 模型 | 追加：`clarification_questions / clarification_answers / retry_count / max_retries` |
| 新模型 | 加 `RemixPromptTemplate`、`ContentScene`；`Content` 加 `scene_status / scene_error / scene_processed_at` |
| Router | 加：`GET /remix-prompt-templates`、`admin CRUD /admin/remix-prompt-templates`、`POST /remix-jobs/{id}/clarify`、`POST /remix-jobs/{id}/push-to-feishu` |
| Service 新模块 | `services/remix/clarifier.py`、`services/content/scene_analyzer.py`、`services/remix/notifier.py`（封装 Bot 推送） |
| Vendor 迁入 | 在原"`remix_planner / remix_assembler / tts`"基础上加 `video_profiler.py / keyframe_extractor.py` |
| Prompt 模板 | 新增 `remix_clarify.j2 / remix_completed_notify.j2 / remix_failed_notify.j2 / scene_describe.j2` |
| 前端页面 | `RemixCreate.tsx` 加 TemplateGallery；`RemixDetail.tsx` 加 `awaiting_clarification` 分支（聊天气泡）；下载按钮拆"浏览器下载 / 飞书发我"两枚 |
| 类型镜像 | 加 `RemixPromptTemplate / ClarificationQuestion / ClarificationOption / ContentScene` |
| Alembic | 多 3 个迁移：`add_remix_prompt_templates / add_content_scenes / add_scene_status_to_contents` |
| 配置 | 新增 `SCENE_ANALYSIS_CONCURRENCY=1 / SCENE_MIN_DURATION_SECONDS=5 / SCENE_EMBEDDING_ENABLED=false / REMIX_REQUIRE_SCENES=false / MAX_FORWARD_FILE_BYTES（若未设）/ LARGE_FILE_DOWNLOAD_URL_EXPIRES_S（若未设）` |
| 分阶段 PR | 在原 PR2 与 PR3 之间插入：**PR2.5 — content_scenes 表 + scene_analyzer**（独立可上线，先给素材库补场景数据）；在原 PR3 之后插入：**PR3.5 — clarifier + templates**；在原 PR4 之后插入：**PR4.5 — Bot 通知** |
| 验证 | 新增端到端测试：(1) 创建作业输入模糊 prompt → 应进 `awaiting_clarification`，前端能渲染选项气泡，答完后能进 `recalling`；(2) 完成后真实飞书账号能收到 Bot 卡片 + 文件；(3) 一条新上传视频，5 分钟内 `content_scenes` 表里能 SELECT 出 ≥ 2 条 shot 记录，且 OSS 上能下到对应 keyframe；(4) 多用户 A/B 同时各跑一个作业，互相看不到对方的列表，A 只收到自己的 Bot 通知 |

### v2 新增风险

- **澄清交互可能让 LLM 跑偏**：用户输入越短，LLM 越爱问"你是想做什么风格"这种开放问题。需要在 prompt 里强约束"最多 3 个问题、每个必须给 2-4 个选项、不允许自由文本回答"。
- **场景分析是 CPU 密集 + 串行的额外管线**：素材库若有 10k 视频要补场景，建议加管理后台"批量补跑" + 限速。短期内可仅对**新上传的视频**强制跑，存量视频按需触发。
- **Bot 发文件受飞书速率限制**：批量完成几十个作业同时发文件会触发限流。`notifier.py` 加 `asyncio.Semaphore(3)` + 429 退避（飞书 SDK 自带退避就用 SDK 的）。
- **关键帧 OSS 体积**：每条视频假设切 8 段、每张 50KB JPEG → 400KB / 视频，10k 视频约 4GB，OSS 成本可忽略。
- **场景 embedding 列扩张**：若开启 `SCENE_EMBEDDING_ENABLED`，10k 视频 × 8 段 × 1024 维 ≈ 320MB pgvector，Postgres 单实例仍可承受；HNSW 索引按需建。
