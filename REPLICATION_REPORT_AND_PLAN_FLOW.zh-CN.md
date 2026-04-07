# 复刻解析报告与复刻方案执行代码流程

本文档基于 `vidgen` 当前代码编写，目的是明确指出：

1. “解析报告”是在哪里生成、存储和展示的
2. “复刻方案”是在哪里生成、存储和展示的
3. 从用户发起复刻到确认继续执行，中间经过了哪些函数和状态
4. 为什么有时会看不到解析报告或复刻方案

## 1. 先区分两个概念

### 1.1 解析报告

这里指前端 assistant 消息里的“上传视频解析报告”文本内容。它偏“拆解说明”，用于告诉用户参考视频的：

- 内容概述
- 风格与节奏
- 背景信息约束
- 音频设计
- 音乐设计
- 镜头拆解

它的数据源优先级是：

1. 后端直接生成的 `analysis_report`
2. 如果后端没给 `analysis_report`，前端用 `replication_plan` 兜底拼一份文本

### 1.2 复刻方案

这里指等待确认阶段展示的“复刻方案确认”卡片内容。它偏“执行方案”，用于让用户确认：

- 内容目标
- 整体设计
- 背景信息约束
- 音频设计
- 音乐设计
- 镜头方案

它的数据源是后端输出的 `replication_plan`。

## 2. 整体链路总览

当前代码下，视频复刻已经被封装成调度 Agent 的显式 skill。

- 仅上传参考视频：不会自动进入复刻
- 上传参考视频 + 用户输入明确包含“复刻 / 同款 / 按这个视频做 / 模仿这个视频”等意图：才会调用视频复刻 skill

复刻模式的整体执行顺序如下：

1. 前端调用 `POST /api/projects/{project_id}/pipeline`
2. 后端创建 `PipelineRun`
3. 后端后台执行 `_run_pipeline(...)`
4. `PipelineExecutor.run(...)` 调用 `orchestrator.run(...)`
5. `OrchestratorAgent.execute(...)` 先做 skill 选择；只有当 `reference_video_id` 存在且用户输入命中复刻意图时，才进入 `_execute_replication(...)`
6. `_execute_replication(...)` 调模型生成 `replication_plan`，并同时生成 `analysis_report`
7. `BaseAgent.run(...)` 把这次 orchestrator 的 `output_data` 落到 `AgentExecution.output_data`
8. `PipelineExecutor._execute_pipeline(...)` 看到 `requires_confirmation=True`，把 `PipelineRun.status` 置为 `waiting_confirmation`
9. 前端通过 SSE 拉到最新 `run` 和 `agents`
10. 前端从 orchestrator 的 `output_data` 里取 `analysis_report` / `replication_plan`
11. 前端把解析报告写成 assistant 消息，把复刻方案渲染进确认卡片
12. 用户点“确认执行”后，后端走 `confirm-plan`，把 `replication_plan` 转成标准 `orchestrator_plan`，继续后续 prompt / audio / video / editor 流程

## 3. 后端入口与状态流转

### 3.1 发起 pipeline

入口在：

- `backend/app/routers/pipeline.py`
- `launch_pipeline(...)`

职责：

- 校验项目与会话
- 创建 `PipelineRun`
- 把 `reference_video_id`、脚本、平台、背景模板等输入写进 `run.input_config`
- 启动后台任务 `_run_pipeline(...)`

相关函数：

- `launch_pipeline(...)`
- `_run_pipeline(...)`

### 3.2 后台执行 pipeline

后台包装函数在：

- `backend/app/routers/pipeline.py`
- `_run_pipeline(...)`

它只做两件事：

- 调 `executor.run(...)`
- 在整条 pipeline 最终完成后尝试自动入仓

### 3.3 pipeline 执行器

核心调度器在：

- `backend/app/agents/pipeline.py`
- `PipelineExecutor.run(...)`
- `PipelineExecutor._execute_pipeline(...)`

这里的关键逻辑是：

- 第一步永远先跑 `orchestrator`
- 如果 orchestrator 返回 `requires_confirmation=True`
  - 把 `PipelineRun.status` 设置成 `waiting_confirmation`
  - `current_agent` 保持为 `orchestrator`
  - 整条 pipeline 暂停
