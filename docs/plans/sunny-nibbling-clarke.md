# 视频混剪功能实现计划

## Context

当前系统支持两种视频生成路径：(1) 从零生成（orchestrator → prompt_engineer → video_generator → editor）和 (2) 参考视频复刻（replication_planner → prompt_engineer → ...）。用户希望新增第三种路径：**多视频混剪**——上传多个视频后，AI 分析并生成混剪方案，用户确认后自动从源视频中提取片段并拼接成片。

关键区别：混剪不需要 AI 生成新视频片段（无需 video_generator），而是从已有视频中**提取时间段片段**并组装。

## 架构决策

**在现有 LangGraph 管道中新增第三条路由分支**，复用现有的 SSE 推送、HITL 确认、DB 模型、checkpoint 机制。

```
START
  ├─[reference_video_ids 有 2+ 项] → remix_planner → (HITL等待确认) → remix_assembler → END
  ├─[reference_video_id 单个]      → replication_planner → prompt_engineer → ...
  └─[无参考视频]                    → orchestrator → prompt_engineer → ...
```

## 视频分析策略：三层漏斗（Token 高效）

**核心原则**：不直接把完整视频发给 LLM，而是用 FFmpeg 自动化做重活，LLM 只做轻量语义理解和创意决策。

### 层级 1：FFmpeg 自动化预分析（零 token 成本）

对每个视频执行以下自动化分析，生成 **ShotProfile**：
- **镜头边界检测**：FFmpeg `select='gt(scene,{threshold})'` 自动识别所有镜头切换点 → 得到时间戳列表
- **关键帧提取**：在每个镜头切换点提取 JPEG 关键帧（已有 `KeyframeExtractor`）
- **时长探测**：ffprobe 获取每个镜头段的精确时长
- **音频能量分析**：`ffmpeg -af volumedetect` 获取每个镜头段的 mean_volume / max_volume → 高能量片段往往是高光时刻
- **运动强度评估**：scene_change 分数本身反映帧间差异，高分 = 高运动强度

每个镜头产出一个 metadata 结构：
```python
@dataclass
class ShotProfile:
    video_id: str
    shot_idx: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    keyframe_path: str          # 关键帧图片路径
    scene_change_score: float   # 运动强度指标
    audio_mean_volume: float    # 音频平均响度 (dB)
    audio_max_volume: float     # 音频峰值响度 (dB)
```

### 层级 2：关键帧图片语义分析（低 token 成本）

- 将所有关键帧**图片**（非视频）批量发给 Qwen 多模态模型
- 一张图 ≈ 几百 token，3 个视频 × ~10 镜头 = ~30 张图片 ≈ 1-2 万 token（远低于 3 个完整视频的数十万 token）
- LLM 为每个镜头返回：场景内容描述、情感标签（exciting/calm/dramatic/...）、视觉质量评分 (1-10)
- 补充到 ShotProfile 中

### 层级 3：LLM 创意规划（一次调用，中等 token 成本）

- 输入：所有视频的 ShotProfile 列表（含语义描述）+ 用户的混剪偏好（目标时长、风格等）
- LLM 做**创意决策**：从所有镜头中选择最佳片段、设计叙事结构、编排节奏、选择转场
- 输出：结构化混剪方案 JSON

**Token 成本对比**：
| 方案 | 估算 Token |
|------|-----------|
| 旧方案：3 个视频直接发 LLM | ~30-50 万 token |
| 新方案：30 张关键帧图片 + 1 次规划 | ~2-4 万 token |
| **节省** | **~90%** |

---

## 实现步骤

### Phase 1: 核心基础设施

#### 1.1 视频预分析服务（新建）
- **新建** `backend/app/services/video_editing/video_profiler.py`
- `VideoProfiler` 类：对单个视频执行层级 1 的完整自动化分析
- 方法：`async def profile_video(video_path: str, video_id: str) -> VideoProfile`
  - 调用 `KeyframeExtractor.extract(strategy="scene_change")` 获取镜头边界+关键帧
  - 对每个镜头段用 `ffmpeg -ss {start} -to {end} -af volumedetect` 获取音频能量
  - 汇总为 `VideoProfile`（包含 `list[ShotProfile]` + 视频元数据）
- 依赖：`KeyframeExtractor`，`media_utils.run_subprocess()`

