# VidGen 系统设计文档

> 面向爆款视频复刻的多 Agent 协作内容生产系统
> Version 1.0 | 高伟翔 | 2026.05

---

## 1. 项目概述

### 1.1 项目定位

VidGen 是一个面向电商和短视频营销场景的多 Agent 视频生产系统。系统通过七个 AI Agent 接力协作（爆款解析、脚本策略、分镜生成、视频生成、智能合成、质量检测、流量回看），把过去需要一个完整营销团队完成的视频生产工作压缩为一条 URL 输入和一个生成按钮。

### 1.2 设计目标

- **端到端自动化**：从输入参考视频到产出成品 MP4，全链路无人工介入。
- **状态可观测**：所有 Agent 共享 GraphState，任务执行的每一步可见、可回放、可调试。
- **失败可恢复**：每个 Agent 节点写 Checkpoint，任务中断后可从最近 Checkpoint 续跑。
- **数据闭环**：流量回看 Agent 把投放后的真实表现反哺到 Qdrant 向量库，下一轮生成自动检索高表现历史案例。
- **生产部署友好**：单卡 L20（90 GiB RAM）即可承载完整推理链路。

### 1.3 核心约束

| 约束维度 | 取值 |
|---------|------|
| 单视频时长 | ≤ 60 秒 |
| 单任务端到端耗时 | ≤ 10 分钟 |
| 单任务 GPU RAM 峰值 | ≤ 90 GiB |
| QA 自动重试上限 | 3 次/节点 |
| 失败任务可恢复率 | 100% |
| 单 Worker 并发任务数 | ≥ 8 |
| 支持语种 | 中文、英文（V1） |

### 1.4 系统不做的事

- 不训练或微调底层视频生成模型，外部能力通过 API或工具 调用。
- 不做实时直播、AR/VR 视频生成。
- 不直接对接广告投放平台 API，流量数据通过用户授权或上传埋点回流。
- 不做视频版权检测和合规审核，依赖外部审核服务。

---

## 2. 系统架构设计

### 2.1 架构概览

VidGen 采用四层架构。从上到下依次为接入层、Agent 编排层、能力执行层、基础设施层。每一层只依赖下一层提供的接口，禁止跨层调用。

```
┌──────────────────────────────────────────────────────────┐
│  接入层                                                    │
│  Web UI (Vue) │ REST API (FastAPI) │ MCP Server          │
├──────────────────────────────────────────────────────────┤
│  Agent 编排层  LangGraph StateGraph                       │
│  ├ ReferenceAgent ├ ScriptAgent ├ StoryboardAgent        │
│  ├ GenerationAgent ├ AssemblyAgent ├ QAAgent             │
│  └ AnalyticsAgent                                         │
├──────────────────────────────────────────────────────────┤
│  能力执行层  ToolRegistry                                  │
│  ├ LLM Client ├ ComfyUI Client ├ FFmpeg Wrapper          │
│  ├ ASR/TTS Wrapper ├ VideoDownloader ├ TrafficAdapter    │
├──────────────────────────────────────────────────────────┤
│  基础设施层                                                │
│  ├ PostgreSQL (业务数据) ├ Qdrant (向量检索)               │
│  ├ Redis (Checkpoint + 缓存) ├ MinIO (素材对象存储)        │
│  └ structlog + OpenTelemetry (可观测)                     │
└──────────────────────────────────────────────────────────┘
```

### 2.2 系统组件

#### 2.2.1 接入层组件

| 组件 | 职责 | 技术 |
|------|------|------|
| Web Frontend | 用户工作台、任务进度展示、视频预览编辑 | Vue 3 + TypeScript + Pinia |
| API Gateway | REST API 网关、认证鉴权、限流 | FastAPI + JWT |
| MCP Server | 把 VidGen 能力暴露给外部 Agent 系统 | FastMCP |
| WebSocket Hub | 任务进度实时推送 | FastAPI WebSocket |

#### 2.2.2 Agent 编排层组件

| 组件 | 职责 | 上游 → 下游 |
|------|------|----------|
| ReferenceAgent | 解析参考视频的结构化特征 | API → ScriptAgent |
| ScriptAgent | 生成结构化脚本 | ReferenceAgent → StoryboardAgent |
| StoryboardAgent | 脚本切分为镜头清单并选择生成策略 | ScriptAgent → GenerationAgent |
| GenerationAgent | 批量生成镜头视频片段 | StoryboardAgent → AssemblyAgent |
| AssemblyAgent | 配音、字幕、BGM、合成最终视频 | GenerationAgent → QAAgent |
| QAAgent | 多维度质量检测，决定通过或回退 | AssemblyAgent → END / 重跑 |
| AnalyticsAgent | 流量数据回看，沉淀经验到 Qdrant | 异步触发 → ScriptAgent (next round) |