- 等用户确认后再恢复

也就是说：

- “解析报告”和“复刻方案”都属于 orchestrator 这一步的产物
- 不是后面的 prompt_engineer 或 video_generator 产生的

## 4. Orchestrator 如何生成解析报告和复刻方案

### 4.1 进入复刻模式

入口在：

- `backend/app/agents/orchestrator.py`
- `OrchestratorAgent.execute(...)`

逻辑：

- 如果 `input_data` 里有 `reference_video_id`
- 先经过 `_select_requested_skill(...)`
- 只有 `_should_invoke_video_replication_skill(...)` 判断为真时，才会进入 `_execute_video_replication_skill(...)`
- `_execute_video_replication_skill(...)` 再调用 `_execute_replication(...)`

### 4.2 复刻主流程

核心函数：

- `backend/app/agents/orchestrator.py`
- `_execute_replication(...)`

这个函数负责：

1. 解析输入参数
2. 解析参考视频路径
3. 定义 `extract_keyframes` 工具
4. 定义 `replication_schema`
5. 组装 `user_prompt`
6. 调 `self.llm.generate_with_tools(...)`
7. 清洗模型返回的 `replication_plan`
8. 归一化镜头关键帧
9. 为镜头分配会话素材
10. 构造最终 `output`

### 4.3 复刻方案 schema

`replication_schema` 定义了模型应该返回的结构，字段包括：

- `video_summary`
- `overall_style`
- `color_palette`
- `pacing`
- `audio_design`
- `music_design`
- `shots`

这意味着：

- 复刻方案的正文内容本质上是模型生成的结构化 JSON
- 前端展示只是把这个 JSON 渲染出来

### 4.4 复刻系统提示词

复刻模式使用的 system prompt 在：

- `backend/app/prompts/system_prompts.py`
- `VIDEO_REPLICATION_SYSTEM_PROMPT`

它决定模型应该如何理解参考视频，以及如何输出：

- 营销分析
- 音频设计
- 音乐设计
- 逐镜头复刻方案

### 4.5 复刻 user prompt

复刻 user prompt 的拼装函数在：

- `backend/app/agents/orchestrator.py`
- `_build_replication_user_prompt(...)`

它把这些输入传给模型：

- 视频路径
- 平台
- 风格
- 用户脚本 / 需求描述
- 背景信息
- 调整意见

### 4.6 模型调用点

真正让模型产出复刻方案的地方在：

- `backend/app/agents/orchestrator.py`
- `_call_replication_llm(...)`
- `self.llm.generate_with_tools(...)`

这是“复刻方案内容是谁输出的”的直接答案：

- 内容生成者是 LLM
- 调用发起者是 `OrchestratorAgent._execute_replication(...)`
- 输出约束来自 `VIDEO_REPLICATION_SYSTEM_PROMPT + replication_schema + user_prompt`

## 5. 解析报告是怎么生成的

### 5.1 后端直接生成 `analysis_report`

后端在 `_execute_replication(...)` 最后构造输出时，会写入：

- `output["analysis_report"]`

生成函数是：

- `backend/app/agents/orchestrator.py`
- `_build_replication_analysis_report(...)`

它会根据：

- `replication_plan`
- `background_context`
- `extracted_frames`

拼出一段完整中文报告。

### 5.2 解析报告与复刻方案的关系

这里要注意：

- `analysis_report` 不是独立建模生成的第二份方案
- 它本质上是基于 `replication_plan + extracted_frames` 整理出来的用户可读文本

所以如果 `replication_plan` 内容很空，`analysis_report` 也会一起变空或者只剩兜底内容。

## 6. Orchestrator 输出如何落库

### 6.1 BaseAgent 负责记录执行结果

所有 agent 的结果最终都会通过：

- `backend/app/agents/base.py`
- `BaseAgent.run(...)`

进行统一收口。

### 6.2 落库位置

开始执行时：

- `_record_start(...)` 创建一条 `AgentExecution`

执行结束时：

- `_record_complete(...)` 把 `result.output_data` 写入 `AgentExecution.output_data`

因此，解析报告和复刻方案最终都存在：

- `AgentExecution.output_data`
- 对应 `agent_name == "orchestrator"`

### 6.3 进度文本

