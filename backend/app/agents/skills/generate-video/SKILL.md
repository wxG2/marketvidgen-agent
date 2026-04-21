---
name: generate-video
description: 根据用户提供的视频生成目标/创意要求和当前会话已选素材图片，启动完整的视频生成流水线（需求转旁白、脚本分镜、可选 TTS 配音、AI 视频合成、自动剪辑），并在后台异步执行。只有当用户明确表达生成、制作、做一个、输出视频等意图时才调用。
tool-name: generate_video
runtime-entry: runtime.py
schema-file: schema.json
required-permission: generate_video
use-when:
  - 用户明确表达生成、制作、做一个、输出视频等意图。
  - 当前会话已有选中素材，且希望进入完整的视频生产流程。
do-not-use-when:
  - 用户只是在聊创意、修改脚本、询问方案，不希望立即开跑 pipeline。
  - 当前会话没有已选素材。
  - 缺少可用生成要求或明确旁白脚本。
required-inputs:
  - project_id
  - session_id
  - user_id
  - user_request
  - image_ids
validation-rules:
  - user_request 表示用户创作目标/生成要求，不是最终旁白，不要把“根据这些素材生成方案”这类元指令当作口播脚本。
  - narration_script/script 仅在用户明确提供可直接播报的脚本文案时填写。
  - user_request 与 narration_script/script 至少有一个不能为空。
  - image_ids 必须至少包含一张当前会话可用素材。
  - 调用后应只返回 started/run_id，不同步等待完整成片结果。
  - 真实生产流程必须由后台 pipeline 执行，并通过 run_id 追踪。
routing-hints:
  - 生成视频
  - 制作视频
  - 做一个视频
  - 输出视频
  - 开始生成
  - 启动流水线
allowed-tools: []
disable-model-invocation: false
user-invocable: false
context: direct
---

# Generate Video

这个 skill 只在用户明确要“开始生产”时才应命中。

被选中后，再读取本正文来提醒参数提取器：

- `user_request` 是核心输入，用于描述本次创作目标
- `narration_script`/`script` 只用于用户明确给出的最终旁白文案
- 图片素材来自当前会话，不应让模型编造 `image_ids`
- 这个 skill 的结果是异步 pipeline `run_id`，不是最终成片

执行逻辑位于 [`runtime.py`](runtime.py)，输入 schema 位于 [`schema.json`](schema.json)，补充边界见 [`reference.md`](reference.md)。
