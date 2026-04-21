---
title: LangGraph Agent Development Spec
summary: 基于 LangGraph 官方文档与 vidgen 当前实现整理的 Agent 设计与开发规范
---

本文档基于 2026-04-10 检索的 LangGraph 官方文档整理，并结合 `vidgen` 当前代码库现状，形成项目内面向 LangGraph Agent 的设计与开发规范。

说明：

- 本文档优先采用 LangGraph 官方文档中的稳定概念：`StateGraph`、state、reducers、checkpointer、thread、interrupt、store、subgraph、streaming、test
- 截至 2026-04-10，LangGraph 官方文档主线已是 v1.x；但本仓库当前依赖仍为 `langgraph==0.6.11`
- 因此，本文档中的“设计原则”优先按官方最新能力整理；涉及具体 API 落地时，必须先确认与当前依赖版本兼容，不兼容时先升级，再接入
- 涉及“本项目当前现状”的描述，来自对仓库本地代码的检查，不是来自外部文档
- 本文档不是官方文档翻译，而是把官方推荐模式、生产约束和 `vidgen` 的当前实现合并为一套可执行规范

## 当前栈结论

根据仓库当前实现，可得到以下结论：

- 项目已在 `backend/requirements.txt` 中引入 `langgraph==0.6.11`
- 当前主编排实现位于 `backend/app/agents/langgraph_pipeline.py`
- 当前实现使用 `StateGraph` 构建 DAG，但 `compile()` 时没有接入 LangGraph 原生 `checkpointer`
- 当前“checkpoint”主要依赖 `AgentContext.save_checkpoint()` 将 `artifacts` 快照写入业务库
- 当前长期记忆主要由 `backend/app/services/agent_memory.py` 提供自定义 KV 式用户记忆服务，而不是 LangGraph 原生 `store`

这意味着：

- 当前项目已经在使用 LangGraph 做编排
- 但当前实现更接近“LangGraph 编排 + 项目自定义持久化”，而不是“完整使用 LangGraph thread / checkpoint / interrupt / store 体系”
- 后续新增 Agent 时，应该优先按本规范收敛，而不是继续把所有状态恢复、人工中断、跨线程记忆都堆到业务自定义逻辑里

## 目标

LangGraph Agent 的设计与开发必须同时满足以下目标：

- 流程可视化、可解释
- 状态边界清晰
- 节点职责单一
- 分支路由可追踪
- 错误恢复有明确策略
- 可暂停、可恢复、可回放
- 可测试、可观测
- 适配 `vidgen` 当前后端架构
- 为未来升级到 LangGraph v1.x 正式生产模式保留清晰边界

## 一、何时应使用 LangGraph

### 1. LangGraph 适合“长流程、有状态、会分支”的 Agent

以下场景优先使用 LangGraph：

- 多步骤流程需要显式节点和边
- 节点之间需要共享状态
- 需要条件路由、并行执行或回环
- 需要人工审批、人工补充信息或中断恢复
- 需要失败后从最近一步恢复
- 需要调试执行轨迹、查看中间状态或做 time travel
- 需要多 Agent / subgraph 组合

### 2. 简单任务不要为了“Agent 化”而强上 LangGraph

以下场景不应默认引入 LangGraph：

- 单次 LLM 调用即可完成的任务
- 没有共享状态、没有多步分支的简单流水线
- 纯工具封装、纯同步 CRUD、纯格式转换
- 任务生命周期很短，不需要恢复执行、人工中断或运行轨迹

经验规则：

- “需要流程控制”时，用 LangGraph
- “只需要一个 prompt”时，不要强行上 LangGraph
- “固定 DAG”优先先建 workflow，再评估是否真的需要 agent loop

### 3. 先选模式，再写代码

根据官方文档，常见模式至少包括：

1. Prompt chaining：适合稳定、可拆解、强顺序的步骤链
2. Routing：适合先分类、再进入不同专用分支
3. Parallelization：适合独立子任务并发执行或多路评估
4. Orchestrator-worker：适合由上游规划者拆任务给下游工作节点
5. Evaluator-optimizer：适合“生成-评估-重试”闭环
6. Agent loop：适合问题路径不可预先穷举、需要自主选工具的任务

