# VidGen 混剪音频优化方案

## Context

此前混剪流程（`remix_planner → remix_assembler → END`）完全绕过了 `AudioSubtitleAgent`，导致成片**无配乐、无配音、无字幕，且默认保留原声**。同时，混剪规划阶段缺少 BGM 来源解析，无法基于用户上传或系统生成的音乐来设计视频节奏。本计划系统性修复这些问题。

**实施状态（2026-05-13）**：已落地 BGM 来源优先级、素材库音频入库、自动模式 `bgm_material_id` 传参、Planner BGM 上下文、Assembler 上传/内置 BGM 应用、`mix` 策略，以及确认混剪后复用 `AudioSubtitleAgent` 生成旁白 / 字幕的链路。上传或生成 BGM 存在时，Planner / Assembler 会强制最终策略为 `bgm_only`，只有显式 `include_source_audio=true` 时才使用 `mix` 并降低原声。当前仍未接入独立音乐生成服务，未上传 BGM 时按内置曲库或静音降级。

---

## 根因分析

| 问题 | 根因 |
|------|------|
| 无 BGM | `RemixConfigRequest.bgm_mood` 默认 `"none"`；用户发起请求未传 bgm_mood |
| BGM 来源缺失 | 原流程没有 `bgm_material_id` 或等价字段，Planner 无法知道用户是否上传了音乐文件，也无法基于真实 BGM 设计混剪方案 |
| 无配音/字幕 | 混剪 graph 路由直接 `remix_assembler → END`，从未调用 `AudioSubtitleAgent` |
| 保留原声 | `include_source_audio=True` 为默认值，且无前端 UI 暴露此选项 |

---

## 方案设计

### 阶段零：BGM 来源解析与音乐先行规划（前置）

**目标**：在 `RemixPlannerAgent` 生成片段顺序、转场和音频策略前，先确定本次混剪是否有可用 BGM，以及 BGM 来自上传、生成、内置曲库还是无音乐。

**0.1 BGM 来源优先级**
- **用户上传优先**：如果 `remix_config.bgm_material_id` 存在，使用该音频素材；如果未显式指定但会话素材中已选择音频文件，则默认取第一个明确选中的音频素材作为 BGM。
- **按需求生成**：如果没有上传 BGM，但用户需求明显要求配乐、节奏感、卡点、氛围音乐等，则判断当前后端是否有音乐生成服务。
- **能力降级**：当前代码库未发现音乐生成服务，v1 默认按"不支持音乐生成"处理；有上传音频时按上传 BGM 设计，没有上传音频时直接按需求设计，并继续使用内置 `bgm_mood` 曲库或静音策略。
- **无音乐场景**：如果用户明确要求无音乐，或需求不需要 BGM，则 `bgm_source="none"`，不强行添加随机音乐。

**0.2 BGM 元信息解析**
- 文件：`backend/app/agents/stages/remix_planner.py`
- 在 `_build_plan()` 前解析 BGM 上下文：`bgm_source`、`bgm_material_id`、`bgm_path`、文件名、时长、基础音量/能量信息。
- 音频探测可复用 ffprobe / ffmpeg `volumedetect`，v1 至少提供文件名和时长；节拍/BPM 无现成能力时可留空，不阻塞流程。
- 将 BGM 上下文写入 LLM payload，让 Planner 根据 BGM 情绪、时长和能量安排片段顺序、转场密度、高潮位置和收尾方式。

**0.3 `audio_design` 扩展字段**
```json
{
  "strategy": "bgm_only",
  "bgm_source": "uploaded",
  "bgm_material_id": "material-id",
  "bgm_path": "/internal/path/to/bgm.mp3",
  "bgm_mood": "cinematic",
  "bgm_volume": 0.18
}
```
- `bgm_source` 取值：`uploaded | generated | library | none`
- `bgm_material_id`：上传 BGM 的素材 ID，可为空
- `bgm_path`：后端解析后的内部路径，仅内部使用，不作为公开 API 暴露
- `bgm_mood` / `bgm_volume` 保持兼容，用于内置曲库和默认混音

---

### 阶段一：BGM 支持（来源优先级 + BGM 应用）

**目标**：用户上传音乐文件时将该文件作为 BGM；未上传时根据需求判断是否需要生成或降级到内置 BGM / 静音；最终成片原声可选择保留、混合或静音。

