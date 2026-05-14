---
title: Agent Database Development Spec v2
summary: 面向 Agent 系统的数据库设计与开发规范，基于 SQLite、SQLAlchemy 2.0、Alembic 及主流 Agent 框架官方文档整理
---

本文档基于 2026-04-09 检索的 SQLite、SQLAlchemy、Alembic、LangGraph / LangChain、AutoGen、Mem0 官方文档整理，并结合 `vidgen` 当前代码库现状，形成项目内面向 Agent 系统的数据库设计与开发规范 v2。

说明：

- 本文档优先约束 `vidgen` 当前栈：`SQLite + SQLAlchemy Async + Alembic`
- 本文档在 v1 的“关系型数据库开发规范”基础上，补齐 Agent 系统常见的数据层要求：会话状态、checkpoint、长期记忆、检索存储、多租户隔离、审计与回放
- 涉及“本项目当前现状”的描述，来自对仓库代码与现有文档的本地检查，不是来自外部文档
- 涉及“为什么这样做”的规则，优先以官方文档和主流 Agent 框架的公开设计为依据
- 本文档不要求项目立即引入向量数据库或图数据库，但要求数据库设计为后续接入留出清晰边界

## 当前栈结论

根据仓库当前实现，可推断出：

- 默认数据库是 SQLite：`DATABASE_URL=sqlite+aiosqlite:///./data/vidgen.db`
- ORM 使用 SQLAlchemy 2.x 异步模式
- 迁移工具已使用 Alembic
- SQLite 连接初始化时已启用：
  - `PRAGMA foreign_keys=ON`
  - `PRAGMA busy_timeout=30000`
  - `PRAGMA synchronous=NORMAL`
  - `PRAGMA journal_mode=WAL`

这意味着本项目当前数据库规范必须优先兼容 SQLite 的约束、DDL 能力、索引行为和迁移限制。

同时，面向 Agent 系统的数据层不能只覆盖“业务主数据表”，还必须覆盖以下几类数据：

- 事务型关系数据：项目、任务、资产、配置、权限等
- 会话型状态数据：thread、run、step、checkpoint、tool call 等
- 记忆型数据：conversation / session / user / organization 级别记忆
- 检索型数据：向量索引、图谱节点边、可搜索文档摘要
- 审计型数据：prompt 版本、模型输入输出摘要、人工审批记录、错误快照、回放事件

因此，v2 的目标不是把所有 Agent 数据都塞进 SQLite 一种结构里，而是为当前栈建立一套“关系层优先、状态层明确、记忆层分级、检索层可插拔、审计层可回放”的数据库规范。

## 目标

数据库设计和开发必须同时满足以下目标：

- 数据语义清晰
- 结构可扩展
- 约束可落地
- 查询可预期
- 迁移可回放
- 与 SQLite 当前能力兼容
- 尽量为未来迁移到 PostgreSQL 保持可移植性
- 支持 Agent 会话状态持久化与恢复执行
- 支持短期记忆与长期记忆分层
- 支持多租户 / 多用户 / 多 Agent 隔离
- 支持可审计、可回放、可追踪
- 为未来接入向量检索 / 图谱检索保留边界

## 一、总体分层原则

### 1. 数据层必须按职责分层，而不是只按技术栈分层

面向 Agent 系统时，数据库设计至少分为以下四层：

1. 事务层：保存业务实体和强一致关系
2. 状态层：保存线程、运行过程、checkpoint、工具执行状态
3. 记忆层：保存可复用、可检索、跨会话的记忆条目
4. 审计层：保存事件、版本、审批、错误与回放记录

如果未来引入向量数据库或图数据库，则额外形成：

5. 检索层：保存向量索引、图谱实体关系或检索优化数据

规则：

- 不允许用一个“大而全”的表同时承担事务、状态、记忆和审计四种职责
- 即使底层暂时都存在 SQLite 中，也必须在建模和命名上保留职责边界
- 事务层的数据模型是系统事实来源；状态层、记忆层、审计层围绕事实来源展开，不反向污染业务主模型

### 2. 优先关系型事实，检索索引是派生物

在 Agent 系统中，向量索引、摘要、缓存、搜索快照通常是派生数据，不应取代主事实表。

规则：

- 用户、项目、线程、运行、工具调用、审批记录等主实体，必须有关系型事实表
- Embedding、检索摘要、rerank 中间结果、缓存命中等默认视为派生数据
- 派生数据必须能够通过主事实重新生成，除非有明确的历史保留需求
- 派生数据的删除策略、TTL 和重建策略必须在文档中明确

### 3. 先定义数据生命周期，再定义表结构

设计新表前，必须先回答：

1. 这类数据属于事务、状态、记忆、审计中的哪一层
2. 这类数据的保留期限是永久、阶段性、还是短期缓存
3. 这类数据是否需要回放、审计或恢复执行
4. 这类数据是否需要全文 / 语义 / 图谱检索
5. 这类数据是否可以重建