#### 2.2.3 能力执行层组件

ToolRegistry 是 Agent 决策层和外部能力的统一抽象。所有外部工具实现统一接口并注册。

| 工具名 | 用途 | 底层实现 |
|--------|------|---------|
| video_download | 下载平台视频 | yt-dlp |
| shot_detect | 镜头切分 | PySceneDetect |
| llm_complete | LLM 推理 | LangChain ChatModel |
| multimodal_understand | 图像/视频理解 | Qwen-VL / Claude |
| embedding | 文本向量化 | BGE-M3 |
| vector_search | 向量检索 | Qdrant Client |
| comfy_workflow_run | 执行 ComfyUI 工作流 | HTTP API |
| tts_synthesize | 文本转语音 | 火山引擎 TTS |
| asr_align | 字幕对齐 | whisper-timestamped |
| ffmpeg_compose | 视频合成 | FFmpeg |
| traffic_fetch | 拉取流量数据 | TrafficAdapter |
| clip_qa | 片段质量打分 | 本地 CV 模型 |

#### 2.2.4 基础设施层组件

| 组件 | 职责 | 部署形态 |
|------|------|---------|
| PostgreSQL | 用户、团队、任务、权限、内部用量数据 | 主从复制 / 托管高可用 |
| Qdrant | 向量检索（脚本特征 + 流量标签） | 单实例（V1）/ 集群（V2） |
| Redis | Checkpoint 存储 + 任务队列 + 缓存 | 哨兵模式 |
| MinIO | 视频素材、成片、临时文件对象存储 | 分布式 |
| ComfyUI Server | GPU 推理节点 | 独立部署，HTTP API |
| structlog + OTel | 结构化日志 + 分布式追踪 | Sidecar |

### 2.3 关键设计原则

- **状态优先**：所有 Agent 间通过共享 GraphState 协作，禁止 Agent 之间直接调用。
- **工具与决策解耦**：Agent 只决策不调用底层 API，所有外部能力通过 ToolRegistry。
- **失败可恢复**：每个节点入口和出口写 Checkpoint，失败可续跑。
- **QA 内嵌**：每个生成节点后必须有 QA，失败按原因路由到对应上游。
- **可观测**：trace_id 端到端贯穿，每个 Agent 必须上报耗时、Token、QA 通过率。

---

## 3. 功能模块设计

### 3.1 GraphState 全局状态

LangGraph StateGraph 的全局状态对象。所有 Agent 通过读写 State 字段协作。

```python
from typing import TypedDict, List, Dict, Optional, Literal
from datetime import datetime

class GraphState(TypedDict):
    # 任务元信息
    task_id: str
    trace_id: str
    user_id: str
    created_at: datetime
    user_intent: str           # 用户原始需求
    source_url: Optional[str]  # 爆款参考视频 URL

    # 各 Agent 输出
    reference_analysis: Optional[ReferenceAnalysis]
    script: Optional[Script]
    storyboard: Optional[List[Shot]]
    raw_clips: List[ClipAsset]
    final_video: Optional[VideoAsset]
    analytics_report: Optional[AnalyticsReport]

    # 控制流
    current_node: str
    qa_attempts: Dict[str, int]
    pending_human_approval: bool
    error: Optional[ErrorInfo]

    # 可观测
    node_timings: Dict[str, float]
    token_usage: Dict[str, int]
```

### 3.2 ReferenceAgent — 爆款解析

**职责**：从爆款视频 URL 出发，提取结构化特征。

**核心步骤**：

1. 调用 `video_download` 工具，下载视频到本地（命中缓存则跳过）。
2. 调用 `ffmpeg_compose` 提取时长、分辨率、关键帧。
3. 调用 `shot_detect` 切分镜头边界。
4. 调用 `multimodal_understand` 对每个镜头采样 1 帧做视觉理解，归纳风格关键词。
5. 调用 BGM 分析子模块，识别背景音乐风格和节奏。
6. 写入 `reference_analysis` 字段，触发下游。

**接口契约**：

```python
async def reference_agent(state: GraphState) -> GraphState:
    """
    前置条件: state['source_url'] 不为空
    后置条件: state['reference_analysis'] 已填充
    异常: SourceUnavailableError, ParseError
    """
```

**关键数据结构**：