规则：

- 不允许先写一个“万能 agent”，再回头给它找场景
- 新建 Agent 前，必须先明确它属于上面哪一种或哪几种组合模式
- 如果流程本质上是固定 DAG，不要包装成自由度过高的工具调用 agent

## 二、总体设计原则

### 1. 从业务流程出发，不从 prompt 出发

LangGraph 官方推荐先把流程拆成离散步骤，再定义状态和连接关系。实践上必须遵守：

1. 先画出业务步骤
2. 再识别每一步属于 LLM、数据访问、外部动作还是人工输入
3. 再定义状态
4. 最后实现节点和路由

禁止做法：

- 先堆一个超长 system prompt，再把所有逻辑塞进一个节点
- 用一个节点同时承担规划、检索、执行、审批和收尾
- 只有 prompt，没有清晰的状态模型和路由规则

### 2. 节点做工作，边做路由

LangGraph 官方明确强调：nodes do the work, edges tell what to do next。

规则：

- 节点负责执行单一职责逻辑
- 边负责表达固定连接或条件分支
- 当节点既要更新状态又要决定去向时，优先使用 `Command`
- 不要把复杂控制流隐藏在外部 if/else 嵌套里，导致图结构和真实执行脱节

### 3. 节点边界应围绕失败模式与可观测性来划分

节点不是越少越好，也不是越碎越好。正确的划分方式是：

- 不同失败模式的逻辑应拆成不同节点
- 外部 API、数据库写入、LLM 调用、人工审批尽量拆开
- 需要独立重试、独立监控、独立回放的步骤必须拆节点

推荐拆分：

- `classify`
- `retrieve_docs`
- `generate_plan`
- `execute_tool`
- `qa_review`
- `human_review`

避免拆分：

- 一个 `run_everything`
- 一个 `agent_step` 同时做 4 种外部调用
- 在单节点里串联多个不可重复副作用

## 三、State 设计规范

### 1. State 是共享事实，不是 prompt 缓存

官方文档明确建议：state 存 raw data，prompt 在节点内按需格式化。

必须遵守：

- State 中存原始输入、结构化结果、关键中间产物、恢复执行所需元数据
- Prompt 模板、格式化后的大段上下文、渲染好的说明文字不应常驻 state
- 节点需要 prompt 时，临时从 raw state 组装

这样做的原因：

- 不同节点可以用同一份原始数据做不同格式化
- 改 prompt 不会破坏历史 state
- 调试时能看清节点真正拿到了什么原始数据

### 2. 只有真正“跨步骤需要”的数据才进入 state

判断标准：

- 该数据是否要跨节点复用
- 该数据是否无法重新推导
- 该数据是否昂贵、慢或不稳定，不适合每次重新取
- 该数据是否影响恢复、审计、回放或调试

应进入 state：

- 原始用户输入
- 结构化分类结果
- 检索原始结果
- 工具执行结果
- 最终产出
- 当前阶段状态、错误摘要、审批结果
- 执行元数据

不应进入 state：

- 可从别的字段推导出的临时字符串
- 仅在当前节点内部使用的局部变量
- 纯展示文案
- 与真实数据重复的 prompt 拼接结果

### 3. State schema 默认优先 `TypedDict`

根据官方 Graph API：

- 默认优先 `TypedDict`
- 需要默认值时可考虑 `dataclass`
- 需要更强校验时可使用 Pydantic，但需接受性能成本

规范：

- 大部分图状态使用 `TypedDict`
- 只有在确实需要递归校验或复杂对象验证时才引入 Pydantic
- 不允许为了“类型看起来高级”而把所有 state 都切成重型 Pydantic

### 4. 显式区分输入、输出和私有状态

Graph API 支持 input schema、output schema 和 private channels。

规则：

- 外部接口输入输出应尽量简洁
- 图内部传递用到、但外部不关心的数据，放入内部 state 或私有 channel
- 不要把所有中间字段都暴露成对外 API 契约