不能回答上述问题时，不应直接建表。

## 二、建模原则

### 1. 单表只表达一个清晰实体

每张表应只承载一个稳定业务实体或一种明确关系，不要把多个生命周期不同、查询模式不同的数据硬塞进一张“大表”。

推荐：

- `projects`
- `agent_threads`
- `agent_runs`
- `agent_checkpoints`
- `agent_memories`
- `tool_calls`
- `run_events`

避免：

- 用一个 `misc_data` / `system_data` / `records` 表混装多种结构
- 用一个 `agent_logs` 表同时保存消息、工具调用、错误和审计事件
- 用大量 nullable 字段模拟多种实体

### 2. 优先规范化，谨慎反规范化

默认优先做规范化设计：

- 可枚举的一对多关系拆表
- 多对多关系显式中间表
- 重复出现的实体字段不要跨表复制
- 工具调用、模型调用、审批记录、checkpoint 不要混入主线程表中

只有在以下条件同时满足时才允许反规范化：

- 查询热点明确
- 读性能收益显著
- 一致性维护成本可接受
- 有明确刷新或回写策略
- 能说明反规范化字段的“真值来源”仍然是哪张表

### 3. 先定义关系，再定义字段

设计新表时，先回答：

1. 这张表的主实体是谁
2. 它依赖哪些父表
3. 删除父表时应级联删除、置空，还是禁止删除
4. 业务上哪些字段必须唯一
5. 主要查询路径是什么
6. 是否参与恢复执行、回放或审计
7. 是否需要向量检索或内容过滤

字段只是关系设计的结果，不是起点。

### 4. 区分“消息”、“状态”、“记忆”三个概念

在 Agent 系统中，这三类数据不能混为一谈：

- 消息：用户与 Agent 的输入输出内容，是对话历史的一部分
- 状态：运行到哪一步、当前节点、工具是否执行成功、是否可恢复
- 记忆：跨消息、跨线程、跨会话可复用的稳定信息

规则：

- `agent_messages` 不等于 `agent_memories`
- `agent_messages` 不等于 `agent_checkpoints`
- 不允许仅通过消息表承担整个运行恢复逻辑
- 不允许仅通过记忆表承担短期会话状态

## 三、命名规范

### 1. 表命名

- 使用小写蛇形命名
- 表名使用复数
- 避免缩写，除非是团队统一术语

推荐：

- `agent_threads`
- `agent_runs`
- `agent_steps`
- `agent_checkpoints`
- `tool_calls`
- `prompt_versions`

避免：

- `AgentRun`
- `threadState`
- `tbl_agent_log`

### 2. 列命名

- 使用小写蛇形命名
- 外键字段统一命名为 `<entity>_id`
- 时间字段统一命名为 `created_at`、`updated_at`、`started_at`、`completed_at`、`expires_at`
- JSON 文本字段统一以 `_json` 结尾
- 状态字段统一使用 `status` 或带语义前缀的 `*_status`
- scope 字段统一使用 `scope`
- namespace 文本字段统一使用 `namespace` 或 `namespace_key`
- 用于恢复执行的游标统一使用 `resume_token`、`checkpoint_key`、`cursor`

推荐：

- `thread_id`
- `run_id`
- `checkpoint_key`
- `tool_call_id`
- `memory_scope`
- `metadata_json`
- `last_activity_at`

### 3. 约束与索引命名

所有显式约束和索引都应命名，避免依赖 SQLite 的隐式或匿名命名。

推荐格式：

- 主键：`pk_<table>`
- 唯一约束：`uq_<table>_<col>`
- 复合唯一约束：`uq_<table>_<col1>_<col2>`
- 外键：`fk_<table>_<col>_<referred_table>`
- 索引：`ix_<table>_<col>`
- 复合索引：`ix_<table>_<col1>_<col2>`
- 检查约束：`ck_<table>_<rule>`

示例：

- `uq_agent_memories_namespace_memory_key`
- `ix_agent_runs_thread_id_status`
- `ix_agent_checkpoints_thread_id_created_at`

项目建议：

- 后续应为 `Base.metadata` 引入统一 `naming_convention`，减少 Alembic 在 SQLite 下处理匿名约束的困难

## 四、主键与外键规范

### 1. 主键

当前项目统一使用字符串 UUID 主键，这在现有代码中已经形成事实标准，应保持一致，除非有充分理由重构。

规则：

- 默认使用 `String` 主键承载 UUID
- 主键列统一命名为 `id`
- 不要混用自增整数主键和 UUID 主键，除非是明确隔离的子系统
- 对外暴露的 Agent 对象 ID（thread、run、memory、tool_call）应可在日志、事件和 API 中稳定引用

### 2. 外键