```python
class ReferenceAnalysis(TypedDict):
    duration_sec: float
    hook_position_sec: float
    shot_count: int
    shot_durations: List[float]
    pacing_tier: Literal['slow', 'medium', 'fast']
    bgm_genre: str
    style_keywords: List[str]
    text_overlay_positions: List[TextOverlayMeta]
    detected_objects: List[str]
```

### 3.3 ScriptAgent — 脚本策略

**职责**：基于用户意图和参考视频特征，生成结构化脚本。

**核心步骤**：

1. 从 Qdrant 检索 top-3 相似历史项目作为 few-shot context。
2. 拼装 prompt：用户意图 + 参考视频特征 + few-shot + 输出 schema。
3. 调用 `llm_complete`（JSON mode）生成脚本草稿。
4. 本地校验：时长合规、section 顺序合理、字数限制。
5. 校验失败则节点内自动重试，最多 2 次。
6. 触发 HITL 让用户确认或修改。

**Prompt 模板要求**：

- 必须显式要求 JSON 输出，schema 与 Script 类型对齐。
- 时长字段必须以参考视频时长为锚点，浮动范围 ±15%。
- 钩子位置必须落在 0—3 秒。
- CTA 段必须出现在最后 5 秒。

**关键数据结构**：

```python
class Script(TypedDict):
    title: str
    duration_sec: float
    sections: List[ScriptSection]
    voiceover: str
    on_screen_text: List[str]
    target_emotion: Literal['excited', 'calm', 'curious', 'urgent']

class ScriptSection(TypedDict):
    section_type: Literal['hook', 'product', 'demo', 'cta', 'transition']
    start_sec: float
    end_sec: float
    description: str
```

### 3.4 StoryboardAgent — 分镜生成

**职责**：把脚本切成可执行的镜头清单，每个镜头选择最优生成策略。

**生成策略决策树**：

```python
def choose_strategy(shot: ShotIntent) -> str:
    if shot.requires_real_product_shot:
        return 'reuse_clip'      # 复用素材库
    elif shot.requires_face_swap:
        return 'vace_swap'       # 走小影同款换头工作流
    elif shot.has_reference_image:
        return 'i2v'             # 图生视频 (Wan2.2)
    else:
        return 't2v'             # 文生视频
```

**关键数据结构**：

```python
class Shot(TypedDict):
    shot_id: str
    start_sec: float
    end_sec: float
    visual_prompt: str
    camera_movement: Literal['static', 'pan', 'zoom_in', 'zoom_out', 'tracking']
    reference_image_url: Optional[str]
    style_lora: Optional[str]
    seed: int
    generation_strategy: Literal['t2v', 'i2v', 'vace_swap', 'reuse_clip']
```

### 3.5 GenerationAgent — 视频生成

**职责**：按分镜清单批量生成视频片段，调度 ComfyUI 工作流。

**调度策略**：

- 同策略的镜头合并为一批提交，减少模型加载次数。
- 单 L20 节点串行执行，多 GPU 节点并行。
- 每个镜头生成完成后立即跑 ClipQA，不通过触发节点内重试。
- 镜头级重试上限 2 次，超过则标记 `qa_passed=false` 并继续。

**显存约束**：

- 模型按需加载 + 动态卸载（复用小影项目实证策略）。
- FP16 推理 + 视频帧流式处理。
- GenerationAgent 在每次提交工作流前调用 `ModelManager.evict_unused`。

### 3.6 AssemblyAgent — 智能合成

**职责**：把镜头片段合成为完整视频，加字幕、配音、BGM。

**流水线步骤**：

1. 按时间轴排序所有 ClipAsset。
2. 调用 `tts_synthesize` 生成配音。
3. 调用 `asr_align` 生成 SRT 字幕。
4. 调用 `ffmpeg_compose` 完成 concat + 混音 + 烧字幕 + 加 BGM（响度归一）。
5. 输出 MP4 到 MinIO，写入 `final_video` 字段。

### 3.7 QAAgent — 质量检测

**职责**：成片多维度质量检测，决定通过还是回退到上游节点。

**检测维度**：

- 时长偏差（实际 vs 脚本目标）≤ 5%。
- 音视频同步偏差 ≤ 80 ms。
- 视觉一致性：抽样 5 帧做 CLIP 相似度，与目标风格关键词对齐 ≥ 0.7。
- 文本可读性：字幕区域无遮挡、字号合规。

**回退路由**：

```python
def qa_router(state: GraphState) -> str:
    reason = state['error']['qa_failure_reason']
    rollback_map = {
        'visual_inconsistency': 'generation',
        'duration_mismatch': 'storyboard',
        'audio_sync': 'assembly',
    }
    target = rollback_map.get(reason, 'human_review')
    if state['qa_attempts'].get(target, 0) >= 3:
        return 'human_review'
    return f'rollback_{target}'
```

