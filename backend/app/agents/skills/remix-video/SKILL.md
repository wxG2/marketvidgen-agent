---
name: remix-video
description: 基于当前会话已选择的多个参考视频启动混剪流程，生成混剪规划并等待用户确认。只有当用户明确表达混剪、拼接、剪一个、合成或基于多个视频生成成片时才调用。
tool-name: remix_video
runtime-entry: runtime.py
schema-file: schema.json
required-permission: remix_video
use-when:
  - 用户已选择 2 个及以上参考视频，并明确要求混剪、拼接、合成或输出成片。
  - 目标是进入多视频 remix pipeline，而不是单视频复刻或文字分析。
do-not-use-when:
  - 用户只是在讨论混剪方案、分镜方案或创意建议，不希望立即启动 pipeline。
  - 当前会话少于 2 个参考视频。
  - 用户明确要求分析参考视频，此时应使用 analyze_video。
required-inputs:
  - project_id
  - session_id
  - user_id
  - reference_video_ids
validation-rules:
  - reference_video_ids 必须包含至少 2 个当前项目可访问的视频。
  - 调用后应启动 remix pipeline，并返回异步 run_id。
  - direction 可为空；若存在，应作为混剪方向进入 input_config.script。
routing-hints:
  - 混剪
  - 拼接
  - 剪一个
  - 剪一条
  - 合成
  - 多个视频
  - remix
allowed-tools: []
disable-model-invocation: false
user-invocable: false
context: direct
---

# Remix Video

这个 skill 用来启动“多参考视频混剪”流程。

被选中后：

- `reference_video_ids` 来自当前自动模式会话，不应让模型编造
- `direction` 是可选的混剪方向或补充要求
- skill 的结果是 remix pipeline 的异步 `run_id`
- pipeline 会进入 `RemixPlannerAgent`，并暂停到 `waiting_remix_confirmation` 等待用户确认；若确认后旁白音频超过当前片段可覆盖时长，后端会自动重跑规划并再次等待确认

执行逻辑位于 [`runtime.py`](runtime.py)，输入 schema 位于 [`schema.json`](schema.json)，边界说明见 [`reference.md`](reference.md)。