### 5. Reducer 必须显式设计，不能靠默认碰运气

官方文档明确指出：

- 默认 reducer 是覆盖
- 列表累积等场景需要显式 reducer
- 消息列表不能简单无脑 `operator.add`

规则：

- 对每个 state key，都要明确它是 overwrite 还是 accumulate
- 聊天消息历史必须优先使用 `add_messages` 或 `MessagesState`
- 不允许在需要“更新已有消息”的场景里仍使用简单 list append 逻辑
- 任何 reducer 的选择，都应能解释为什么不会导致状态污染或重复累积

### 6. Chat Agent 优先基于 `MessagesState` 扩展

官方文档建议消息型图使用 `MessagesState`。

规范：

- 对话型 Agent 默认从 `MessagesState` 继承，再扩展业务字段
- 非对话型 workflow 不要为了统一而强行塞 `messages`
- `messages` 之外的业务状态必须独立命名，例如 `retrieval_results`、`tool_outputs`、`qa_report`

## 四、节点实现规范

### 1. 节点函数只承担一个稳定职责

节点实现必须做到：

- 输入明确
- 输出明确
- 副作用可识别
- 失败策略明确

推荐职责：

- 分类
- 规划
- 检索
- 工具执行
- 结果评估
- 人工审批
- 持久化或发送动作

### 2. 节点内部按需格式化 prompt

规则：

- Prompt 模板应尽量局部化到节点
- 节点内先读取 raw state，再拼装 prompt
- 路由判断优先使用结构化输出，不要从自然语言长文本里二次猜测

推荐：

- `llm.with_structured_output(...)`
- 明确的 schema / enum
- 节点返回结构化字典，再由图路由

### 3. 节点签名按官方能力预留扩展位

官方 Graph API 说明，节点可接受：

1. `state`
2. `config`
3. `runtime`

规范：

- 纯逻辑节点只接收 `state`
- 需要读取 `thread_id`、trace tags 等运行信息时再接 `config`
- 需要访问跨线程 store、stream writer、runtime context 时接 `runtime`
- 不要为了“未来可能用到”就给每个节点塞一长串自定义参数

### 4. 外部副作用必须与纯推理逻辑隔离

副作用包括：

- 数据库写入
- 文件写入
- 发邮件 / 发消息
- 第三方 API 变更操作
- 扣费或创建外部资源

规则：

- 尽量单独放在专门节点
- 必须定义是否可重试、是否幂等、失败后如何补偿
- 不允许把多个不可逆外部动作堆在一个节点里

## 五、路由与控制流规范

### 1. 固定顺序用普通边，分支用条件边

规则：

- 永远固定的连接用 `add_edge`
- 需要根据 state 计算下一步时用 `add_conditional_edges`
- 当节点既更新 state 又决定目标节点时，用 `Command`

### 2. 动态 fan-out 才使用 `Send`

官方 Graph API 中，`Send` 适用于 map-reduce 或动态并发分发。

规则：

- 只有当下游节点数量在运行时才知道时，才使用 `Send`
- 如果边的目标集合是静态已知的，优先显式节点和显式边

### 3. 自由度和风险必须匹配

规则：

- 高风险动作前不允许让 agent 自由决定一切
- 涉及发送、发布、写库、删改外部资源时，优先显式分支或人工审批
- 能 workflow 的地方不要故意写成高自由度 agent loop

## 六、错误处理规范

官方“Thinking in LangGraph”把错误分为四类，这一分类应直接成为项目标准。

### 1. 瞬时错误：系统自动重试

适用：

- 网络抖动
- 第三方 429 / 5xx
- 短时超时

规则：

- 使用 `RetryPolicy`
- 只对明确可重试错误启用重试
- 重试策略按节点配置，不做全局粗暴重试

### 2. LLM 可恢复错误：写回 state，再回环

适用：

- 工具调用失败，但模型可根据错误调整参数后重试
- 结构化输出有缺口，需要模型补足

规则：