### 3.8 AnalyticsAgent — 流量回看

**职责**：对已发布视频做流量回看，沉淀经验反哺下一轮生成。

**触发方式**：异步定时任务，独立于主 StateGraph 执行。

**数据来源**：

- 用户授权的平台 API（抖音开放平台、TikTok Insights）。
- 用户上传的埋点回流文件。

**沉淀逻辑**：

- 每条已发视频在 24/72/168 小时三个时间点抓取核心指标。
- 根据完播率、互动率打质量标签（爆款/合格/差）。
- (脚本特征 + 流量标签) 作为新 example 写入 Qdrant，参与未来检索。

### 3.9 LangGraph 流程拓扑

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(GraphState)

graph.add_node('reference', reference_agent)
graph.add_node('script', script_agent)
graph.add_node('storyboard', storyboard_agent)
graph.add_node('generation', generation_agent)
graph.add_node('assembly', assembly_agent)
graph.add_node('qa', qa_agent)
graph.add_node('human_review', human_review_node)

graph.set_entry_point('reference')
graph.add_edge('reference', 'script')
graph.add_edge('script', 'storyboard')
graph.add_edge('storyboard', 'generation')
graph.add_edge('generation', 'assembly')
graph.add_edge('assembly', 'qa')

graph.add_conditional_edges('qa', qa_router, {
    'pass': END,
    'rollback_storyboard': 'storyboard',
    'rollback_generation': 'generation',
    'rollback_assembly': 'assembly',
    'human_review': 'human_review',
})
graph.add_edge('human_review', 'qa')