复刻过程中的灰字进度来自：

- `AgentContext.report_progress(...)`

它把文本写进：

- `AgentExecution.progress_text`

## 7. 前端如何拿到并展示解析报告

### 7.1 先从 orchestrator 的 output_data 取数

前端在：

- `frontend/src/components/pipeline/AutoModeStudio.tsx`

里先取：

- `orchestratorExecution`
- `replicationOutput`

其中：

- `replicationOutput = orchestratorExecution?.output_data`

### 7.2 解析报告消息的写入逻辑

关键 `useEffect` 在：

- `AutoModeStudio.tsx`
- 负责等待 `currentRun.status === 'waiting_confirmation'`
- 然后调用 `buildReplicationAnalysisReport(replicationOutput)`

执行逻辑：

1. 先拿关键帧图 `buildReplicationFrameImages(...)`
2. 再拿报告文本 `buildReplicationAnalysisReport(...)`
3. 如果已有“上传视频解析报告”消息，就更新
4. 否则 append 一条新的 assistant 消息

消息标题固定为：

- `上传视频解析报告`

### 7.3 前端解析报告的兜底规则

`buildReplicationAnalysisReport(...)` 的优先级是：

1. 如果 `replicationOutput.analysis_report` 是非空字符串，直接使用
2. 否则尝试用 `replicationOutput.replication_plan` 现拼
3. 如果 `replication_plan` 也不可用，则返回空字符串

这意味着：

- 后端不给 `analysis_report` 时，前端仍有可能拼出一份报告
- 但如果后端连 `replication_plan` 也基本为空，前端就拼不出来

## 8. 前端如何展示复刻方案

### 8.1 方案数据来源

前端 `replicationPlan` 的来源是：

- 找到 `agent_name === 'orchestrator' && status === 'completed'` 的执行记录
- 读取 `output_data.replication_plan`

### 8.2 方案展示位置

复刻方案现在不再以单独 assistant 消息展示，而是直接显示在：

- `复刻方案确认` 卡片

这张卡片会显示：

- 内容目标
- 整体设计
- 背景信息约束
- 音频设计
- 音乐设计
- 镜头方案

### 8.3 卡片渲染的条件

卡片外层条件是：

- `currentRun.status === 'waiting_confirmation'`
- 且 `replicationPlan` 存在

但卡片内部每块内容又是按字段单独判断的：

- `video_summary` 有值才显示“内容目标”
- `overall_style/color_palette/pacing` 有值才显示“整体设计”
- `audio_design/music_design` 有内容才显示
- `shots.length > 0` 才显示“镜头方案”

所以会出现一种情况：

- 卡片壳子出现了
- 但中间正文是空的

这通常说明：

- `replicationPlan` 对象存在
- 但里面关键字段几乎都为空

## 9. 用户确认后是如何继续执行的

确认接口在：

- `backend/app/routers/pipeline.py`
- `confirm_replication_plan(...)`

当用户点击“确认执行”时，后端会：

1. 读出最近一次已完成的 orchestrator `AgentExecution.output_data`
2. 取出 `replication_plan`
3. 把 `replication_plan.shots` 转成标准 `orchestrator_plan`
4. 把 `run.status` 从 `waiting_confirmation` 改回 `running`
5. 启动 `_continue_from_confirmation(...)`

恢复后续链路：

- `prompt_engineer`
- `audio_subtitle`
- `video_generator`
- `video_editor`

## 10. 为什么有时看不到解析报告

常见原因有 4 类。

### 10.1 后端根本没有产出 `analysis_report`

如果 orchestrator 在生成方案前就失败，`output_data` 里不会有 `analysis_report`。

### 10.2 后端虽然有输出，但 `replication_plan` 很空

因为 `analysis_report` 很大程度依赖 `replication_plan`，当方案被清洗后只剩空结构时：

- 后端生成的 `analysis_report` 可能很短
- 前端兜底也拼不出太多内容

### 10.3 前端追加解析报告消息的时机被 `waiting_confirmation` 限制

当前前端追加解析报告消息的 `useEffect` 只在以下条件下执行：

- `currentRun.status === 'waiting_confirmation'`

这意味着：