根据 SQLite 官方文档，外键必须在每个连接上显式启用，且父键必须是主键或唯一键；子键列虽然不是强制索引，但通常应建立索引以避免线性扫描。

规则：

- 所有逻辑存在依赖关系的数据都应优先使用外键，而不是只靠代码约束
- 外键列默认建立索引
- 删除策略必须显式声明：`CASCADE`、`SET NULL`、`RESTRICT`
- 引用的父字段必须是主键或唯一约束覆盖的列

当前项目建议：

- 像 `project_id`、`user_id`、`thread_id`、`run_id`、`session_id` 这类高频关联键，必须持续保持索引
- 复合外键只在确有业务必要时使用，并确保父表有完全匹配的主键或唯一约束

### 3. 级联删除

使用 `ON DELETE CASCADE` 只适用于“子数据绝无独立价值”的情况，例如：

- `agent_messages -> agent_threads`
- `agent_steps -> agent_runs`
- `tool_calls -> agent_runs`

不适合级联删除的场景：

- 需要保留审计记录
- 子记录可能被其他流程引用
- 删除父实体不等于业务上删除历史
- 需要保留失败快照、审批记录、模型调用记录

规则：

- 只要数据涉及审计、合规、回放或错误分析，优先 `RESTRICT` 或逻辑删除，而不是级联物理删除
- checkpoint、审批事件、错误事件默认不跟随线程删除自动清空，除非有明确 retention 策略

## 五、字段类型规范

### 1. SQLite 类型意识

SQLite 官方文档指出，SQLite 使用动态类型系统，非 STRICT 表的列更多体现 affinity，而不是强类型约束。因此：

- 不要以为声明了 `String` / `Integer` 就自动获得严格数据库级类型保护
- 应同时依赖：
  - ORM 类型
  - 应用层校验
  - 必要时的 CHECK 约束

### 2. 字符串

适用：

- 标识符
- 文件路径
- 状态值
- 平台名、类型名、标签名
- checkpoint key、namespace、step type、tool name

规则：

- 有固定枚举范围的字符串，优先增加 CHECK 约束或应用层枚举校验
- 长文本内容用 `Text`，不要滥用 `String`
- `scope`、`status`、`event_type`、`role` 这类字段，如集合稳定，应优先受约束

### 3. 数值

规则：

- 计数、排序、毫秒时长、重试次数、token 数、步骤序号用 `Integer`
- 分数、比率、相似度、成本估算等允许小数的值用 `Float` / `REAL`
- 金额类如果未来出现，优先定点表示，不建议直接用浮点

### 4. 布尔

SQLite 没有真正独立的 Boolean 存储类，布尔值本质上是整数 `0/1`。

规则：

- Python / SQLAlchemy 层可继续使用 `Boolean`
- 对关键布尔字段，如需强约束，可补充 CHECK 约束
- 像 `is_terminal`、`is_retryable`、`is_archived`、`is_deleted`、`is_latest` 这类语义明确字段，不要用模糊命名替代

### 5. 日期时间

SQLite 没有原生 datetime 存储类型；当前项目通过 SQLAlchemy `DateTime` 与 Python `timezone.utc` 生成 UTC 时间。

规则：

- 所有时间一律使用 UTC
- 代码中统一使用 `datetime.now(timezone.utc)`
- 字段命名统一使用 `*_at`
- 不在数据库中混存本地时区时间
- 运行过程相关时间点应细分为：`queued_at`、`started_at`、`completed_at`、`failed_at`、`expires_at`

### 6. JSON 数据

当前项目里多个字段使用 `Text` 保存 JSON，例如 `value_json`、`metadata_json`、`payload_json`。

规则：

- 只有在结构不稳定、查询维度不强、短期不值得拆表时，才允许使用 JSON 文本字段
- 字段名必须以 `_json` 结尾
- 写入前必须做 JSON 序列化与 schema 级校验
- 如果某个 JSON 字段被频繁按内部属性查询，应考虑拆列或拆表
- 运行快照、模型原始返回、工具原始响应允许使用 JSON，但必须补充可查询的关键结构化列

示例：

- `tool_calls` 中允许保留 `arguments_json`、`result_json`
- `agent_checkpoints` 中允许保留 `state_json`
- 但同时应有 `status`、`step_index`、`thread_id`、`run_id` 等结构化列可检索

### 7. 向量与检索字段

当前项目栈不建议直接把高维 embedding 塞进 SQLite 文本列作为长期方案。

规则：

- 若仅做实验性原型，可在关系库中临时保存 embedding 元数据，但不应作为长期生产方案
- 关系库中应优先保存：
  - `embedding_provider`
  - `embedding_model`
  - `embedding_dimensions`
  - `embedding_version`
  - `indexed_at`
  - `vector_document_id`
- 真正的向量内容默认应放在专用向量存储或支持向量扩展的数据库中
- 若未来迁移到 PostgreSQL + pgvector，也应把“向量字段”和“关系事实”分层建模，而不是把所有检索语义都绑在单表上