compiled = graph.compile(checkpointer=RedisCheckpointer(...))
```

---

## 4. 数据库设计

### 4.1 数据库选型

| 数据类型 | 存储方案 | 选型理由 |
|---------|---------|---------|
| 业务关系数据（用户、团队、任务、权限、内部用量） | PostgreSQL | 强一致、关系完整、JSONB 支持、生产运维成熟 |
| 任务执行 Checkpoint | Redis 7 | 高频读写、原生支持过期 |
| 向量数据（脚本特征 + 历史案例） | Qdrant | 支持 payload 过滤、HNSW 索引 |
| 视频/图像/音频文件 | MinIO | S3 协议兼容、可水平扩展 |
| 任务消息队列 | Redis Streams | 与 Checkpoint 共用 Redis 实例 |
| 日志与监控数据 | OpenTelemetry → Loki + Prometheus | 标准化、成本可控 |

> 本地开发或 Demo 环境可以继续使用 SQLite；企业内部生产环境推荐使用 PostgreSQL。当前版本仅面向企业内部使用，不包含对外商业化交易链路，只记录内部资源配额、用量和审计数据。

### 4.2 PostgreSQL 核心表结构

#### users（用户表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 用户 ID |
| email | VARCHAR(120) UK | 邮箱（可空） |
| phone | VARCHAR(20) UK | 手机号（可空） |
| password_hash | VARCHAR(120) | 密码哈希（bcrypt） |
| display_name | VARCHAR(60) | 显示名 |
| avatar_url | VARCHAR(255) | 头像 URL |
| role | ENUM | admin / operator / reviewer / viewer |
| monthly_generation_quota | INT | 月度生成配额（内部资源控制） |
| monthly_generation_used | INT | 本月已使用生成次数 |
| team_id | BIGINT FK | 所属内部团队 / 部门 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

索引：`(email)` 唯一、`(phone)` 唯一、`(team_id)`。

#### tasks（任务表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(40) PK | 任务 ID（业务主键，含时间戳） |
| user_id | BIGINT FK | 创建者 |
| project_id | BIGINT FK | 所属项目（可空） |
| status | ENUM | queued / running / paused / completed / failed |
| current_node | VARCHAR(40) | 当前执行节点 |
| user_intent | TEXT | 用户原始需求 |
| source_url | VARCHAR(500) | 参考视频 URL |
| target_duration_sec | INT | 目标时长 |
| target_platform | VARCHAR(20) | 目标平台 |
| config_json | JSON | 高级配置 |
| result_video_id | VARCHAR(40) FK | 成片 ID |
| error_info | JSON | 失败详情 |
| token_consumed | INT | 消耗 Token |
| generation_units_consumed | INT | 消耗的内部生成资源单位 |
| created_at | DATETIME | 创建时间 |
| completed_at | DATETIME | 完成时间 |

索引：`(user_id, created_at)`、`(status)`、`(project_id)`。

#### videos（视频成品表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(40) PK | 视频 ID |
| task_id | VARCHAR(40) FK | 来源任务 |
| user_id | BIGINT FK | 拥有者 |
| file_path | VARCHAR(255) | MinIO 对象键 |
| cover_path | VARCHAR(255) | 封面对象键 |
| duration_sec | DECIMAL(6,2) | 实际时长 |
| resolution | VARCHAR(20) | 分辨率 |
| file_size_bytes | BIGINT | 文件大小 |
| metadata_json | JSON | 镜头清单等元信息 |
| published_to | JSON | 发布平台与平台视频 ID |
| created_at | DATETIME | 创建时间 |

索引：`(user_id, created_at)`、`(task_id)`。

#### analytics（流量数据表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 自增 ID |
| video_id | VARCHAR(40) FK | 关联视频 |
| platform | VARCHAR(20) | 投放平台 |
| platform_video_id | VARCHAR(80) | 平台侧视频 ID |
| time_window | ENUM | 24h / 72h / 168h |
| views | BIGINT | 播放量 |
| completion_rate | DECIMAL(5,4) | 完播率 |
| engagement_rate | DECIMAL(5,4) | 互动率 |
| conversion_rate | DECIMAL(5,4) | 转化率 |
| performance_tier | ENUM | hit / normal / poor |
| collected_at | DATETIME | 抓取时间 |

索引：`(video_id, time_window)`、`(platform_video_id)`。

#### projects（项目表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 项目 ID |
| user_id | BIGINT FK | 创建者 |
| team_id | BIGINT FK | 所属团队（可空） |
| name | VARCHAR(80) | 项目名 |
| description | TEXT | 描述 |
| brand_lora_id | VARCHAR(40) | 品牌 LoRA ID（可空） |
| created_at | DATETIME | 创建时间 |

索引：`(user_id)`、`(team_id)`。

#### loras（用户 LoRA 表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(40) PK | LoRA ID |
| user_id | BIGINT FK | 拥有者 |
| name | VARCHAR(80) | 名称 |
| training_status | ENUM | training / ready / failed |
| model_path | VARCHAR(255) | MinIO 对象键 |
| training_image_count | INT | 训练图片数 |
| created_at | DATETIME | 创建时间 |

### 4.3 Redis Key 设计

| 用途 | Key 模板 | TTL |
|------|---------|-----|
| Task Checkpoint | `vidgen:checkpoint:{task_id}:{node}:{phase}` | 7 天 |
| Task 实时状态 | `vidgen:task:status:{task_id}` | 24 小时 |
| 任务执行队列 | `vidgen:queue:tasks`（Redis Stream） | 持久 |
| HITL 等待队列 | `vidgen:queue:hitl` | 持久 |
| 用户登录态 | `vidgen:session:{user_id}` | 2 小时 |
| ComfyUI 模型加载状态 | `vidgen:model:loaded:{node_id}` | 30 分钟 |
| 频率限制 | `vidgen:ratelimit:{user_id}:{endpoint}` | 1 分钟 |

### 4.4 Qdrant Collection 设计

| Collection | 维度 | 用途 | Payload 字段 |
|-----------|------|------|------------|
| `script_examples` | 1024 | 脚本结构 + 流量表现历史检索 | duration_sec, pacing_tier, performance_tier, user_id, video_id |
| `reference_videos` | 1024 | 参考视频特征检索 | source_url, style_keywords, shot_count |
| `material_assets` | 1024 | 用户素材库语义检索 | user_id, asset_type, tags |

向量化模型统一使用 BGE-M3，1024 维。

### 4.5 MinIO Bucket 设计

| Bucket | 用途 | 访问策略 |
|-------|------|---------|
| `vidgen-uploads` | 用户上传素材 | 私有，签名 URL 访问 |
| `vidgen-clips` | 中间镜头片段 | 私有 |
| `vidgen-videos` | 最终成片 | 私有，可生成时效签名 URL |
| `vidgen-covers` | 视频封面 | 公开读 |
| `vidgen-loras` | 用户 LoRA 模型文件 | 私有 |

---

## 5. 技术栈

### 5.1 前端技术栈

| 类别 | 选型 | 理由 |
|------|------|------|
| 框架 | Vue 3 + TypeScript | 团队熟悉度高，生态成熟 |
| 状态管理 | Pinia | Vue 3 官方推荐 |
| UI 组件库 | Element Plus | 企业级组件齐全 |
| 路由 | Vue Router 4 | 标配 |
| HTTP 客户端 | Axios | 标配 |
| 实时通信 | 原生 WebSocket + 心跳保活 | 任务进度推送 |
| 视频播放器 | Video.js + 自定义镜头时间轴层 | 支持镜头级标记 |
| 构建工具 | Vite | 比 Webpack 快 5—10 倍 |
| 包管理 | pnpm | 节省磁盘，支持 monorepo |

### 5.2 后端技术栈

| 类别 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.11 | LLM 和 AI 生态首选 |
| Web 框架 | FastAPI | 异步原生、自动 OpenAPI |
| ORM | SQLAlchemy 2.0 + Alembic | 业界标准，支持异步 |
| Agent 编排 | LangGraph | 原生支持 StateGraph、Checkpoint、HITL |
| LLM 抽象 | LangChain BaseChatModel | 多模型切换 |
| 任务队列 | Celery + Redis | 长时任务异步执行 |
| 向量检索 | Qdrant Client | 与基础设施层对应 |
| 缓存 | Redis | 同时承担队列、Checkpoint、缓存 |
| 对象存储 | MinIO Python SDK | S3 兼容 |
| 日志 | structlog + JSON formatter | 结构化日志 |
| 监控 | OpenTelemetry + Prometheus + Grafana | 标准化可观测 |
| 测试 | pytest + pytest-asyncio | 标配 |

### 5.3 AI 模型与能力

| 类别 | 选型 | 用途 |
|------|------|------|
| 主 LLM（生产） | DeepSeek-V3.5 | 脚本生成、决策、分析 |
| 主 LLM（开发） | Claude Opus 4.7 | 开发调试 |
| 多模态理解 | Qwen-VL-Max | 镜头视觉理解 |
| 视频生成（文生视频） | Wan2.2 | 通用文生视频 |
| 视频生成（图生视频） | Wan2.2 i2v | 图生视频 |
| 视频换头 | VACE 视频条件控制框架 | 复用小影实战工作流 |
| 图像编辑 | Qwen-image-edit / Flux2-klein | 单帧编辑 |
| 文本嵌入 | BGE-M3 | 1024 维文本向量化 |
| 镜头分割 | PySceneDetect | 镜头切分 |
| 字幕对齐 | whisper-timestamped | 毫秒级字幕对齐 |
| 文本转语音 | 火山引擎 TTS | 多语种、多音色 |
| 视频处理 | FFmpeg | 标配 |
| ComfyUI | 自部署 | 视频生成工作流执行引擎 |

### 5.4 MCP 与 AI Coding

| 用途 | 工具 |
|------|------|
| MCP Server 实现 | FastMCP |
| AI Coding 工具支持 | Claude Code、Codex、Copilot、Cursor |
| 项目级 AI 上下文 | CLAUDE.md（约定项目结构、技术栈、API 风格） |

### 5.5 基础设施与运维

| 类别 | 选型 |
|------|------|
| 容器化 | Docker + Docker Compose（开发）/ Kubernetes（生产） |
| CI/CD | GitHub Actions / GitLab CI |
| 镜像仓库 | Harbor 私有仓库 |
| 配置管理 | Pydantic Settings + .env 文件 |
| 密钥管理 | Vault / K8s Secret |
| 服务网关 | Nginx（开发）/ APISIX（生产） |

---

## 6. 开发与部署

### 6.1 开发环境

#### 6.1.1 本地依赖

| 工具 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.11 | 后端语言 |
| Node.js | 20 LTS | 前端构建 |
| pnpm | 9 | 前端包管理 |
| Docker | 24 | 容器化 |
| Docker Compose | 2.20 | 编排基础服务 |
| Git | 2.40 | 版本控制 |
| make | — | 任务脚本 |

#### 6.1.2 本地开发启动流程

```bash
# 克隆仓库
git clone https://github.com/<org>/vidgen.git
cd vidgen

