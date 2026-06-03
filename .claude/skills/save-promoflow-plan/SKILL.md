---
name: save-promoflow-plan
description: After the user approves a plan (typically via ExitPlanMode) for promo-flow work, persist a structured plan file under promo-flow/plan/ with mandatory 问题描述 + 方案逻辑摘要 header. Auto-triggers immediately after plan approval; also runs on explicit request like "把这个方案存到 promo-flow/plan" or "落到 plan 目录".
disable-model-invocation: false
user-invocable: true
context: direct
---

# Save Promo-Flow Plan

## Purpose

在用户**确认执行**一个方案后，自动把方案落盘到 `promo-flow/plan/` 目录，形成可追溯的需求/方案记录。落盘文件首部必须包含 **问题描述** 与 **方案逻辑摘要** 两个固定章节，保证后续回看时能在 30 秒内 grok 该 plan 在解决什么、思路是什么。

## Use When

- 刚刚通过 `ExitPlanMode` 让用户审批了一个 plan，并且用户**同意**了
- 用户在对话里说"帮我把这个方案存下来"、"落到 plan 目录"、"存到 promo-flow/plan"、"归档这个方案"
- 用户在执行完一个改动后说"补一份 plan 文档"

## Do Not Use When

- 用户**否决**了 plan（rejected ExitPlanMode）—— 不存草稿
- 用户明确说"不用存"、"不要留文档"、"先口头方案就好"
- 改动属于纯探索/咨询（用户只是问"是什么"，没有让你做事）
- 落盘内容会包含密钥、token、用户隐私数据
- 当前 plan 与 `promo-flow/` 无关（只动 `vidgen` 根 `backend/` 时不归档到这里）

## Required Inputs

执行前需在脑内（或对话上下文里）锁定以下信息——若任一缺失则先回到用户确认：

| 输入 | 说明 |
| --- | --- |
| `title` | 方案标题。简洁、能识别主题，例如 "promo-flow 混剪：消除冻帧 + 真正使用召回候选池" |
| `problem` | **问题描述**：用户报告的症状 + 根因（含可见现象与触发条件）；必须包含 file:line 引用如果是代码 bug |
| `solution_summary` | **方案逻辑摘要**：3-6 行讲清楚改动的核心思路、影响的文件/模块、为什么这样改 |
| `body` | （可选）剩余的详细 plan 内容：分 Fix、关键改动文件、验证、不在范围、设计权衡等 |

## Output Location

- **基础路径**：`/Users/weixiang/agent/vidgen/promo-flow/plan/`
- **文件命名**：`{topic}_{kind}.md`，全小写、下划线分隔。例：
  - `remix_auto_supplement_plan.md`
  - `remix_video_freeze_fix.md`
  - `remix_voiceover_review_plan.md`
- **若文件已存在**：先 Read 现有文件，判断是同一主题的延续 vs 新主题：
  - 同主题延续 → 在文件末尾追加新章节（标题如 `## N、第 X 轮迭代`），不覆盖历史
  - 新主题 → 文件名加 `_v2` / `_round3` / 更具体的限定词，避免覆盖

## Required File Skeleton

落盘文件必须严格按以下结构开头（标题号用全角）：

```markdown
# {title}

## 一、问题描述

{problem 的完整内容；包括用户报告原话、复现场景、根因分析、相关 file:line}

## 二、方案逻辑摘要

{solution_summary：3-6 行；先说做什么，再说为什么；点出影响的文件/模块}

## 三、{后续章节，按 plan 自身需要展开，常见包括：}
- 详细修复方案 / Fix 分项
- 关键改动文件清单
- 复用现有能力
- 验证步骤（含执行命令）
- 不在范围
- 设计权衡
```

前两节标题（"一、问题描述"、"二、方案逻辑摘要"）**字面不可改**，方便 grep 检索。后续章节标题号可自由编排。

## Execution Steps

1. **确认触发条件**（避免对探索/咨询场景误触发）：用户是否真的批准了 plan？plan 是否涉及 `promo-flow/`？
2. **从对话上下文抽取 `title` / `problem` / `solution_summary` / `body`**。若是从 plan mode 出来，直接复用 `/Users/youfang/.claude/plans/*.md` 里刚批的内容。
3. **决定文件名**：参考 Output Location；若不确定主题归属，用 `AskUserQuestion` 让用户从 2-3 个候选名里挑。
4. **检查路径**：`ls /Users/weixiang/agent/vidgen/promo-flow/plan/` 看是否撞名；撞名按上面规则处理。
5. **用 Write 工具落盘**：内容严格按 Required File Skeleton。
6. **给用户一行回执**：`已保存：promo-flow/plan/{filename}`，附上文件的 markdown 链接，让用户能直接点开核对。

## Gotchas

- **不要**把 plan mode 的临时文件（`/Users/youfang/.claude/plans/image-*.md`）当作正式归档——那是 plan mode 工作区，会被覆盖。本 skill 的产物是**项目内**的永久记录。
- **不要**在用户拒绝 ExitPlanMode 时自动归档；只对"approved" 状态生效。
- **不要**在 `vidgen/backend/`（vidgen 根目录的旧版混剪）相关 plan 上触发；这个目录的事情归档到别处或不归档。
- **不要**误落到 `docs/plans/`、`docs/` 等其它目录；本 skill 唯一目标路径是 `promo-flow/plan/`。
- **不要**做 `git add` / `git commit`；归档只写文件，让用户自己决定是否入仓库。

## Example

用户场景：刚通过 ExitPlanMode 批准了 "promo-flow 混剪：修复转场过快 + 末尾音频截断（第 4 轮）" 这个 plan。

skill 执行后产物：`promo-flow/plan/remix_transition_audio_fix.md`

```markdown
# promo-flow 混剪：修复转场过快 + 末尾音频截断（第 4 轮）

## 一、问题描述

上一轮（消除冻帧 + 真正使用召回候选池）已落地，输出成片不再有冻帧。但用户新反馈两个症状：
1. 各个镜头转场时间太快 —— ...
2. 音频没说完就结束了 —— ...

根因（已锁定两处）：
- `app/services/remix/assembler.py:782-783` `_build_supplement_segment` 继承父段 transition → 短 supplement 上转场占比过高
- `app/services/remix/assembler.py:403` `_concat_with_xfade` 使用 `-shortest` → 视频被音轨拖短

## 二、方案逻辑摘要

仅后端、零数据模型变更。三层修复：
1. supplement 段强制 `transition_to_next='cut'`、`transition_duration=0`，不继承父段
2. `_concat_with_xfade` 删除 `-shortest`，让 video / audio 各自跑完
3. `xfade_dur` 按 clip 时长 clamp（上限 = 段时长 / 3）
4. `_build_voiceover_timeline` 末段也保留 trailing gap，给音频尾音 120ms 空间

## 三、关键改动文件
...
```