## 六、约束规范

### 1. 非空约束

默认原则：

- 能确定业务上必须存在的字段，一律 `nullable=False`
- 不要为了“以后可能有用”把核心字段全部放成 nullable

必须认真判断 nullable 的字段：

- 外键字段
- 状态字段
- 标题 / 名称字段
- 核心输入输出字段
- namespace / scope 字段
- 恢复执行相关键值

### 2. 唯一约束

唯一性应由数据库保证，而不是只由代码判断。

适用场景：

- 用户范围内唯一键：如 `agent_memories(namespace, memory_key)`
- 线程内唯一步骤：如 `agent_steps(run_id, step_index)`
- 运行内唯一工具调用：如 `tool_calls(run_id, call_key)`
- Prompt 版本号唯一：如 `prompt_versions(prompt_name, version)`

规则：

- 复合唯一约束必须显式命名
- 对“软唯一”需求，可考虑唯一部分索引

### 3. CHECK 约束

对于稳定状态机或简单范围约束，优先增加 CHECK。

适用场景：

- `status IN (...)`
- `scope IN ('conversation', 'session', 'user', 'organization')`
- `step_index >= 0`
- `retry_count >= 0`
- `duration_ms >= 0`
- 布尔映射值只允许 `0/1`

特别说明：

- SQLite 支持 CHECK，但对复杂业务规则仍应以应用层校验为主
- 只要状态集合已经稳定，就不应完全依赖代码侧常量

## 七、索引规范

### 1. 只为查询路径建立索引

索引不是越多越好。每个索引都会增加写入成本和迁移复杂度。

必须建索引的列：

- 外键列
- 高频过滤列
- 高频排序列
- 高频去重列
- 联合查询的前导列
- checkpoint / run / memory / event 的高频时间列

### 2. 联合索引优先按查询条件顺序设计

联合索引应围绕真实查询设计，而不是“觉得可能有用”。

经验规则：

- 等值过滤列放前
- 高频排序列放后
- 不要创建被更强联合索引完全覆盖、但没有额外价值的冗余索引

常见示例：

- `ix_agent_runs_thread_id_created_at`
- `ix_agent_steps_run_id_step_index`
- `ix_agent_memories_namespace_scope_updated_at`
- `ix_run_events_run_id_created_at`

### 3. 善用部分索引

SQLite 官方文档支持 partial index；当某字段大量为 NULL，或某个状态只覆盖少数行时，部分索引往往比全量索引更合适。

适用场景：

- `current_run_id IS NOT NULL`
- `status = 'pending'`
- `deleted_at IS NULL`
- `expires_at IS NULL`
- `is_latest = 1`

### 4. 子键索引是默认要求

SQLite 外键文档明确指出：子键列虽然不是强制索引，但在删除或更新父表记录时，如果子键没有索引，数据库可能需要线性扫描整个子表。

因此项目规则为：

- 所有高频外键默认建索引
- 复合外键按完整组合建联合索引

### 5. 检索索引与事实索引分开评估

规则：

- 面向事务查询的索引，由关系模型查询路径决定
- 面向搜索召回的索引，由检索路径决定
- 不能因为向量或全文检索需求，就在事务表上随意堆叠不必要索引
- 若某搜索需求已明显超出 SQLite 或普通 B-tree 索引能力，应优先评估专用检索层，而不是继续叠加关系库技巧

## 八、Agent 状态与执行持久化规范

### 1. 线程、运行、步骤、checkpoint 必须分离建模

面向 Agent 系统，至少应区分以下实体：

- `agent_threads`：一条对话线程或任务线程
- `agent_runs`：一次完整的执行实例
- `agent_steps`：执行过程中的步骤或节点
- `agent_checkpoints`：可恢复的状态快照
- `tool_calls`：工具调用记录
- `model_calls`：模型调用记录（可选，但推荐）

推荐关系：

- `agent_threads 1:N agent_runs`
- `agent_runs 1:N agent_steps`
- `agent_runs 1:N agent_checkpoints`
- `agent_runs 1:N tool_calls`
- `agent_runs 1:N model_calls`

规则：

- 不允许只靠 `agent_messages` 恢复运行状态
- 不允许只存“最后状态”，导致中间步骤无法追踪
- 至少要能回答：某次 run 运行到哪一步、失败在哪、是否可恢复、最后一次稳定 checkpoint 是什么

### 2. checkpoint 是一等公民，不是日志附属物

checkpoint 的用途包括：

- 失败恢复
- 人工审批后继续执行
- 长任务中断后续跑
- 状态回放与调试

规则：

- checkpoint 必须有稳定主键和业务唯一键，如 `checkpoint_key`
- 必须关联 `thread_id`、`run_id`
- 必须记录 `step_index` 或 `node_name`
- 必须记录 `state_json`
- 必须记录 `created_at`
- 必须记录“是否为可恢复点”或可通过 `status` 推导