**1.1 修改 schema 与默认值**
- 文件：`backend/app/schemas/pipeline.py`
- `RemixConfigRequest` 新增 `bgm_material_id: Optional[str] = None`
- 将 `RemixConfigRequest.include_source_audio` 默认值改为 `False`
- 将 `RemixConfigRequest.bgm_mood` 默认值改为 `"cinematic"`，仅在没有上传/生成 BGM 时作为内置曲库 fallback
- 新增 `voiceover_script: Optional[str] = None`（为阶段二预留）
- 新增 `add_voiceover: bool = False`

**1.2 自动模式素材入口支持音频**
- 文件：`backend/app/services/material_service.py`
- 当前素材库只索引图片；扩展 `get_media_type()` 和扫描/上传逻辑，让 `.mp3/.wav/.aac/.flac/.ogg/.m4a/.webm` 可作为 `media_type="audio"` 的素材入库。
- 文件：`frontend/src/components/pipeline/AutoModeStudio.vue`
- 保留 `image_ids` 只传图片素材；新增从 `selectedMaterials` 中筛选音频素材，混剪启动时将第一个音频素材 ID 写入 `remix_config.bgm_material_id`。
- UI 上音频素材无需进入分镜图片列表，但应能在会话素材区被选中、移除和显示文件名。

**1.3 RemixPlanner 使用 BGM 上下文规划**
- 文件：`backend/app/agents/stages/remix_planner.py`
- 新增 BGM 解析方法：优先解析 `remix_config.bgm_material_id`，其次从会话选择的音频素材中取第一个。
- 校验音频素材必须属于当前用户/会话，且 `media_type` 为 `audio`；若显式传入的 `bgm_material_id` 不存在、无权限或不是音频，直接返回可读错误，不静默换成随机 BGM。
- 如果 BGM 来源为 `uploaded` 或 `generated`，Planner 侧先把 `audio_design.strategy` 规范为 `bgm_only`；只有 `include_source_audio=true` 时允许 `mix`。
- 文件：`backend/app/prompts/system_prompts.py`
- 更新 `REMIX_PLANNING_PROMPT`：如果提供上传或生成 BGM，必须围绕该 BGM 的情绪、时长和能量设计片段顺序、节奏、转场和收尾；如果无音乐生成能力且无上传 BGM，则按需求直接规划并使用 `library` 或 `none` 降级。

**1.4 RemixAssembler 使用上传/生成 BGM 文件**
- 文件：`backend/app/agents/stages/remix_assembler.py`，`_apply_audio_design()` 方法
- 优先使用 `audio_design.bgm_path` 作为 BGM 文件；只有 `bgm_path` 为空且 `bgm_source="library"` 时才调用 `_find_bgm(bgm_mood)`。
- `bgm_source="uploaded"` 或 `"generated"` 但 `bgm_path` 不存在时应失败并返回可读错误，避免用户以为使用了上传音乐，实际却被替换为随机 BGM。
- Assembler 入口再次计算最终有效 strategy：有上传/生成 BGM 时覆盖 LLM 返回的 `source_audio` / `silent` 为 `bgm_only`，并用无音轨方式抽取源视频片段；仅 `include_source_audio=true` 时保留弱原声并使用 `mix`。
- 继续支持 BGM fade in/out、循环到目标时长、按 `bgm_volume` 控制音量。

**1.5 支持 BGM + 原声混音**（可选增强）
- 文件：`backend/app/agents/stages/remix_assembler.py`，`_apply_audio_design()` 方法
- 当前逻辑：`source_audio` 和 `bgm_only` 互斥
- 新增 `"mix"` 策略：用 ffmpeg `amerge` 或 `amix` 将原声（降低到 0.3 音量）+ BGM 混合

**1.6 确认内置 BGM 文件路径**
- 检查 `settings.BGM_DIR` 下是否有 `cinematic.mp3` 等文件
- `backend/app/services/video_editing/helpers.py` 中的 `_find_bgm()` 函数负责查找内置曲库 fallback

---

### 阶段二：AI 配音 + 字幕（中等改动）

**选择方案：复用 AudioSubtitleAgent，仍不进入 PromptEngineer / VideoGenerator**

理由：混剪不生成新视频片段，不适合重新进入 PromptEngineer / VideoGenerator；但旁白、TTS、字幕文件和中间产物入仓应复用现有 `AudioSubtitleAgent`，避免在 `RemixAssemblerAgent` 里重复实现音频生成。