#### 1.2 片段提取服务（新建）
- **新建** `backend/app/services/video_editing/clip_extractor.py`
- `ClipExtractorService.extract_clip(source_path, start_seconds, end_seconds, output_path)`
- FFmpeg 命令：`ffmpeg -y -ss {start} -to {end} -i {source} -c:v libx264 -c:a aac -af "afade=t=in:d=0.15,afade=t=out:st={dur-0.15}:d=0.15" {output}`
- 重编码保证帧精确，音频边界自动 fade 避免突变
- 复用 `media_utils.py` 中的 `run_subprocess()` 和 `probe_duration()`

#### 1.3 新增 HITL 异常
- **修改** `backend/app/agents/executors/langgraph/exceptions.py`
- 添加 `WaitingRemixConfirmation(Exception)` 类

#### 1.4 Pipeline 状态扩展
- **修改** `backend/app/agents/executors/langgraph/state.py`
- 在 `LangGraphPipelineState` 中添加 `remix_plan: dict` 字段

#### 1.5 数据库迁移
- 新增 Alembic 迁移：`PipelineRun.status` CHECK 约束添加 `'waiting_remix_confirmation'`

#### 1.6 请求 Schema
- **修改** `backend/app/schemas/pipeline.py`
- `PipelineCreateRequest` 添加：
  - `reference_video_ids: list[str] = Field(default_factory=list)` — 混剪模式多视频 ID
  - `remix_config: Optional[RemixConfigRequest] = None`
- 新增 Schema：
  - `RemixConfigRequest`：`target_duration_seconds`, `mood`, `bgm_mood`, `bgm_volume`, `include_source_audio`
  - `ConfirmRemixRequest`：`approved`, `adjustments`, `edited_segments`
  - `RemixSegmentEdit`：`segment_idx`, `source_video_id`, `start_seconds`, `end_seconds`, `transition_type`, `removed`

---

### Phase 2: 混剪规划 Agent

#### 2.1 RemixPlannerAgent（新建）
- **新建** `backend/app/agents/stages/remix_planner.py`
- 继承 `BaseAgent`，`name = "remix_planner"`
- 依赖注入：`LLMService`, `VideoProfiler`

**执行流程（三层漏斗）**：

```
Step 1: 并行预分析所有视频
  for each video_id in reference_video_ids:
    video_path = resolve from VideoUpload DB
    profile = await video_profiler.profile_video(video_path, video_id)
  → 得到 list[VideoProfile]，每个含多个 ShotProfile

Step 2: 关键帧语义分析
  收集所有 ShotProfile 的关键帧图片路径
  调用 llm.generate_structured(
    system=REMIX_SHOT_ANALYSIS_PROMPT,
    images=[all keyframe paths],
    schema=ShotAnalysisSchema  # 批量返回每个镜头的描述和标签
  )
  → 将语义信息合并到 ShotProfile

Step 3: 混剪方案生成
  构造上下文文本：所有视频的 ShotProfile 列表（含 metadata + 语义描述）
  调用 llm.generate_structured(
    system=REMIX_PLANNING_PROMPT,
    user=context_text + user_preferences,
    schema=RemixPlanSchema
  )
  → 得到结构化混剪方案

Step 4: 关键帧路径校验
  验证方案中每个 segment 的 reference_keyframe_path 是否存在
  不存在则用时间最近的关键帧替代（参考 ReplicationPlannerAgent 的 frame normalization）

return AgentResult(
  success=True,
  output_data={"requires_confirmation": True, "remix_plan": plan}
)
```

#### 2.2 混剪方案 JSON Schema
```json
{
  "title": "string — 混剪视频标题建议",
  "concept": "string — 创意理念/叙事线索",
  "target_duration_seconds": "number",
  "source_videos": [{
    "video_id": "string",
    "duration_seconds": "number",
    "total_shots": "integer",
    "analysis_summary": "string — 视频整体内容/风格概述"
  }],
  "segments": [{
    "segment_idx": "integer",
    "source_video_id": "string",
    "source_shot_idx": "integer — 对应源视频的镜头索引",
    "start_seconds": "number",
    "end_seconds": "number",
    "description": "string — 片段内容及选择原因",
    "role": "intro|buildup|highlight|transition|climax|outro",
    "quality_score": "number — 综合质量评分 (1-10)",
    "transition_to_next": "cut|fade|dissolve|slideright|slideup",
    "transition_duration": "number (秒, 默认 0.5)",
    "reference_keyframe_path": "string"
  }],
  "audio_design": {
    "strategy": "source_audio|bgm_only|silent",
    "bgm_mood": "string",
    "bgm_volume": "number"
  },
  "analysis_report": "string — 人类可读的混剪决策说明（为什么选这些片段、如何编排）"
}
```