推荐字段：

- `id`
- `thread_id`
- `run_id`
- `step_index`
- `node_name`
- `checkpoint_key`
- `resume_token`
- `state_json`
- `metadata_json`
- `status`
- `created_at`
- `expires_at`

### 3. Agent team 状态必须支持整体保存与恢复

对于多 Agent 协作系统，仅保存某一个 agent 的消息历史是不够的。

规则：

- 如果存在 team / group chat / coordinator 模式，必须有 team 级状态对象或可组合恢复的状态结构
- team 状态至少要能恢复：参与者、当前发言轮次、共享消息线程、共享 memory 视图、终止条件状态
- 如果实现上暂时不单独建表，也必须保证从 `agent_runs`、`agent_steps`、`agent_messages` 中可恢复 team 级最小状态

推荐实体：

- `agent_teams`
- `agent_team_members`
- `agent_team_states`

当前阶段如果不实现独立表，至少在文档和代码中保留此扩展位。

### 4. 工具调用与模型调用要可独立审计

规则：

- 工具调用记录不应只存在于大块日志文本里
- 每次工具调用至少应保存：
  - `run_id`
  - `step_id` 或 `step_index`
  - `tool_name`
  - `arguments_json`
  - `status`
  - `started_at`
  - `completed_at`
  - `error_message`
  - `result_json` 或 `result_preview`
- 模型调用若涉及成本、质量分析或审计，应单独保存：
  - `model_name`
  - `prompt_version_id`
  - `input_tokens`
  - `output_tokens`
  - `latency_ms`
  - `finish_reason`

## 九、记忆系统规范

### 1. 明确区分四类记忆 scope

记忆系统至少应区分以下 scope：

- `conversation`：单次 turn 级别的即时上下文
- `session`：当前线程 / 当前任务周期内的短期记忆
- `user`：跨线程、跨会话的长期个体记忆
- `organization`：多个用户、多个 agent 可共享的组织级记忆

规则：

- 任何记忆记录都必须有 `scope`
- 不能把所有记忆都默认塞进 `user` 级
- 不同 scope 的 retention、读写权限、检索策略必须不同

### 2. 记忆写入必须显式定义“提炼策略”

不是所有消息都应该进入长期记忆。

规则：

- 会话消息默认不自动等于长期记忆
- 进入 `user` 或 `organization` 级记忆前，必须满足至少一个条件：
  - 明确的用户偏好
  - 稳定事实
  - 可复用工作上下文
  - 长期业务规则
- 短期摘要、一次性失败信息、临时生成结果默认不写入长期记忆，除非业务明确要求

推荐字段：

- `memory_key`
- `scope`
- `namespace`
- `content`
- `summary`
- `source_type`
- `source_thread_id`
- `source_run_id`
- `importance`
- `metadata_json`
- `created_at`
- `updated_at`
- `expires_at`
- `archived_at`

### 3. 记忆必须有 namespace 设计

主流 Agent 框架通常使用 namespace / key 组织长期记忆，以支持分用户、分组织、分应用场景隔离。

规则：

- 长期记忆默认必须包含 namespace
- namespace 设计至少应覆盖下列维度中的若干项：
  - `organization_id`
  - `user_id`
  - `assistant_id` / `agent_id`
  - `application_context`
- namespace 可以存为结构化多列，也可以存为标准化字符串，但必须保证可过滤和可索引

推荐：

- 若当前仍以 SQLite 为主，优先采用“多列 + 规范化生成 namespace_key”的方式
- 不建议只存一个未经约束的自由文本 `namespace`

### 4. 记忆不是永久不变，必须支持更新、归档与失效

规则：

- 记忆表必须支持 `updated_at`
- 需要失效的记忆必须支持 `expires_at` 或 `is_active`
- 被逻辑淘汰但仍需审计的记忆，应支持 `archived_at`
- 不允许仅靠物理删除来表达“这条记忆现在不该再被检索”

### 5. 共享记忆与私有记忆必须明确隔离

规则：

- `organization` 级记忆默认不得与 `user` 级私有记忆混查，除非查询层显式声明
- 若有多个 agent 共享同一组织上下文，必须明确哪些记忆是 team-visible，哪些是 agent-private
- 共享记忆的写入者、来源和最后更新时间应可追踪

## 十、检索层与向量 / 图谱存储规范

### 1. 当前项目默认采用“关系事实 + 可插拔检索层”架构

考虑到当前栈为 SQLite，项目现阶段应采用以下原则：

- 关系数据库保存事实数据、状态数据、记忆元数据和审计记录
- 向量检索、图谱检索作为可插拔能力，不作为当前 SQLite 表设计的前置硬依赖
- 如果暂未接入向量数据库，也应预留检索文档 ID、embedding 版本、索引时间等元数据字段