# 启动基础服务（PostgreSQL、Redis、Qdrant、MinIO）
docker compose -f docker-compose.dev.yml up -d

# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head           # 数据库迁移
python -m vidgen.scripts.seed  # 初始化种子数据
uvicorn vidgen.main:app --reload --port 8000

# 前端
cd ../frontend
pnpm install
pnpm dev                        # http://localhost:5173

# Worker
cd ../backend
celery -A vidgen.celery_app worker -l info
```

#### 6.1.3 开发工具集成

`.vscode/settings.json` 推荐配置 Python 解释器路径、ESLint 自动修复、Prettier 自动格式化。
`pre-commit` 钩子统一执行 ruff、mypy、prettier，保证提交前代码合规。
项目根目录的 `CLAUDE.md` 描述项目结构和约定，配合 Claude Code / Codex 使用。

### 6.2 部署方案

#### 6.2.1 部署拓扑

```
        ┌──────────────────────────────────────┐
        │          Nginx / APISIX 网关          │
        └────────────────┬─────────────────────┘
                         │
        ┌────────────────┴─────────────────────┐
        │                                      │
   ┌────▼─────┐                          ┌────▼─────┐
   │ Frontend │                          │   API    │
   │ (Nginx)  │                          │ (FastAPI)│
   └──────────┘                          └────┬─────┘
                                              │
              ┌───────────────────────────────┼─────────────────┐
              │                               │                 │
        ┌─────▼──────┐                  ┌─────▼─────┐    ┌──────▼──────┐
        │  Worker    │                  │  MCP      │    │  WebSocket  │
        │  (Celery)  │                  │  Server   │    │     Hub     │
        └──┬─────────┘                  └───────────┘    └─────────────┘
           │
   ┌───────┴───────────────────────────────────────────────────────┐
   │                                                                │