#### 2.3 系统提示词
- **修改** `backend/app/prompts/system_prompts.py`
- `REMIX_SHOT_ANALYSIS_PROMPT`：指导 LLM 对一批关键帧图片进行批量语义分析（场景描述、情感标签、质量评分）
- `REMIX_PLANNING_PROMPT`：指导 LLM 基于所有镜头的 profile 数据（metadata + 语义）生成混剪方案，包含：
  - 叙事结构设计（开场→递进→高潮→收尾）
  - 节奏编排（利用 audio_energy 和 motion_score 识别高光时刻）
  - 片段选择标准（质量评分 > 阈值、避免重复场景、时长匹配）
  - 转场选择（相似场景用 cut，不同场景用 fade/dissolve）

---

### Phase 3: 混剪组装 Agent

#### 3.1 RemixAssemblerAgent（新建）
- **新建** `backend/app/agents/stages/remix_assembler.py`
- 继承 `BaseAgent`，`name = "remix_assembler"`
- 依赖注入：`ClipExtractorService`

**执行流程**：
1. 从 `context.artifacts["remix_plan"]` 读取已确认的混剪方案
2. 解析每个 video_id 到实际文件路径（查 VideoUpload DB）
3. 遍历 `segments`，并行调用 `ClipExtractorService.extract_clip()` 提取视频片段
4. 根据 `audio_design.strategy` 处理音频：
   - `source_audio`：保留各片段原始音频
   - `bgm_only`：提取片段时 `-an`（静音），最后叠加 BGM
   - `silent`：提取片段时 `-an`
5. 按 `segment_idx` 顺序，使用 `_concat_with_xfade()`（helpers.py）拼接片段
6. 可选叠加 BGM（复用 `composer.py` 的 BGM 混音 FFmpeg 模式）
7. 返回 `AgentResult` 包含 `final_video_path`

---

### Phase 4: 管道集成

#### 4.1 Graph 路由
- **修改** `backend/app/agents/executors/langgraph/executor.py`
  - `__init__` 添加参数 `remix_planner: Optional[RemixPlannerAgent]`, `remix_assembler: Optional[RemixAssemblerAgent]`
  - `_route_first_stage` 新增：`reference_video_ids` 列表长度 >= 2 → `"remix_planner"`
  - `_build_graph` 添加节点和边：
    ```python
    builder.add_node("remix_planner", self._remix_planner_node)
    builder.add_node("remix_assembler", self._remix_assembler_node)
    builder.add_edge("remix_planner", "remix_assembler")  # HITL 异常会中断
    builder.add_edge("remix_assembler", END)
    ```
  - conditional_edges 映射表添加 `"remix_planner": "remix_planner"`
  - `run()` 添加 `except WaitingRemixConfirmation` 处理

#### 4.2 Node 实现
- **修改** `backend/app/agents/executors/langgraph/nodes.py`
  - `_remix_planner_node`：执行 RemixPlannerAgent，保存 artifacts，`raise WaitingRemixConfirmation()`
  - `_remix_assembler_node`：执行 RemixAssemblerAgent，保存 final_video 到 artifacts
  - `resume_from_remix_confirmation(context, input_config)`：从 HITL 恢复，直接运行 remix_assembler

#### 4.3 API 端点
- **修改** `backend/app/routers/pipeline.py`
  - 新增 `POST /api/projects/{project_id}/pipeline/{run_id}/confirm-remix`
  - 参照 `confirm_replication_plan` 端点：读取 artifacts → 应用用户编辑 → 启动后台任务运行 remix_assembler

#### 4.4 DI 装配
- **修改** `backend/app/bootstrap.py`
  - 创建 `VideoProfiler`、`ClipExtractorService`、`RemixPlannerAgent`、`RemixAssemblerAgent`
  - 注入到 `LangGraphPipelineExecutor`

---

### Phase 5: 前端适配