### 2. 检索文档必须有与事实表的映射关系

规则：

- 一个可被检索的文档、摘要、记忆条目，必须能追溯到对应事实来源
- 任何向量记录都必须能回答：它来自哪条 memory / message / document / asset
- 不允许存在无法追溯来源的“孤立 embedding”

推荐实体：

- `retrieval_documents`
- `retrieval_document_chunks`
- `retrieval_indexes`

推荐映射字段：

- `source_table`
- `source_id`
- `content_hash`
- `embedding_version`
- `indexed_at`

### 3. 图谱实体关系应作为独立建模方向，而不是 JSON 拼装

如果未来需要 Graph Memory、实体关系追踪或跨文档事实链接：

- 应优先考虑专门的实体 / 边模型，或专用图存储
- 不建议长期使用单个 JSON 字段堆叠实体关系图
- 若短期使用关系表表达图结构，可采用：
  - `memory_entities`
  - `memory_relations`

### 4. 检索层必须支持重建

规则：

- 向量索引、图谱索引、摘要缓存默认都应可由主事实重建
- 每次 embedding 模型升级或 chunk 策略变化时，应通过版本字段区分
- 不允许在缺少版本字段的情况下覆盖旧索引，导致回放与 A/B 对比失真

## 十一、多租户与隔离规范

### 1. 先定义隔离边界，再建表

面向 Agent 系统，至少要明确以下隔离维度：

- 用户隔离
- 组织 / 工作区隔离
- Agent / assistant 隔离
- 线程隔离
- 环境隔离（dev / staging / prod）

规则：

- 每张会跨用户数据的表，都必须说明隔离主键是什么
- 不能默认“上层代码会传对 user_id”来代替建模规范
- 不允许让 organization 级与 user 级数据在无约束条件下混放且无法过滤

### 2. 隔离字段必须参与索引设计

规则：

- `organization_id`、`workspace_id`、`user_id`、`assistant_id` 等隔离键，应参与高频查询索引设计
- 高并发或高频读取场景下，应优先用“隔离键 + 时间/状态”的联合索引

### 3. 不同 scope 的读写权限必须有数据层表达

如果系统存在共享记忆、审批记录、内部工具结果等敏感数据：

- 应至少用 scope、visibility、owner_type、owner_id 等字段表达读写边界
- 权限判断虽然主要在应用层，但数据库模型必须保留可审计的边界字段

## 十二、生命周期、归档与 TTL 规范

### 1. 所有 Agent 数据都必须定义 retention 策略

至少应明确以下对象的保留期：

- 消息历史
- run / step / checkpoint
- 工具调用结果
- 错误快照
- 用户长期记忆
- 检索索引元数据
- 审计事件

规则：

- 未定义 retention 的表，不应直接进入长期生产使用
- retention 策略必须说明：永久保留、到期归档、到期删除、可手动清理中的哪一种

### 2. 优先逻辑过期，而不是直接物理删除

规则：

- 需要审计、分析或可能恢复的数据，优先通过 `expires_at`、`archived_at`、`deleted_at` 表达生命周期
- 物理删除适用于缓存型、可重建、无审计价值的数据
- 到期后是否仍参与检索，需要有明确规则

### 3. 过期数据清理必须有计划任务或显式运维流程

规则：

- 不能只在文档中写 TTL，而没有执行机制
- 清理任务应是幂等的
- 对清理过的检索数据，要么同步清理外部索引，要么标记为待重建

## 十三、观测、审计与回放规范

### 1. 事件表是 Agent 系统的必需品，不是可选增强

推荐实体：

- `run_events`
- `approval_events`
- `error_events`
- `prompt_versions`
- `model_calls`

规则：

- 至少要能回答：
  - 某次 run 为什么结束
  - 某次工具调用为什么失败
  - 某条回复使用了哪个 prompt 版本
  - 某次人工审批发生在什么时候，由谁批准
- 这些信息不能只存在应用日志中而不入库

### 2. Prompt 版本必须可追踪

规则：

- Prompt 模板或系统指令一旦参与生产执行，就应可版本化
- `model_calls` 或 `agent_runs` 应能追溯到 `prompt_version_id`
- 若 prompt 由多个片段拼装，至少要记录核心系统提示版本和关键工具策略版本

### 3. 回放必须有最小可行数据集

最小回放能力至少应覆盖：

- 输入消息
- 线程上下文
- 运行步骤序列
- 关键 checkpoint
- 工具调用参数与结果摘要
- 模型调用基础元数据
- 最终输出

规则：

- 无法支持最小回放的数据结构，不应宣称“支持审计”和“支持可恢复执行”

## 十四、SQLite 专项规范

### 1. 外键必须显式开启

SQLite 外键不是默认总开启。当前项目已经在连接事件中执行 `PRAGMA foreign_keys=ON`，这是必须保留的行为。