┌──────▼─────┐  ┌─────┐  ┌──────┐  ┌──────┐  ┌────────────────────────┐
│PostgreSQL │  │Redis│  │Qdrant│  │MinIO │  │ComfyUI Server (GPU L20)│
└────────────┘  └─────┘  └──────┘  └──────┘  └────────────────────────┘
```

#### 6.2.2 Kubernetes 资源规划

| 服务 | 副本数 | CPU | 内存 | GPU | 备注 |
|------|-------|-----|------|-----|------|
| Frontend (Nginx) | 2 | 0.5 | 512 Mi | — | 前端静态资源 |
| API Gateway | 3 | 1 | 1 Gi | — | 水平扩展 |
| Worker | 5 | 2 | 4 Gi | — | 任务执行 |
| MCP Server | 2 | 0.5 | 1 Gi | — | 对外接口 |
| WebSocket Hub | 2 | 0.5 | 1 Gi | — | 实时推送 |
| ComfyUI Server | 按需 | 8 | 64 Gi | 1×L20（90 GiB VRAM） | GPU 节点 |
| PostgreSQL | 1 主 1 从 | 4 | 16 Gi | — | 持久化 |
| Redis | 哨兵模式 3 节点 | 1 | 4 Gi | — | 持久化 |
| Qdrant | 1（V1） | 2 | 8 Gi | — | 向量库 |
| MinIO | 4 节点分布式 | 2 | 4 Gi | — | 对象存储 |

#### 6.2.3 环境分级

| 环境 | 用途 | 数据 | 接入 |
|------|------|------|------|
| dev | 本地开发 | mock 数据 | 开发者本机 |
| staging | 预发布 | 脱敏生产数据 | 团队内部 |
| production | 生产 | 真实数据 | 公网 |

环境间的差异通过 ConfigMap 和 Secret 注入，代码版本完全一致。

### 6.3 CI/CD

#### 6.3.1 GitHub Actions 工作流

CI 在每次 push 和 PR 时执行：

```yaml
name: CI
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ruff check . && ruff format --check .
      - run: mypy vidgen/
      - run: cd frontend && pnpm install && pnpm lint

  test-backend:
    runs-on: ubuntu-latest
    services:
      mysql:
        image: mysql:8.0
        env: { MYSQL_ROOT_PASSWORD: test }
      redis:
        image: redis:7
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[dev]"
      - run: pytest --cov=vidgen --cov-report=xml
      - uses: codecov/codecov-action@v4

  test-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cd frontend && pnpm install && pnpm test:unit

  build:
    needs: [lint, test-backend, test-frontend]
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t vidgen-api:${{ github.sha }} -f backend/Dockerfile .
      - run: docker build -t vidgen-web:${{ github.sha }} -f frontend/Dockerfile .
      - run: docker push ...
```

#### 6.3.2 部署流水线

| 阶段 | 触发 | 动作 |
|------|------|------|
| dev | 任意 push | 自动部署到 dev 环境 |
| staging | tag 创建（v\*.\*.\*-rc.\*） | 部署到 staging，运行 E2E 测试 |
| production | tag 创建（v\*.\*.\*） + 人工审批 | 蓝绿部署 |

回滚策略：每次部署保留最近 3 个版本，单命令回滚。

#### 6.3.3 数据库迁移

使用 Alembic 管理迁移脚本。每个 PR 涉及 schema 变更必须包含 migration。CI 阶段自动验证：

```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

确保迁移可逆。

---

## 7. 项目初始化（起步操作）

本节是项目从零到第一次跑通的具体步骤，给 AI Coder 和新加入的开发者参照。

### 7.1 项目结构初始化