- 将错误摘要写入 state
- 让上游 LLM 节点看到错误后决定下一步
- 不要吞错后静默继续

### 3. 用户可修复错误：`interrupt`

适用：

- 缺少必要输入
- 需要人工确认
- 需要补充业务上下文

规则：

- 用 `interrupt` 暂停
- 恢复值必须可序列化
- 恢复后的路由应显式定义

### 4. 未知错误：直接抛出

规则：

- 无法判断如何恢复的异常不要包装成“正常结果”
- 不要用大而全的 `except Exception: return {"error": ...}` 吞掉真实问题
- 未知问题应进入日志、监控和调试流程

## 七、Persistence、Thread 与 Memory 规范

### 1. 需要恢复执行时，必须使用原生 checkpointer

官方文档明确指出：human-in-the-loop、memory、time travel、fault-tolerance 都依赖 checkpointer。

因此：

- 只要 Agent 需要暂停恢复、线程记忆、状态回放、部分执行测试，就必须在 `compile()` 时接入 checkpointer
- 不允许只依赖业务表里的自定义 JSON 快照，就宣称“已支持 LangGraph 持久化”

对 `vidgen` 当前代码的要求：

- 现有 `context.save_checkpoint()` 可继续作为业务快照或兼容层
- 但新建需要真正恢复图执行的 Agent，不应只靠该方法

### 2. 每次持久化执行都必须有稳定 `thread_id`

官方文档要求：带 checkpointer 的图执行时，`thread_id` 是恢复和查询状态的主键。

规则：

- 所有持久化执行都必须传入 `configurable.thread_id`
- `thread_id` 必须是稳定业务标识，不得每次 invoke 临时随机生成
- 推荐把 `thread_id` 与业务会话 / run / conversation 显式映射

### 3. Checkpoint 是线程内状态，不是跨线程长期记忆

规则：

- thread checkpoint 解决“当前线程如何暂停、恢复、回放”
- long-term memory 解决“跨线程如何保留用户或项目记忆”
- 不允许把 checkpoint 当长期记忆库使用
- 也不允许把长期记忆 KV 表当线程恢复机制使用

### 4. 跨线程记忆用 `store`，并做命名空间隔离

官方文档明确：

- checkpointer 保存 thread 内状态
- `store` 保存跨 thread 的共享记忆

规范：

- 长期记忆默认进入 `store`
- namespace 使用 tuple，至少包含稳定业务维度
- 推荐基础维度：`(tenant_id, user_id, agent_name, "memories")`
- 如果当前阶段仍沿用项目自定义 `AgentMemoryService`，也必须保持与上述命名空间语义一致

### 5. 长期记忆写入必须区分 hot path 与 background

官方 Memory 文档明确指出，长期记忆可以在主执行路径中写入，也可以后台异步写入。

规则：

- 只有当记忆需要立刻影响当前响应时，才放在 hot path
- 语义提炼、偏好总结、历史归纳、few-shot 沉淀等高耗时记忆，优先放后台任务
- 不要让主回答链路同时承担“解决当前任务”和“大量记忆提炼”两种重负
- 如果采用后台写入，必须明确触发时机、去重策略和失败补偿

### 6. 生产环境不要使用内存 store / 内存 checkpointer

规则：

- `InMemorySaver` / `MemorySaver` 只用于开发和测试
- 生产环境必须使用持久化后端
- 选型要与当前后端数据栈、部署方式和运维能力一致

## 八、Human-in-the-Loop 规范

### 1. 需要人工介入的地方，优先用 `interrupt`

适用场景：

- 审批发送、审批发布
- 人工补充缺失信息
- 人工修正文案、计划、参数
- 高风险动作确认

### 2. `interrupt` 使用必须遵守四条硬规则

根据官方文档：

1. 不要把 `interrupt` 包在宽泛的 `try/except` 里
2. 不要在同一节点中改变多个 `interrupt` 的调用顺序
3. 不要向 `interrupt` 传不可序列化复杂对象
4. `interrupt` 前的副作用必须幂等，或移到 `interrupt` 之后 / 独立节点