规则：

- 不允许移除外键启用逻辑
- 测试环境、脚本环境也必须保持一致

### 2. WAL 模式与并发

SQLite 官方文档说明 WAL 能提升并发读写体验，但仍不是高并发服务端数据库。

规则：

- 当前项目可继续使用 `journal_mode=WAL`
- 避免长事务
- 避免在一个请求里持有数据库事务同时执行很慢的外部网络调用
- 批量写入和大迁移需要关注 WAL 文件增长与 checkpoint 行为
- 长流程 Agent 应优先采用“短事务 + checkpoint 持久化”的方式，而不是用单个长事务包住整个运行

### 3. STRICT 表策略

SQLite 支持 STRICT 表，可提供更接近传统数据库的类型约束。

项目建议：

- 新增核心表时，可评估是否逐步引入 STRICT TABLE
- 但在全面启用前，必须先验证 Alembic、测试环境和现有 SQLAlchemy DDL 行为是否完全兼容

当前结论：

- 这是值得评估的增强方向
- 不是当前必须立即统一切换的规则

### 4. ALTER TABLE 限制

SQLite 官方文档明确指出：SQLite 只原生支持有限的 ALTER TABLE 能力。复杂变更往往需要“建新表 -> 拷贝数据 -> 替换旧表”的迁移路径。

项目规则：

- 除简单 `add_column` 外，不要假设 SQLite 能直接完成复杂结构修改
- 涉及改列类型、删列、改约束、改主键、改唯一约束时，优先使用 Alembic batch migration
- 不要依赖手工修改 `sqlite_schema`

### 5. SQLite 在 Agent 系统中的定位

规则：

- SQLite 适合作为当前项目的单机事实库、开发库、轻量部署库
- 若未来出现以下条件，应主动评估 PostgreSQL：
  - 多实例并发写入明显增加
  - 需要更强的迁移能力和运维能力
  - 需要 DB-backed long-term store / checkpointer
  - 需要更稳定地接入 pgvector 或更复杂的检索能力

## 十五、SQLAlchemy / Alembic 规范

### 1. 模型即单一事实来源

ORM 模型应是业务表结构的单一事实来源，迁移脚本由模型演进产生，而不是长期靠手写 SQL 和运行时补丁维持。

建议：

- 新表、新字段、新索引、新约束必须先改模型，再生成迁移
- 运行时 `create_all()` 只适用于开发自举，不应替代正式迁移流程

### 2. SQLite 迁移优先 batch mode

Alembic 官方明确为 SQLite 提供了 batch migration，用于处理 SQLite 缺乏完整 ALTER TABLE 支持的问题。

规则：

- 表结构调整优先使用 `op.batch_alter_table(...)`
- 对 SQLite 复杂迁移，不要写“假设数据库支持 ALTER”的脚本

### 3. 命名约束

Alembic 官方强调命名约束的重要性，尤其在 SQLite 和 batch migration 中更明显。

规则：

- 新增 `UniqueConstraint`、`ForeignKeyConstraint`、`CheckConstraint` 时显式命名
- 后续建议引入统一 `MetaData.naming_convention`

### 4. 避免运行时补 schema 成为长期机制

当前项目在 `init_db()` 里存在 `_migrate_legacy_schema()` 逻辑，用于补历史字段。

这可以作为兼容过渡，但不应持续扩张。

规则：

- 历史兼容补丁只能短期存在
- 稳定后应迁移为正式 Alembic revision
- 不允许把“应用启动时偷偷修表”作为常规 schema 演进方式

### 5. 数据迁移要区分事实迁移与索引迁移

规则：

- 事实表迁移失败必须可回滚
- 检索索引、摘要缓存、embedding 等派生数据迁移，可以允许“删除后重建”策略，但必须在变更说明中写清楚
- 升级 embedding 模型、chunk 策略、memory 提炼策略时，应明确是否需要全量重建检索层

## 十六、推荐的核心实体草图

当前项目不要求一次性全部实现，但数据库设计应围绕以下核心实体演进：

### 1. 事务层

- `projects`
- `users`
- `repository_assets`
- `pipeline_runs`

### 2. 状态层

- `agent_threads`
- `agent_messages`
- `agent_runs`
- `agent_steps`
- `agent_checkpoints`
- `tool_calls`
- `model_calls`

### 3. 记忆层

- `agent_memories`
- `memory_sources`
- `memory_entities`（可选）
- `memory_relations`（可选）

### 4. 审计层

- `run_events`
- `approval_events`
- `error_events`
- `prompt_versions`

### 5. 检索层（未来可插拔）

- `retrieval_documents`
- `retrieval_document_chunks`
- `retrieval_indexes`

## 十七、项目内具体建议

基于当前模型、迁移文件和 Agent 系统通用实践，建议新增或后续逐步落实：

