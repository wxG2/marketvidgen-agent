# 更新计划：复刻方案自动选图 + 方案与确认合并

> 创建时间：2026-04-02
> 状态：待实施

## Context

当前复刻模式下，orchestrator 只用参考视频的关键帧作为镜头参考图，不会从用户的素材仓库中挑选图片。复刻方案以单独的 assistant 消息展示，与确认按钮分离，用户需上下滚动对照。用户希望：
1. 复刻方案生成时，自动从会话已选素材中为每个镜头分配图片
2. 分配的图片在方案中可预览
3. 方案内容不再作为单独消息，而是内嵌到确认卡片中，与按钮一体化

---

## Part 1: 后端 — 自动分配素材到镜头

### 1.1 新增 `_get_session_materials` 方法

**文件**: `backend/app/agents/orchestrator.py`

通过 `context.pipeline_run_id` 查 `PipelineRun.session_id`，再 JOIN `AutoSessionMaterialSelection` + `Material`，按 `sort_order` 排序，只取 `media_type == "image"` 的素材。返回 `list[dict]`，包含 `material_id`, `file_path`, `filename`, `category`, `thumbnail_url`（格式 `/api/materials/{id}/thumbnail`）。

需新增 import: `AutoSessionMaterialSelection` from `app.models.auto_chat`

### 1.2 新增 `_assign_materials_to_shots` 方法

**文件**: `backend/app/agents/orchestrator.py`

接收 `shots: list[dict]`, `materials: list[dict]`。按顺序分配：shot[i] 获得 materials[i % len(materials)]。为每个 shot 添加字段：
- `material_id`: 素材 ID
- `material_image_path`: `MATERIALS_ROOT / file_path` 的完整路径（用于后续视频生成）
- `material_filename`: 文件名（前端展示用）
- `material_thumbnail_url`: `/api/materials/{id}/thumbnail`（前端展示用）

### 1.3 在 `_execute_replication` 中调用

**文件**: `backend/app/agents/orchestrator.py`（约 line 622 之后，`_normalize_replication_shots` 调用之后）

```python
session_materials = await self._get_session_materials(context)
if session_materials:
    replication_plan["shots"] = self._assign_materials_to_shots(
        replication_plan.get("shots", []), session_materials
    )
```

无素材时跳过，行为与当前一致（只用关键帧）。

### 1.4 更新确认端点使用素材路径

**文件**: `backend/app/routers/pipeline.py`（line 616）

```python
# Before:
"image_path": shot.get("reference_frame_path", ""),

# After:
"image_path": shot.get("material_image_path") or shot.get("reference_frame_path", ""),
```

有素材时用素材图，无素材时回退到关键帧。

---

## Part 2: 前端 — 移除独立方案消息 + 合并到确认卡片

### 2.1 移除"复刻执行方案"独立消息的 useEffect

**文件**: `frontend/src/components/pipeline/AutoModeStudio.tsx`

删除 lines 710-760 的 useEffect（创建 title="复刻执行方案" 的 assistant 消息）。

清理关联代码：
- 保留 `replicationPlanMessageIdRef` 和 `replicationPlanAppendingRef` 的声明（用于 hydration 兼容旧数据）
- 移除 hydration 中对 `replicationPlanMessageIdRef` 的恢复（line 263, 266）
- 各处 reset 代码（lines 812, 814, 860, 862, 948, 950）可保留无害，或一并清理

### 2.2 扩展确认卡片，内嵌方案内容

**文件**: `frontend/src/components/pipeline/AutoModeStudio.tsx`（lines 1405-1473）

将当前简单的确认卡片替换为包含完整方案的内嵌布局：

```
<div> <!-- 确认卡片容器 -->
  <header> 复刻方案确认 </header>
  
  <div className="max-h-[60vh] overflow-y-auto"> <!-- 可滚动方案区 -->
    <!-- 内容目标 (video_summary) -->
    <!-- 整体设计 (overall_style / color_palette / pacing) -->
    <!-- 背景信息约束 (background_context) -->
    <!-- 音频设计 (audio_design) -->
    <!-- 音乐设计 (music_design) -->
    <!-- 镜头方案 — 每个 shot 一张卡片 -->
    <!--   卡片内: 描述 + 运镜 + 时长 + 素材缩略图(material_thumbnail_url) 或 关键帧图 -->
  </div>
  
  <!-- 调整输入框 + 按钮组（不在滚动区内，始终可见） -->
</div>
```

镜头卡片中的图片展示：
- 优先展示 `material_thumbnail_url`（已分配素材的缩略图）
- 无素材时展示 `reference_frame_path` 关键帧（通过 `toMediaUrl` 转换）
- 每张缩略图约 64x48px，点击无需放大（与当前消息中图片规格一致）

### 2.3 更新 builder 函数

**文件**: `frontend/src/components/pipeline/AutoModeStudio.tsx`

`buildReplicationPlanMessage` 和 `buildReplicationPlanImages` 不再被调用（方案不再作为消息），可标记为 unused 或直接删除。如果保留用于其他用途，更新 `buildReplicationPlanImages` 使其优先返回 `material_thumbnail_url`。

### 2.4 Hydration 兼容

**文件**: `frontend/src/components/pipeline/AutoModeStudio.tsx`

`hydrateFromSessionDetail` 中移除对 `title === '复刻执行方案'` 消息的查找（line 263）。已持久化的旧方案消息仍会出现在消息列表中（作为普通 assistant 消息渲染），不影响功能。

---

## 关键文件清单

| 文件 | 改动 |
|------|------|
| `backend/app/agents/orchestrator.py` | 新增 `_get_session_materials`, `_assign_materials_to_shots`；在 `_execute_replication` 中调用 |
| `backend/app/routers/pipeline.py` | `confirm_replication_plan` 中 `image_path` 优先取 `material_image_path` |
| `frontend/src/components/pipeline/AutoModeStudio.tsx` | 删除方案消息 useEffect；扩展确认卡片内嵌方案；清理 builder 函数 |

## 复用的现有函数/模式

- `_resolve_images()` (`orchestrator.py:309`) — 参考其素材查询模式
- `_selection_to_response()` (`auto_sessions.py:117`) — 参考 `thumbnail_url` 格式：`/api/materials/{id}/thumbnail`
- `toMediaUrl()` (`AutoModeStudio.tsx:1767`) — 关键帧路径转 URL
- Material thumbnail API: `GET /api/materials/{id}/thumbnail` — 素材图片展示端点

## 验证方式

1. **后端单测**: 为 `_assign_materials_to_shots` 写测试，覆盖：素材数 > 镜头数、素材数 < 镜头数、无素材
2. **集成测试**: 上传参考视频 + 选择素材 → 发起复刻 → 检查 orchestrator output 中 shots 是否包含 `material_id` / `material_image_path`
3. **前端验证**: 确认卡片中应展示镜头方案（含素材缩略图），不再有独立的"复刻执行方案"消息
4. **确认后验证**: 点击确认执行 → 检查后续 prompt_engineer / video_generator 使用的是素材图片路径而非关键帧路径
5. **无素材回退**: 不选素材直接复刻 → 行为应与当前一致（用关键帧）
