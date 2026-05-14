# 修复：最终视频拼接顺序不遵循镜头方案

## Context

当前视频生成流水线中，镜头方案（shot plan）由 Replication Planner 或 Prompt Engineer（导演）精心设计并经用户确认，但最终视频的片段拼接顺序却没有严格按照镜头方案执行。

**根因**：在 `composer.py` 的视频剪辑阶段，当存在字幕时（有 TTS 配音），会调用一个 LLM 来重新决定片段播放顺序（`ordered_indices`）。这个 LLM 的 system prompt 是"Return only the best playback order"，赋予了它自由重排片段的权力，从而覆盖了上游导演精心设计的镜头顺序。

**数据流**：
1. 导演（prompt_engineer）→ `shot_prompts`，每个 shot 有 `shot_idx` 代表时间线位置
2. 视频生成器 → 按 `shot_idx` 排序输出 `video_clips`
3. 剪辑器 → **当有字幕时调用 LLM 重排**（这里打破了镜头方案顺序）

## 修改方案

**核心思路**：移除剪辑器中的 LLM 重排逻辑，始终使用导演的 `shot_idx` 顺序（即顺序 `[0, 1, 2, ...]`）作为最终拼接顺序。

理由：
- 导演已经基于营销叙事逻辑（hook → problem → core_value → proof → result → CTA）精心排序
- 复刻路径下镜头方案经用户确认，不应被下游覆盖
- LLM 重排不可靠（现有代码已有 fallback 到顺序排列的逻辑）
- 移除后可节省一次 LLM API 调用（降低成本和延迟）

## 具体修改

### 1. `backend/app/services/video_editing/composer.py`

**删除 LLM 重排逻辑**（第 108-158 行），替换为：
```python
# Shot order from the director plan is canonical — always use it.
ordered_indices = list(range(len(video_clips)))
usage = {}
```

同时移除不再需要的 import：
- 删除 `from app.prompts import VIDEO_EDITOR_SYSTEM_PROMPT`（第 10 行）
- 删除 `_extract_ordered_indices` 从 helpers import 中（第 14 行）

### 2. `backend/app/services/video_editing/helpers.py`

**删除 `_extract_ordered_indices` 函数**（第 8-35 行）—— 已无调用方。

### 3. `backend/app/prompts/system_prompts.py`

**删除 `VIDEO_EDITOR_SYSTEM_PROMPT`**（第 65-68 行）—— 已无使用方。

### 4. `backend/app/prompts/__init__.py`

从 import 和 `__all__` 中移除 `VIDEO_EDITOR_SYSTEM_PROMPT`。

### 5. `backend/tests/test_video_pipeline_fixes.py`

更新测试 `test_video_editor_no_audio_skips_llm`：
- 测试名改为 `test_video_editor_always_uses_sequential_order`
- 移除对 LLM 不被调用的断言（因为 LLM 已完全移除）
- 改为验证片段按顺序处理

## 不需要改动的文件

- `video_editor.py`：`result.usage` 为空时 `if result.usage:` 不执行，无需改动
- `video_generator.py`：已按 `shot_idx` 排序输出，正确
- `shared.py`：`build_editor_input` 传递的数据结构不变

## 验证方式

1. 运行现有测试：`pytest backend/tests/test_video_pipeline_fixes.py -v`
2. 确认 LLM 相关 import 无残留：grep `VIDEO_EDITOR_SYSTEM_PROMPT` 和 `_extract_ordered_indices`
3. 端到端验证：创建含字幕的视频生成任务，确认最终视频片段顺序与镜头方案一致