```
vidgen/
├── README.md
├── CLAUDE.md                    # AI Coder 工作指引
├── pyproject.toml
├── docker-compose.dev.yml
├── docker-compose.prod.yml
├── Makefile
├── .github/workflows/           # CI/CD
├── docs/
│   ├── product-brief.md
│   └── system-design.md
├── backend/
│   ├── vidgen/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # Pydantic Settings
│   │   ├── celery_app.py
│   │   ├── api/                 # 路由层
│   │   ├── agents/              # 7 个 Agent 实现
│   │   ├── tools/               # ToolRegistry 和具体工具
│   │   ├── models/              # SQLAlchemy ORM
│   │   ├── schemas/             # Pydantic schemas
│   │   ├── services/            # 业务逻辑
│   │   ├── infra/               # 基础设施适配（Redis、Qdrant、MinIO）
│   │   ├── graph/               # LangGraph StateGraph 定义
│   │   └── scripts/             # 一次性脚本（seed、迁移）
│   ├── alembic/                 # 数据库迁移
│   ├── tests/
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── views/               # 8 个页面
│   │   ├── components/
│   │   ├── stores/              # Pinia
│   │   ├── api/                 # axios 封装
│   │   └── router/
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
└── infra/
    ├── k8s/                     # K8s 配置
    └── nginx/
```

### 7.2 第一次运行检查清单

- [ ] 安装本地依赖（Python 3.11、Node 20、Docker 24、pnpm 9）。
- [ ] 复制 `.env.example` 到 `.env` 并填写本地 LLM API Key（DeepSeek 或 Claude）。
- [ ] 启动基础服务：`docker compose -f docker-compose.dev.yml up -d`。
- [ ] 后端虚拟环境：`cd backend && python -m venv .venv && source .venv/bin/activate`。
- [ ] 安装 Python 依赖：`pip install -e ".[dev]"`。
- [ ] 数据库迁移：`alembic upgrade head`。
- [ ] 种子数据：`python -m vidgen.scripts.seed`。
- [ ] 启动后端：`uvicorn vidgen.main:app --reload --port 8000`。
- [ ] 启动 Worker：`celery -A vidgen.celery_app worker -l info`。
- [ ] 安装前端依赖：`cd ../frontend && pnpm install`。
- [ ] 启动前端：`pnpm dev`。
- [ ] 访问 `http://localhost:5173` 完成首次登录。
- [ ] 访问 `http://localhost:8000/docs` 查看 OpenAPI 文档。
- [ ] 创建一个测试任务，确认 7 个 Agent 节点全部跑通。

### 7.3 实施里程碑

| 阶段 | 时间窗口 | 交付物 | 验收标准 |
|------|---------|--------|---------|
| M1 骨架 | Week 1—2 | 项目结构、GraphState、ToolRegistry、单 Agent | 单 Agent 可独立运行并写入 State |
| M2 主链路 | Week 3—5 | Reference→Script→Storyboard→Generation→Assembly 端到端 | 输入 URL 可产出 MP4（无 QA） |
| M3 QA 与 HITL | Week 6 | QA 节点、回退路由、Checkpoint 续跑 | QA 失败可自动回退，任务可断点续跑 |
| M4 流量回看 | Week 7—8 | AnalyticsAgent、Qdrant 沉淀回环 | 已发布视频流量数据可入库，影响下一轮生成 |
| M5 工程化 | Week 9—10 | MCP Server、Docker Compose、监控看板 | 一键启动开发环境，关键指标可看板查询 |
| M6 前端工作台 | Week 11—12 | 8 个页面完整实现 | 用户可完成完整使用闭环 |
| M7 部署上线 | Week 13—14 | K8s 部署、CI/CD、灰度发布 | 生产环境跑通，蓝绿可切换 |

### 7.4 给 AI Coder 的工作约定

在 `CLAUDE.md` 中固化以下约定，让 AI Coder 在生成代码时严格遵守：

- 后端使用类型注解，所有函数必须有完整 type hints。
- 所有 Agent 实现必须遵循 `async def agent(state: GraphState) -> GraphState` 签名。
- 所有外部能力必须通过 ToolRegistry 注册，禁止直接 import 第三方 SDK。
- 数据库操作走 SQLAlchemy 2.0 异步 API，禁止裸 SQL。
- 异常必须从 `vidgen.exceptions` 模块定义的体系派生。
- 所有日志通过 structlog 输出，必须包含 `trace_id`。
- 提交前自动通过 ruff、mypy、pytest 检查。

---

*VidGen System Design v1.0*
*—— END OF DOCUMENT ——*