#### 5.1 API 层
- **修改** `frontend/src/api/pipeline.ts`
  - `PipelineConfig` 类型添加 `reference_video_ids` 和 `remix_config`
  - 新增 `confirmRemixPlan(projectId, runId, body)` 函数

#### 5.2 上传流程
- 现有上传端点支持单文件上传，前端多次调用即可收集多个 video_upload_id
- **修改** `frontend/src/stores/pipelineStore.ts`，添加 `remixVideoIds: string[]` 状态

#### 5.3 混剪方案确认 UI
- **新建** `frontend/src/components/remix/RemixPlanReview.vue`
- 展示：时间轴可视化（颜色区分源视频）、每个片段关键帧缩略图、时间范围、叙事角色、质量评分
- 交互：调整时间范围、删除片段、修改转场、确认/拒绝

#### 5.4 SSE 状态适配
- 前端识别 `waiting_remix_confirmation` 状态渲染对应 UI

---

## 需要新建的文件

| 文件 | 作用 |
|-----|------|
| `backend/app/services/video_editing/video_profiler.py` | 视频预分析服务（FFmpeg 自动化镜头分析） |
| `backend/app/services/video_editing/clip_extractor.py` | FFmpeg 片段提取服务 |
| `backend/app/agents/stages/remix_planner.py` | 混剪规划 Agent（三层漏斗） |
| `backend/app/agents/stages/remix_assembler.py` | 混剪组装 Agent |
| `frontend/src/components/remix/RemixPlanReview.vue` | 混剪方案确认 UI |

## 需要修改的文件

| 文件 | 改动 |
|-----|------|
| `backend/app/agents/executors/langgraph/executor.py` | 添加混剪路由、节点、HITL 异常处理 |
| `backend/app/agents/executors/langgraph/nodes.py` | 添加 remix 节点方法和 resume 方法 |
| `backend/app/agents/executors/langgraph/state.py` | 添加 `remix_plan` 字段 |
| `backend/app/agents/executors/langgraph/exceptions.py` | 添加 `WaitingRemixConfirmation` |
| `backend/app/schemas/pipeline.py` | 添加混剪相关请求/响应 Schema |
| `backend/app/routers/pipeline.py` | 添加 confirm-remix 端点 |
| `backend/app/prompts/system_prompts.py` | 添加混剪分析和规划提示词 |
| `backend/app/bootstrap.py` | DI 装配新服务和 Agent |
| `frontend/src/api/pipeline.ts` | 添加混剪 API 函数 |
| `frontend/src/stores/pipelineStore.ts` | 添加混剪状态 |

## 关键复用

| 复用内容 | 文件路径 |
|---------|---------|
| 关键帧提取 + 镜头检测 | `backend/app/services/keyframe_extractor.py` → `FFmpegKeyframeExtractor` |
| FFmpeg 异步执行 | `backend/app/services/media_utils.py` → `run_subprocess()`, `probe_duration()` |
| xfade 拼接 | `backend/app/services/video_editing/helpers.py` → `_concat_with_xfade()` |
| BGM 混音 | `backend/app/services/video_editing/composer.py` → BGM 处理逻辑 |
| HITL 异常+恢复模式 | `backend/app/agents/executors/langgraph/exceptions.py` + `nodes.py` |
| ReplicationPlanner 的帧路径校验 | `backend/app/agents/stages/replication_planner.py` → frame normalization |

## 模型能力说明

**无需额外模型接入**。当前 `qwen3-omni-flash` 多模态能力（图片输入）足以完成关键帧语义分析。通过三层漏斗策略，避免了直接发送视频的高 token 成本。

## 验证方案

1. **单元测试**：
   - `VideoProfiler`：验证镜头检测数量、ShotProfile metadata 完整性
   - `ClipExtractorService`：验证提取片段的 start/end 时间精度
2. **集成测试**：上传 2-3 个短视频 → 触发混剪管道 → 验证预分析 → 验证方案生成 → 确认 → 验证成片
3. **手动验证**：
   - API 上传多个视频到同一 project
   - 发起 pipeline（`reference_video_ids` 含多个 ID）
   - SSE 流观察分析进度："正在分析视频 1/3..."、"正在提取关键帧..."
   - `waiting_remix_confirmation` 时查看混剪方案（含关键帧缩略图）
   - 确认后验证最终视频包含正确片段和转场