**2.1 混剪确认后执行 AudioSubtitleAgent**
- 文件：`backend/app/agents/executors/shared.py`
- 文件：`backend/app/agents/executors/pipeline.py`
- 文件：`backend/app/agents/executors/langgraph/nodes.py`
- 当 `remix_config.add_voiceover=true` 时，把 `remix_plan.segments` 的旁白 / 描述字段和 `audio_design` 语音参数转成 `AudioSubtitleAgent` 输入，生成 `audio_path` 与 `subtitle_path`

**2.2 从 remix_plan 生成旁白脚本**
- 如果 `input_config["add_voiceover"]` 为 True 且 `voiceover_script` 为空：
  - 拼接各 segment 的 `description` 字段生成简单脚本
  - 或：调用 LLM 基于描述生成连贯旁白（可选）

**2.3 TTS 生成音频**
- 调用现有 `AudioSubtitleAgent`，由它内部调用 TTSService 生成旁白音频

**2.4 字幕生成**
- 使用 `AudioSubtitleAgent` 输出的 `subtitle_path`（SRT/ASS）
- 如果音频 Agent 因脚本为空跳过，Assembler 保持无旁白 / 无字幕，不阻塞混剪输出

**2.5 ffmpeg 叠加字幕 + 混合 TTS 音频**
- 在 `_apply_audio_design()` 后，新增 `_apply_voiceover_subtitles()` 方法
- 优先使用 `AudioSubtitleAgent` 输出的 `audio_path` / `subtitle_path`
- 用 ffmpeg `subtitles` filter 叠加字幕
- 用 ffmpeg `amix` 将 TTS 音频（主）+ BGM（次，可选）混合

---

## 关键文件

| 文件 | 修改内容 |
|------|---------|
| `backend/app/schemas/pipeline.py` | `RemixConfigRequest` 新增 `bgm_material_id`, `add_voiceover`, `voiceover_script`；修改默认值 |
| `backend/app/services/material_service.py` | 素材库支持音频文件入库和 `media_type="audio"` |
| `backend/app/agents/stages/remix_planner.py` | 解析上传/生成/内置 BGM 上下文；扩展 `audio_design`；按 BGM 和需求生成混剪方案 |
| `backend/app/prompts/system_prompts.py` | 更新 `REMIX_PLANNING_PROMPT`，要求基于 BGM 情绪、时长和能量设计节奏 |
| `backend/app/agents/executors/shared.py` | 将 `remix_plan` 转换为 `AudioSubtitleAgent` 输入；把音频 artifact 传给 RemixAssembler |
| `backend/app/agents/executors/pipeline.py` / `backend/app/agents/executors/langgraph/nodes.py` | 混剪确认后按需执行 `AudioSubtitleAgent`，再执行 `RemixAssemblerAgent` |
| `backend/app/agents/stages/remix_assembler.py` | 优先使用 `AudioSubtitleAgent` 输出的 `audio_path/subtitle_path`；优先使用 `audio_design.bgm_path`；支持 BGM+原声/旁白混音 |
| `backend/app/services/video_editing/helpers.py` | 确认 `_find_bgm()` 路径正确；可按需添加 BGM 文件 |
| `frontend/src/components/pipeline/AutoModeStudio.vue` | 自动模式素材入口支持音频；混剪请求传入 `bgm_material_id`、`bgm_mood`、`include_source_audio`、`add_voiceover` |

---

## 实现顺序

```
阶段一（可独立完成）:
1. 修改 RemixConfigRequest：新增 bgm_material_id；默认 include_source_audio=False, bgm_mood="cinematic"
2. 扩展素材库和自动模式素材选择，允许音频素材作为会话素材进入 pipeline
3. RemixPlanner 解析 BGM 上下文，并把 uploaded/generated/library/none 写入 audio_design
4. 更新 REMIX_PLANNING_PROMPT，让方案基于 BGM 和用户需求设计节奏
5. RemixAssembler 优先使用 audio_design.bgm_path，内置曲库只作为 library fallback
6. Planner / Assembler 双层强制上传或生成 BGM 的最终策略：默认 "bgm_only"，显式 include_source_audio=true 时才 "mix"

阶段二（依赖阶段一）:
7. 在混剪确认续跑中按需执行 AudioSubtitleAgent
8. RemixAssembler 使用 AudioSubtitleAgent 输出的音频/字幕进行最终合成
9. 保留 RemixAssembler 内部 TTS fallback 作为兼容路径
10. 前端 UI 增加配音/字幕选项
```