1. 将现有数据库规范拆分为“事务层 + 状态层 + 记忆层 + 审计层”的明确结构。
2. 新增 `agent_threads`、`agent_runs`、`agent_steps`、`agent_checkpoints` 四类核心状态实体，不再仅依赖 message 表表达运行过程。
3. 为 `agent_memories` 增加 `scope`、`namespace`、`expires_at`、`archived_at` 等字段，建立正式的长期记忆规范。
4. 为所有新建复合约束显式命名，不再依赖匿名约束。
5. 对状态字段逐步补充 CHECK 约束，例如 `queued/running/completed/failed/cancelled` 这类稳定状态集合。
6. 对高频 JSON 字段建立“是否需要拆表”的定期审查机制。
7. 对所有外键列复查索引覆盖情况，保持“子键默认有索引”的规则。
8. 将 `_migrate_legacy_schema()` 中的长期逻辑逐步收敛回 Alembic。
9. 建立 `run_events`、`tool_calls`、`model_calls` 的最小审计链路。
10. 为未来接入向量检索预留 `retrieval_documents` 与 embedding 元数据，而不是直接把向量内容塞进 SQLite 文本列。
11. 评估是否为核心表启用 STRICT TABLE 试点。
12. 当 Agent 并发、恢复执行和长期记忆需求明显上升时，优先评估 PostgreSQL 作为下一阶段事实库或持久化 store。

## 十八、数据库变更流程

每次数据库变更必须走以下流程：

1. 明确业务目的：新增实体、补约束、优化查询、修历史问题、支持恢复执行、支持长期记忆、支持审计回放
2. 明确该变更属于事务层、状态层、记忆层、审计层还是检索层
3. 修改 SQLAlchemy 模型
4. 设计索引、唯一约束、删除策略、retention 策略
5. 生成 Alembic migration
6. 在 SQLite 本地库上验证 upgrade / downgrade
7. 验证关键读写路径、恢复路径与测试
8. 如涉及派生检索数据，明确是否需要重建
9. 补充文档或变更说明

## 十九、评审清单

提交数据库改动前，至少回答下面问题：

1. 这张表是否表达了单一实体或单一关系
2. 它属于事务层、状态层、记忆层、审计层还是检索层
3. 主键、外键、唯一性是否由数据库保证
4. nullable 是否符合真实业务语义
5. 是否有缺失的子键索引
6. 是否存在冗余索引
7. 状态字段是否需要 CHECK 约束
8. JSON 字段是否真的不该拆表
9. 是否定义了 retention / TTL / 归档策略
10. 是否支持恢复执行、回放或审计的最小数据需求
11. 是否区分了消息、状态、记忆三类数据
12. namespace / scope / tenant 隔离是否清晰
13. 检索索引是否可由事实数据重建
14. 迁移是否兼容 SQLite 的 ALTER 限制
15. 是否会破坏现有数据
16. 是否有对应测试或最小验证步骤

## 二十、官方参考

### 1. SQLite / SQLAlchemy / Alembic

- SQLite Foreign Key Support  
  https://www.sqlite.org/foreignkeys.html
- SQLite Datatypes In SQLite  
  https://www.sqlite.org/datatype3.html
- SQLite STRICT Tables  
  https://www.sqlite.org/stricttables.html
- SQLite Partial Indexes  
  https://www.sqlite.org/partialindex.html
- SQLite ALTER TABLE  
  https://www.sqlite.org/lang_altertable.html
- SQLite Write-Ahead Logging  
  https://sqlite.org/wal.html
- SQLite PRAGMA Statements  
  https://www.sqlite.org/pragma.html
- SQLAlchemy 2.0 Constraints and Indexes  
  https://docs.sqlalchemy.org/20/core/constraints.html
- Alembic Batch Migrations for SQLite  
  https://alembic.sqlalchemy.org/en/latest/batch.html
- Alembic Naming Constraints  
  https://alembic.sqlalchemy.org/en/latest/naming.html

### 2. Agent 状态、记忆与持久化

- LangGraph Persistence  
  https://docs.langchain.com/oss/python/langgraph/persistence
- LangChain Long-term Memory  
  https://docs.langchain.com/oss/python/langchain/long-term-memory
- LangChain Deep Agents Memory  
  https://docs.langchain.com/oss/python/deepagents/long-term-memory
- AutoGen Managing State  
  https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/tutorial/state.html
- AutoGen Memory and RAG  
  https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/memory.html
- Mem0 Memory Types  
  https://docs.mem0.ai/core-concepts/memory-types
- Mem0 Configure the OSS Stack  
  https://docs.mem0.ai/open-source/configuration
- Mem0 pgvector  
  https://docs.mem0.ai/components/vectordbs/dbs/pgvector
- Mem0 Milvus  
  https://docs.mem0.ai/components/vectordbs/dbs/milvus
