---
name: replicate-video
description: 分析当前会话中的参考视频，按照其风格、节奏和结构生成一个全新视频（复刻流程）。只有当用户明确说复刻、模仿、仿照、照着做一个、同款时才调用；纯分析请求应使用 analyze_video。
tool-name: replicate_video
runtime-entry: runtime.py
schema-file: schema.json
required-permission: replicate_video
use-when:
  - 用户明确表达复刻、模仿、仿照、照着做一个、同款等意图。
  - 当前会话已有参考视频，且目标是进入复刻流程。
do-not-use-when:
  - 用户只是想分析参考视频，此时应交给 analyze_video。
  - 当前会话没有 reference_video_id。
  - 用户并未表达启动复刻，只是在讨论创意或要求总结。
required-inputs:
  - project_id
  - session_id
  - user_id
  - reference_video_id
validation-rules:
  - reference_video_id 必须存在，且当前会话可访问。
  - 调用后应启动复刻 pipeline，而不是直接返回最终视频。
  - direction 可为空，但若存在应被拼接为额外复刻要求。
  - 复刻流程应先进入方案生成与确认链路，而不是跳过确认直接产出。
routing-hints:
  - 复刻
  - 模仿
  - 仿照
  - 照着做一个
  - 同款
  - 翻拍
allowed-tools: []
disable-model-invocation: false
user-invocable: false
context: direct
---

# Replicate Video

这个 skill 用来启动“参考视频复刻”流程，而不是输出分析文字。

被选中后，再读取本正文来提醒参数提取器：

- `direction` 是唯一需要从用户消息里提取的可选参数
- 参考视频必须来自当前会话
- skill 的结果是复刻 pipeline 的异步 `run_id`

执行逻辑位于 [`runtime.py`](runtime.py)，输入 schema 位于 [`schema.json`](schema.json)，边界说明见 [`reference.md`](reference.md)。
