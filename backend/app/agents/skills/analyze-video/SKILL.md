---
name: analyze-video
description: 深度分析当前会话中的参考视频，输出包含镜头拆解、视觉风格、音频设计、营销策略等维度的结构化报告。只有当用户明确要求分析、拆解、解析、讲解或总结视频内容时才调用；生成或复刻场景不应使用此技能。
tool-name: analyze_video
runtime-entry: runtime.py
schema-file: schema.json
required-permission: analyze_video
use-when:
  - 用户明确要求分析、拆解、解析、讲解或总结参考视频内容。
  - 当前会话已有参考视频，且目标是得到文字分析报告而不是启动生产流程。
do-not-use-when:
  - 用户想直接复刻参考视频，此时应交给 replicate_video。
  - 用户只是讨论想法、润色脚本或闲聊，不需要调用视频技能。
  - 当前会话没有 reference_video_id。
required-inputs:
  - project_id
  - session_id
  - user_id
  - reference_video_id
validation-rules:
  - reference_video_id 必须存在，且归属于当前 project。
  - 返回结果必须是文字分析，不应触发生成、下载或后台 pipeline。
  - 模型返回空文本时应视为失败，不要用“视频分析完成”之类的占位文案冒充报告。
  - focus 为空时执行全面分析；不为空时优先覆盖用户指定关注点。
routing-hints:
  - 分析
  - 拆解
  - 解析
  - 讲解
  - 总结
allowed-tools: []
disable-model-invocation: false
user-invocable: false
context: direct
---

# Analyze Video

在路由阶段，只使用 frontmatter 判断是否可能命中这个 skill。

当 skill 被选中后，再读取本文件正文与 [`reference.md`](reference.md) 的存在信息，帮助参数提取器理解：

- 这是一个“只输出分析文本”的 skill
- 它不会启动生成 pipeline
- `focus` 是唯一可提取的用户输入参数

执行逻辑位于 [`runtime.py`](runtime.py)，输入 schema 位于 [`schema.json`](schema.json)。