---

## 验证方法

1. 上传 2+ 个参考视频和 1 个音频素材，启动混剪 → `remix_plan.audio_design.bgm_source="uploaded"`，`bgm_material_id` 指向该素材，最终策略为 `bgm_only`，抽片不保留原视频音轨，成片使用该音频作为 BGM
2. 未上传音频，但需求明确要求"节奏感强/配乐混剪" → 当前无音乐生成能力时流程不失败，Planner 按需求设计并降级到 `library` 或 `none`
3. 未来接入音乐生成能力后，未上传音频且需求需要 BGM → 先生成 BGM，再以 `bgm_source="generated"` 进入规划和组装
4. 显式传入不存在、非音频或无权限的 `bgm_material_id` → 返回可读错误，不静默改用随机 BGM
5. 设置 `add_voiceover=true` → 成片应有 AI 旁白和字幕
6. LLM 返回 `audio_design.strategy="source_audio"` 或 `"silent"`，但已上传 BGM → 后端仍覆盖为 `bgm_only`
7. 检查 `ffmpeg` 命令日志和 `ffprobe` 输出，确认最终 mp4 的音频流来自上传/生成/内置 BGM 的预期来源

---

## 明确假设与默认策略

- BGM 上传入口采用"会话素材"方案，扩展现有素材库支持音频，而不是新增专用 BGM 上传接口。
- 当前代码库未发现音乐生成服务，因此 v1 默认按"不支持音乐生成"分支落地：有上传 BGM 就使用上传文件，没有上传 BGM 就按需求选择内置曲库或静音。
- `bgm_path` 只在后端 Agent 之间流转，不进入公开 API 响应；前端和外部调用方只感知 `bgm_material_id`、`bgm_source`、`bgm_mood`、`bgm_volume`。
- 显式用户选择优先级高于自动判断：用户传入 `bgm_material_id` 时必须使用该文件或明确失败；用户要求无音乐时不自动生成或随机选择 BGM。

---

## 一、功能完成度总览（历史评估）

| 模块 | 文件 | 完成度 |
|------|------|--------|
| LangGraph 路由（混剪分支） | `agents/executors/langgraph/executor.py` | ✅ 完整 |
| 视频预分析（VideoProfiler） | `services/video_editing/video_profiler.py` | ⚠️ 部分 |
| 混剪规划（RemixPlannerAgent） | `agents/stages/remix_planner.py` | ✅ 完整 |
| HITL 方案确认 | `executors/langgraph/nodes.py` + `routers/pipeline.py` | ✅ 完整 |
| 混剪组装（RemixAssemblerAgent） | `agents/stages/remix_assembler.py` | ✅ 完整 |
| Runtime Skill（remix_video） | `agents/skills/remix-video/runtime.py` | ✅ 完整 |
| Orchestrator Chat 路由 | `agents/stages/orchestrator_chat.py` | ✅ 完整 |
| QA 审核（QAReviewerAgent） | `agents/stages/qa_reviewer.py` | ⚠️ 基础 |
| 外部公开 API 混剪支持 | `routers/public_video_jobs.py` | ⚠️ 不确定 |
| 前端混剪确认 UI | `frontend/src/components/` | ⚠️ 框架存在，细节未验证 |

**整体结论：核心主流程已通，约 85% 完成度；边界情况处理、QA 质量、前端细节仍待打磨。**

---

## 二、已实现的核心功能

### 2.1 三条主流程路由
```
START
├─ [reference_video_ids ≥ 2] → remix_planner → (HITL) → remix_assembler → END
├─ [reference_video_id 单个] → replication_planner → ... → END
└─ [无参考视频]              → orchestrator → ... → END
```

### 2.2 混剪主流程
1. **视频预分析**：FFmpeg 镜头检测 + 关键帧提取 → VideoProfile
2. **关键帧语义分析**：批量 LLM 分析关键帧图片
3. **BGM 来源解析**：优先用户上传音频，其次生成 BGM（当前无能力时降级），最后使用内置曲库或静音
4. **混剪方案生成**：LLM 基于用户需求和 BGM 上下文输出片段编排 + 转场策略 + 音频策略
5. **HITL 确认**：pipeline 暂停 → 前端展示方案 → 用户确认 → 继续
6. **片段提取**：asyncio.gather 并行 FFmpeg 按时间段切片
7. **片段拼接**：支持 fade/dissolve 转场或无转场直连
8. **音频设计**：source_audio / bgm_only / mix / silent 策略，BGM 来源包含 uploaded / generated / library / none