项目内将这四条视为强约束，不是建议项。

### 3. 高风险副作用应放在审批之后

规则：

- 发送邮件、落库、下单、发起外部任务等动作，默认放在审批通过之后
- 如果审批前必须先写入某些数据，该操作必须幂等
- 无法保证幂等时，必须拆节点，不允许写在 `interrupt` 前面

### 4. 静态中断只用于调试，不用于真实审批流

官方文档说明，`interrupt_before` / `interrupt_after` 适合作为断点调试。

规则：

- 真实 HITL 流程使用 `interrupt()`
- 调试、排障、手工单步执行时，才使用静态中断

## 九、Subgraph 与多 Agent 规范

### 1. Subgraph 是复杂 Agent 的首选拆分方式

官方文档明确，subgraph 适合：

- multi-agent systems
- 复用一组节点
- 团队分工开发

因此：

- 当某一能力块拥有独立输入输出契约时，优先抽成 subgraph
- 当不同团队或不同模块要并行开发时，优先定义 subgraph 边界

### 2. 父图与子图的通信方式必须显式选择

两种模式：

1. 不共享 state schema：在父节点中包装调用，显式做输入输出映射
2. 共享 state key：直接把 compiled subgraph 作为节点加入父图

规则：

- 只要父子图状态语义不同，就必须显式转换
- 不允许为了省事，把所有字段都塞进共享 state，导致上下游强耦合

### 3. Subgraph persistence 默认使用 per-invocation

官方文档建议，大多数应用尤其 multi-agent 场景，默认每次调用隔离。

规则：

- 子 agent 默认每次调用独立，不保留自己的长期会话状态
- 只有当某个 subagent 真的需要多轮上下文延续时，才启用 per-thread
- 完全不需要持久化时，才考虑 stateless

## 十、项目结构规范

### 1. 新的复杂 LangGraph Agent 优先使用目录化结构

参考官方 application structure，推荐：

```text
backend/app/agents/<agent_name>/
├── __init__.py
├── state.py
├── nodes.py
├── tools.py
├── graph.py
└── prompts.py
```

约定：

- `state.py`：状态 schema、reducers、context schema
- `nodes.py`：节点实现
- `tools.py`：LangChain tool / 业务工具包装
- `graph.py`：构图与 `compile()`
- `prompts.py`：节点级 prompt 模板

### 2. 简单图允许单文件，但不要无限长大

规则：

- 如果图只有 3 到 5 个简单节点，可先放在一个文件中
- 当同文件同时承载 state、tool、node、routing、test fixture 时，应及时拆分
- 超过一个人难以快速读懂的单文件 agent，不应继续堆逻辑

### 3. `vidgen` 当前风格下的落地建议

结合当前仓库：

- 现有 `backend/app/agents/langgraph_pipeline.py` 可以继续维护
- 但新建“对话式 agent”“多工具 agent”“多 subgraph agent”时，不建议继续集中到这个文件中
- 后续若重构当前主流程，优先把 state、nodes、graph 拆开，再接入原生 checkpointer / store

## 十一、测试与可观测性规范

### 1. 每个图都必须有图级测试

官方测试指南建议：每次测试使用新的 checkpointer 实例并在测试中重新 compile。

规则：

- 图工厂函数与 compile 逻辑应可在测试中重复构建
- 不要把全局单例图直接写死到难以测试

### 2. 节点级、路径级、恢复级测试都要覆盖

至少覆盖：

- 单节点输入输出测试
- 条件路由测试
- 中断恢复测试
- 部分执行测试
- 错误与重试测试
- 长期记忆命名空间测试

### 3. 部分执行测试应利用持久化能力

官方文档说明，可使用 `update_state`、`interrupt_after` 等方式测试局部路径。

规则：

- 对复杂图，不要求每次都从 `START` 跑完整链路
- 允许使用保存状态模拟进入中间节点
- 但前提是图本身具有清晰的 checkpoint / thread 设计

### 4. 观测必须至少覆盖状态和节点轨迹