- 如果 orchestrator 在进入 `waiting_confirmation` 之前就失败
- 即使有局部输出，前端也不会把它追加成“上传视频解析报告”消息

### 10.4 历史消息恢复依赖会话持久化消息

前端展示的“上传视频解析报告”不是直接渲染 `AgentExecution.output_data`，而是：

- 先从 `replicationOutput` 生成消息内容
- 再 append / patch 到自动会话消息表

所以如果这一步没有发生，会话里就不会存在那条 assistant 消息。

## 11. 为什么有时看不到复刻方案

常见原因有 3 类。

### 11.1 pipeline 没进入 `waiting_confirmation`

如果后端没有把 `run.status` 置为 `waiting_confirmation`，确认卡片不会出现。

### 11.2 `replication_plan` 对象为空或字段几乎为空

这种情况下会出现两种表现：

- 完全没有确认卡片
- 或者有卡片壳子，但中间正文为空

### 11.3 方案字段被后端容错清洗掉了

当前后端为了避免 `AttributeError('list' object has no attribute 'get')`，会对模型返回的异常结构做清洗：

- `audio_design` 不是对象就清空
- `music_design` 不是对象就清空
- `shots` 不是合法数组就清空或跳过坏镜头

这样做能避免整条链路直接崩，但副作用是：

- 某些请求虽然进入了等待确认
- 但方案正文可能已经被清洗得非常少

## 12. 与这两个输出最相关的核心函数清单

### 12.1 后端

- `backend/app/routers/pipeline.py`
  - `launch_pipeline(...)`
  - `_run_pipeline(...)`
  - `confirm_replication_plan(...)`
  - `_continue_from_confirmation(...)`

- `backend/app/agents/pipeline.py`
  - `PipelineExecutor.run(...)`
  - `PipelineExecutor._execute_pipeline(...)`
  - `PipelineExecutor.resume_from_confirmation(...)`

- `backend/app/agents/base.py`
  - `BaseAgent.run(...)`
  - `_record_start(...)`
  - `_record_complete(...)`
  - `AgentContext.report_progress(...)`

- `backend/app/agents/orchestrator.py`
  - `OrchestratorAgent.execute(...)`
  - `_execute_replication(...)`
  - `_build_replication_user_prompt(...)`
  - `_sanitize_replication_plan(...)`
  - `_normalize_replication_shots(...)`
  - `_assign_materials_to_shots(...)`
  - `_build_replication_analysis_report(...)`

- `backend/app/prompts/system_prompts.py`
  - `VIDEO_REPLICATION_SYSTEM_PROMPT`

### 12.2 前端

- `frontend/src/components/pipeline/AutoModeStudio.tsx`
  - `replicationPlan` 取数逻辑
  - `replicationOutput` 取数逻辑
  - 解析报告 append / patch 的 `useEffect`
  - `buildReplicationAnalysisReport(...)`
  - `buildReplicationFrameImages(...)`
  - `复刻方案确认` 卡片渲染逻辑

## 13. 排查建议

如果下次又出现“没有解析报告”或“看不到复刻方案”，建议按这个顺序查：

1. 看 `PipelineRun.status` 是否进入了 `waiting_confirmation`
2. 看最近一次 `agent_name='orchestrator'` 的 `AgentExecution.output_data`
3. 检查 `output_data.analysis_report` 是否为空
4. 检查 `output_data.replication_plan` 的这些字段是否为空：
   - `video_summary`
   - `overall_style`
   - `color_palette`
   - `pacing`
   - `audio_design`
   - `music_design`
   - `shots`
5. 看 `AgentExecution.progress_text` 是否持续更新，判断是否卡在模型生成前还是模型返回后
6. 再看前端 `AutoModeStudio.tsx` 中解析报告 `useEffect` 是否被 `waiting_confirmation` 条件挡住

## 14. 一句话总结

- 解析报告：后端 orchestrator 先生成 `analysis_report`，前端再把它变成“上传视频解析报告”消息
- 复刻方案：后端 orchestrator 生成 `replication_plan`，前端把它渲染成“复刻方案确认”卡片
- 两者都来源于同一次 orchestrator 复刻执行
- 一旦 `replication_plan` 本身很空，解析报告和复刻方案通常会一起变弱，甚至在 UI 上看起来像“没出来”