---

## 三、待优化点（按优先级）

### P0 — 功能正确性风险

**1. 视频预分析"三层漏斗"不完整**
- 文件：`remix_planner.py` 第 94-100 行
- 问题：`_enrich_shots()` 在关键帧路径不存在时降级为 `_fill_default_shot_semantics()`，语义分析质量无法保证
- 优化：确保 VideoProfiler 始终输出有效的关键帧路径；添加降级告警日志

**2. 片段提取缺少并发限制**
- 文件：`remix_assembler.py` 中的 `asyncio.gather()`
- 问题：同时开启大量 FFmpeg 进程会导致 CPU/IO 竞争，大视频可能超时或 OOM
- 优化：添加 `asyncio.Semaphore(4)` 限制并发 FFmpeg 进程数

**3. 音频存在性未校验**
- 文件：`remix_assembler.py`
- 问题：源视频可能无音轨，直接提取音频会静默失败
- 优化：提取前检测音轨，无音轨时自动切换策略或提示用户

**4. BGM 素材来源未校验**
- 文件：`remix_planner.py` / `remix_assembler.py`
- 问题：显式传入的 BGM 素材如果不存在、不是音频或无权限访问，不能静默 fallback 到随机内置音乐
- 优化：Planner 阶段完成素材归属、类型和路径校验；Assembler 阶段对 `uploaded/generated` 的缺失路径直接失败

**5. 转场时长溢出无保护**
- 问题：若所有片段的 `transition_duration` 总和 > 目标总时长，拼接结果会异常
- 优化：在 RemixAssemblerAgent 入口校验总转场时长 < 总片段时长 * 0.5

### P1 — 质量与可靠性

**6. QA 审核规则过于简单**
- 文件：`agents/stages/qa_reviewer.py`
- 问题：现有检查不包含混剪特有规则（视觉连贯性、节奏感、片段相关性）
- 优化：添加混剪专属 QA 指标，如相邻片段语义相似度、音频节奏对齐度

**7. 混剪流程无自动重试**
- 问题：`retry_video_generator` 等重试路径仅适用于普通生成流程，混剪失败后直接 failed
- 优化：在 QA 失败时支持重新进入 RemixAssemblerAgent（使用原始 remix_plan）

**8. 外部 API 混剪审核接口不明**
- 文件：`routers/public_video_jobs.py`
- 问题：`/v1/video-jobs/{id}/review` 是否等价于 `confirm-remix`？混剪场景下的 HITL 流程未在公开 API 中明确支持
- 优化：扩展公开 API，增加 `waiting_remix_confirmation` 状态的处理接口

### P2 — 用户体验

**9. 前端混剪方案编辑能力不足**
- 问题：现有 UI 只支持"确认/拒绝"整个方案，不支持精细编辑（拖拽片段、调整时间点、改转场类型）
- 优化：增加时间轴可视化组件，支持关键帧缩略图预览和拖拽调整

**10. 镜头检测阈值不可配置**
- 文件：`services/video_editing/video_profiler.py`
- 问题：快节奏视频和长镜头视频的最优检测阈值差异很大，固定阈值效果差
- 优化：暴露 `scene_threshold` 参数，并在 remix_video skill 的 schema 中允许用户传入

**11. 混剪结果缺乏反馈闭环**
- 问题：用户对混剪结果的评价（满意/不满意）未被收集，无法改进模型提示词
- 优化：增加结果评分 API，把用户反馈关联到 remix_plan，为后续提示词迭代提供数据

---

## 四、验证方法

1. **端到端测试**：上传 2+ 个参考视频 + 1 个音频素材 → 发起混剪请求 → 确认方案 → 检查输出视频使用上传 BGM
2. **降级测试**：不上传音频但要求配乐 → 当前无音乐生成能力时不失败，按 `library` 或 `none` 降级
3. **单元测试**：`backend/tests/test_pipeline_runtime.py` + `test_requirement_layer.py`，补充 `bgm_material_id`、`audio_design.bgm_source`、非法音频素材用例
4. **并发压测**：同时发起 3+ 个混剪任务，观察 FFmpeg 进程数和内存占用
5. **边界测试**：无音轨视频、极短片段（<1s）、转场时长 > 片段时长、上传 BGM 路径丢失或无权限