规则：

- 新增 LangGraph Agent 时，默认接入 tracing / observability
- 需要能看到当前节点、路由决策、中间状态摘要、错误信息
- 对生产可疑问题，优先通过轨迹定位，不靠日志拼猜

## 十二、面向 `vidgen` 的专项约束

### 1. 当前视频主流程更适合“workflow + evaluator”，而不是自由 agent loop

原因：

- 当前主流程步骤相对固定
- `orchestrator -> prompt -> audio/video -> editor -> qa` 的边界较清晰
- QA 本质上接近 evaluator-optimizer 模式

因此：

- 主视频生成链路应继续以显式 workflow 为主
- 不要为了“更像 agent”而改造成高自由度工具循环

### 2. 如果要新增对话式或工具式 Agent，应与主流水线分层

建议：

- 生成型 pipeline 保持 workflow
- 对话式助手、知识检索助手、运营辅助助手可单独建 LangGraph agent
- 两者共享业务服务层，但不强行共用一个大图

### 3. 本项目的长期记忆应明确分层

建议分层：

- thread 内短期状态：LangGraph checkpoint
- 跨 thread 的用户 / 项目偏好：LangGraph store 或语义等价的统一记忆层
- 业务事实数据：SQLAlchemy 模型与关系库

不允许：

- 用 `artifacts_snapshot` 同时承担线程状态和长期记忆
- 用 `agent_memories` 表承担所有运行中恢复逻辑

## 十三、开发流程

每次新增 LangGraph Agent，至少走以下流程：

1. 明确业务目标和是否真的需要 LangGraph
2. 选择模式：workflow、routing、parallel、orchestrator-worker、evaluator-optimizer 或 agent loop
3. 定义 state schema 与 reducers
4. 划分节点和副作用边界
5. 定义路由规则与终止条件
6. 决定是否需要 checkpointer、store、interrupt、subgraph
7. 实现 graph factory / compile 入口
8. 补齐图级、节点级、恢复级测试
9. 补充开发文档与运行说明

## 十四、评审清单

提交 LangGraph Agent 代码前，至少回答下面问题：

1. 这个任务真的需要 LangGraph 吗
2. 当前设计属于哪种官方模式
3. state 是否只保存真正跨步骤需要的数据
4. 是否把 prompt 文本错误地常驻在 state 中
5. reducer 是否为每个关键字段明确设计
6. 消息状态是否使用了正确的消息 reducer
7. 节点是否围绕失败模式和副作用边界拆分
8. 条件路由是否可追踪、可测试
9. 高风险动作前是否需要 `interrupt`
10. `interrupt` 前是否存在非幂等副作用
11. 需要恢复执行时，是否真正使用了 checkpointer
12. `thread_id` 是否是稳定业务标识
13. 长期记忆是否与线程状态明确分层
14. 子图边界和输入输出契约是否清晰
15. 是否有图级测试、路径测试和恢复测试
16. 是否具备足够的执行轨迹和调试能力

## 十五、官方参考

### 1. LangGraph 核心文档

- LangGraph Overview  
  https://docs.langchain.com/oss/python/langgraph/overview
- Thinking in LangGraph  
  https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph
- Workflows and agents  
  https://docs.langchain.com/oss/python/langgraph/workflows-agents
- Graph API overview  
  https://docs.langchain.com/oss/python/langgraph/graph-api

### 2. 持久化、记忆与人工介入

- Persistence  
  https://docs.langchain.com/oss/python/langgraph/persistence
- Memory  
  https://docs.langchain.com/oss/python/langgraph/memory
- Interrupts  
  https://docs.langchain.com/oss/python/langgraph/interrupts
- Streaming  
  https://docs.langchain.com/oss/python/langgraph/streaming

### 3. 生产与结构化开发

- Subgraphs  
  https://docs.langchain.com/oss/python/langgraph/use-subgraphs
- Application structure  
  https://docs.langchain.com/oss/python/langgraph/application-structure
- Test  
  https://docs.langchain.com/oss/python/langgraph/test
