# Agent 系统模块成熟度评估报告

## 1. 评估概览

### 项目定位

代码事实：`vidgen` / `capy` 当前是一个面向短视频生产的 AI 工作台，包含 Vue + Vite 前端、FastAPI 后端、本地账号体系、素材/项目/会话管理，以及由多个 Agent 阶段驱动的一键生成链路。文档与代码均显示系统支持两类流程：

- 自动模式：通过会话式工作台上传素材、选择图片、绑定参考视频或背景模板，并触发视频生成 / 复刻 / 分析等动作。
- 手动模式：按上传、分析、素材选择、提示词、生成、Talking Head、时间轴剪辑逐步操作。

架构判断：该项目已经不是单一 LLM wrapper，而是一个“面向视频生产的 Agent 工作流系统”。它具备 Agent 系统的多个核心构件，但距离生产级 Agent 平台仍有明显差距，尤其在安全、状态一致性、记忆闭环、评估体系和测试一致性上。

### 当前系统整体成熟度总结

整体成熟度判断：**基础可用到较完整之间，约 2.6 / 5**。

代码事实：

- `backend/app/agents/` 已按 `core / stages / executors / chat / skills` 分层。
- `PipelineRun`、`AgentExecution`、`ModelUsage`、`AutoChatSession` 等模型已经记录主流程状态与用量。
- 默认 `PIPELINE_ENGINE` 为 `langgraph`，同时存在顺序 `PipelineExecutor` 与 `LangGraphPipelineExecutor`。
- `ChatAgent` 已通过 runtime skill metadata、routing hints、LLM 候选路由和 direct stream 分支处理普通对话与工具调用。
- `backend/app/agents/skills/` 已采用 `SKILL.md + schema.json + runtime.py` 的目录式 runtime skill，并在启动时自动注册到 `ToolRegistry`。

架构判断：

- 自动模式图文生成主链路已经具备较完整骨架。
- 复刻链路处在重构后不完全收敛状态：新代码引入 `ReplicationPlannerAgent`，但部分前端、确认接口和旧测试仍按 `orchestrator` 查找复刻输出。
- 记忆、审计、检索、工具调用持久化等能力存在模型或服务，但没有全部接入主流程。
- 安全边界有雏形，但上传校验、密钥管理、静态资源鉴权等生产级要求不足。

### 主要优点

- Agent 分层清晰：`BaseAgent`、`AgentContext`、阶段 Agent、执行器、ChatAgent、ToolRegistry 和 runtime skills 已经形成独立边界。
- 主流程可观测性较好：每个 Agent 执行会落库 `AgentExecution`，并通过 SSE 暴露给前端；模型用量通过 `ModelUsage` 聚合。
- 编排能力不止一种：同时存在顺序 Pipeline 和 LangGraph DAG 两类执行模式。
- runtime skill 设计较现代：启动只读 frontmatter，命中后再加载正文、schema、runtime 与参考文件，避免每次把全部工具细节送入模型。
- 视频生产交付闭环较完整：提示词、TTS、视频生成、剪辑、仓库保存、平台预览、抖音发布草稿与发布接口均有实现。

### 主要短板

- 安全能力不足：上传文件名、MIME、大小、Agent 文件访问、LLM 输出清洗等能力尚未形成已接入主链路的统一实现；`config.py` 中存在非空 API key 形态的默认值，需确认是否为真实密钥；`/generated`、`/repository` 静态路径绕过认证。
- 状态与复刻链路不一致：`ReplicationPlannerAgent` 已替代旧 Orchestrator 内联复刻逻辑，但 `confirm-plan` 仍查找 `agent_name == "orchestrator"`，前端也主要从 `orchestratorExecution` 读取 `replication_plan`。
- 记忆系统未形成闭环：自定义 `AgentMemoryService` 基本只作为上下文对象传递，未被主阶段实际读写；Mem0 被 ChatAgent / Orchestrator 使用，但工具触发的 pipeline 未传入 `user_id/mem0`。
- Agent 状态模型“表很完整，接入不足”：`AgentThread`、`AgentRun`、`AgentStep`、`AgentCheckpoint`、`ToolCall`、`ModelCall`、`RunEvent`、`RetrievalDocument` 只在模型层和 `models/__init__.py` 被引用，未见主流程写入。
- 测试与实现不同步：`test_orchestrator_replication_prompt.py` 仍测试已迁移的方法；`test_pipeline_runtime.py` 当前路由返回 422，需进一步定位。

## 2. 总体评分表

| 模块名称 | 当前状态 | 成熟度评分（0-5） | 证据（关键代码目录/文件） | 主要问题 | 优先级 |
|---|---|---:|---|---|---|
| 1. 感知与输入层 | 较完整，主链路已使用 | 3 | `backend/app/routers/auto_sessions.py`、`backend/app/routers/upload.py`、`backend/app/routers/materials.py`、`backend/app/schemas/pipeline.py`、`frontend/src/components/pipeline/AutoModeStudio.vue`、`backend/app/services/qwen_client.py` | 文件上传安全校验未完整接入；请求 schema 枚举/范围约束弱；多入口对参考视频意图处理不一致 | P1 |
| 2. 意图理解与任务解析 | 较完整但存在链路不一致 | 3 | `backend/app/agents/chat/agent.py`、`backend/app/agents/stages/orchestrator.py`、`backend/app/agents/stages/replication_planner.py`、`backend/app/prompts/chat_agent_prompts.py`、`backend/app/agents/skills/*/SKILL.md` | Chat skill 可区分 analyze/replicate/generate，但 pipeline reference_video 分支直接进入 replication planner；旧测试与前端仍按 Orchestrator 取复刻输出 | P0 |
| 3. 记忆系统 | 基础可用，局部接入 | 2 | `backend/app/models/agent_memory.py`、`backend/app/services/agent_memory.py`、`backend/app/services/mem0_service.py`、`backend/app/agents/chat/agent.py`、`backend/app/agents/stages/orchestrator.py` | 自定义 KV 记忆未真正用于主阶段；Mem0 依赖外部初始化，缺少治理和 UI；工具触发 pipeline 未传入记忆上下文 | P1 |
| 4. 规划与推理模块 | 较完整，主链路已使用 | 3 | `backend/app/agents/stages/orchestrator.py`、`backend/app/agents/stages/prompt_engineer.py`、`backend/app/agents/stages/replication_planner.py`、`backend/app/agents/stages/qa_reviewer.py`、`backend/app/prompts/system_prompts.py` | 规划结果缺少版本化/评估；复刻规划迁移后收敛不足；HITL prompt review 有状态但缺少完整 API/UI 闭环 | P1 |
| 5. 工具调用与执行层 | 较完整，ChatAgent 主链路已用 | 3 | `backend/app/agents/core/tool_registry.py`、`backend/app/agents/skills/loader.py`、`backend/app/agents/skills/spec.py`、`backend/app/agents/skills/*/runtime.py`、`backend/app/services/qwen_client.py` | 工具调用未写入 `ToolCall` 表；权限仅内存态；skill runtime 启动后台任务时没有完整透传 user/memory 上下文 | P1 |
| 6. 状态管理与工作流编排 | 基础可用，但存在关键一致性风险 | 2 | `backend/app/models/pipeline.py`、`backend/app/agents/executors/pipeline.py`、`backend/app/agents/executors/langgraph/*`、`backend/app/routers/pipeline.py` | `confirm-plan` 与 `ReplicationPlannerAgent` 不一致；LangGraph 未使用原生 checkpointer；服务重启只标记失败不恢复；pipeline tests 当前 422 | P0 |
| 7. 输出生成与结果交付 | 较完整，主链路已使用 | 3 | `backend/app/agents/stages/video_editor.py`、`backend/app/services/video_editor_service.py`、`backend/app/services/video_delivery.py`、`backend/app/routers/repository.py`、`backend/app/routers/social_accounts.py`、`backend/app/models/video_delivery.py` | 外部平台发布依赖凭证；产物鉴权弱；QA 输入字段与实际输出不完全对齐；缺少发布失败重试/审计策略 | P1 |
| 8. 安全与权限控制 | 基础可用但未达生产级 | 2 | `backend/app/auth.py`、`backend/app/routers/auth.py`、`backend/app/main.py`、`backend/app/agents/core/tool_registry.py`、`backend/app/config.py` | 上传校验未完整接入；Cookie `secure=False`；静态产物公开；无 CSRF/rate limit；疑似密钥默认值；Agent 文件访问沙箱未形成统一入口 | P0 |
| 9. 观测、日志与评估模块 | 基础可用，工程化不足 | 2 | `backend/app/models/pipeline.py`、`backend/app/models/usage.py`、`backend/app/services/usage_service.py`、`backend/app/agents/stages/qa_reviewer.py`、`backend/app/routers/auto_sessions.py` | 有执行记录和用量统计，但缺少集中日志/指标/trace；QA 规则较弱；旧测试失败；`ModelCall/RunEvent` 等审计表未接入 | P1 |
| 10. 多 Agent 协作模块 | 主链路已使用，动态协作不足 | 3 | `backend/app/agents/stages/*`、`backend/app/agents/executors/pipeline.py`、`backend/app/agents/executors/langgraph/*` | 多 Agent 主要是固定流水线；前端 Agent 顺序未包含 `replication_planner/qa_reviewer`；跨 Agent 协议未持久化到通用 Agent 状态表 | P2 |

## 3. 逐模块详细评估

### 1. 感知与输入层

1. 标准定义：感知与输入层负责接收用户文本、会话历史、图片、视频、文件、平台参数、项目上下文等外部输入，并把它们转换为 Agent 可消费的结构化上下文。

2. 当前项目中的对应实现：代码事实显示，系统通过 `auto_sessions` 接收会话消息，通过 `upload` 接收参考视频，通过 `materials` 接收素材文件，通过 `pipeline` 接收生成参数，通过 Qwen client 支持 `image_paths` / `video_paths` 多模态输入。

3. 关键代码/目录/类/函数：
   - `backend/app/routers/auto_sessions.py`：`chat_with_agent(...)`、`_load_session_context(...)`
   - `backend/app/routers/upload.py`：`upload_video(...)`
   - `backend/app/routers/materials.py`：`upload_materials(...)`、`upload_project_materials(...)`
   - `backend/app/schemas/pipeline.py`：`PipelineCreateRequest`
   - `backend/app/services/qwen_client.py`：`chat_json(...)`、`chat_with_tools(...)`
   - `frontend/src/components/pipeline/AutoModeStudio.vue`

4. 当前成熟度评分（0-5）：**3**

5. 评分依据：主流程已能接收文本、图片素材、参考视频、会话参数和平台参数，并进入 Agent pipeline；但输入校验、schema 约束和安全边界不足。

6. 当前已实现能力：
   - 自动模式多会话消息持久化。
   - 会话级素材选择与参考视频绑定。
   - 图片作为视觉输入传给 Orchestrator / PromptEngineer。
   - 视频作为 `video_url` 多模态输入传给 Qwen。
   - 前端可恢复会话状态、素材选择、参考视频和当前 run。

7. 当前缺失能力：
   - 上传文件名、MIME 类型、文件大小校验没有形成所有上传入口共享的统一实现。
   - `PipelineCreateRequest` 对 `platform / duration_mode / transition / bgm_mood` 等字段没有使用枚举或范围约束。
   - 缺少输入级内容安全策略和限流策略。

8. 是否接入主链路：**是**。自动模式和 pipeline 均依赖该层。

9. 主要风险或限制：
   - 恶意或异常文件上传可能绕过现有 helper。
   - 参考视频入口存在多条路径，Chat skill 与 pipeline 直接启动之间的意图处理不完全一致。

10. 建议下一步演进方向：
   - P0/P1：在所有上传入口接入统一的文件名、MIME 类型和文件大小校验。
   - P1：为 `PipelineCreateRequest` 增加 Pydantic 枚举、范围、长度校验。
   - P2：引入上传扫描、配额和内容安全审核。

### 2. 意图理解与任务解析

1. 标准定义：该模块负责理解用户意图，将自然语言请求分类为聊天、生成、分析、复刻、调整、终止等任务，并生成下游执行所需的结构化任务描述。

2. 当前项目中的对应实现：代码事实显示，`ChatAgent` 通过 runtime skill metadata、routing hints 和 LLM 候选路由判断是否调用 `analyze_video / replicate_video / generate_video`；普通图文生成由 `OrchestratorAgent` 解析脚本与图片；参考视频复刻由 `ReplicationPlannerAgent` 生成复刻方案。

3. 关键代码/目录/类/函数：
   - `backend/app/agents/chat/agent.py`：`_route_runtime_skill(...)`、`_score_runtime_skill_candidates(...)`、`_route_skill_candidates_with_llm(...)`
   - `backend/app/agents/stages/orchestrator.py`：`OrchestratorAgent.execute(...)`
   - `backend/app/agents/stages/replication_planner.py`：`ReplicationPlannerAgent.execute(...)`
   - `backend/app/agents/skills/analyze-video/SKILL.md`
   - `backend/app/agents/skills/replicate-video/SKILL.md`
   - `backend/app/agents/skills/generate-video/SKILL.md`
   - `backend/app/prompts/chat_agent_prompts.py`

4. 当前成熟度评分（0-5）：**3**

5. 评分依据：对话式入口已经具备显式 skill 路由和保守 fallback；但 pipeline 级 reference video 分支与 ChatAgent 级意图路由没有完全统一。

6. 当前已实现能力：
   - 普通聊天走 direct LLM stream。
   - 明确工具意图时走 runtime skill 调用。
   - 候选 skill 冲突时使用 LLM 做轻量路由。
   - 普通图文生成可拆解 shot plan、时长、脚本分段。
   - 复刻路径能生成 `replication_plan`、`analysis_report`、`extracted_frames`。

7. 当前缺失能力：
   - `PipelineExecutor` / `LangGraphPipelineExecutor` 在检测到 `reference_video_id` 时直接路由到 `replication_planner`，未见同等的 “analysis_only” pipeline 级分类。
   - 旧文档和测试仍描述 Orchestrator 内联复刻方法，但当前 `OrchestratorAgent` 已无 `_build_replication_user_prompt` 等方法。
   - 缺少统一的 intent decision 记录，例如没有把“为什么选择某个 skill”落到 `RunEvent` 或 `ToolCall`。

8. 是否接入主链路：**是**。ChatAgent、Orchestrator、ReplicationPlanner 都接入了自动模式。

9. 主要风险或限制：
   - 同一参考视频请求从 ChatAgent 进入和直接从 pipeline API 进入，可能走出不同语义。
   - 复刻相关前端和确认接口仍按 `orchestrator` 读取输出，可能导致等待确认阶段无法继续。

10. 建议下一步演进方向：
   - P0：统一参考视频意图入口，明确 `analyze_video` 与 `replicate_video` 谁负责 pipeline 级分支。
   - P0：修正旧测试、前端与 `confirm-plan` 中的 agent 名称耦合。
   - P1：把 skill 路由结果写入可审计事件。

### 3. 记忆系统

1. 标准定义：记忆系统负责跨消息、跨会话、跨 run 保存用户偏好、历史事实、任务经验，并在后续推理或工具调用中可控地检索和使用。

2. 当前项目中的对应实现：代码事实显示，项目存在两套记忆能力：自定义 KV 记忆 `AgentMemoryService` + `AgentMemory` 表，以及语义记忆 `Mem0Service`。`ChatAgent` 会检索 Mem0 并异步写入对话记忆；`OrchestratorAgent` 会检索/写入部分平台风格记忆。

3. 关键代码/目录/类/函数：
   - `backend/app/models/agent_memory.py`：`AgentMemory`
   - `backend/app/services/agent_memory.py`：`AgentMemoryService`
   - `backend/app/services/mem0_service.py`：`Mem0Service`
   - `backend/app/main.py`：`app.state.agent_memory`、`app.state.mem0`
   - `backend/app/agents/chat/agent.py`：Mem0 search / add
   - `backend/app/agents/stages/orchestrator.py`：Mem0 search / add_explicit

4. 当前成熟度评分（0-5）：**2**

5. 评分依据：有明确的数据模型和服务，且 Mem0 已接入 ChatAgent / Orchestrator；但自定义 KV 记忆没有被主阶段实际读写，记忆治理缺失，工具触发 pipeline 的记忆上下文传递也不完整。

6. 当前已实现能力：
   - 用户级 Mem0 语义搜索。
   - 对话完成后异步写入 Mem0。
   - pipeline 完成后可写入简短平台/风格记忆。
   - `AgentMemoryService` 支持 user scope 的 get/set/get_all/delete 和 typed helper。

7. 当前缺失能力：
   - `AGENT_MEMORY_ENABLED` 配置项未见实际条件控制。
   - `AgentMemoryService.remember_* / recall_*` 未见主流程调用。
   - runtime skill 的 `generate_video` / `replicate_video` 启动 `_run_pipeline(...)` 时没有传入 `user_id / memory_service / mem0`。
   - 没有记忆查看、删除、同意、TTL 策略和冲突处理 UI。

8. 是否接入主链路：**部分接入**。Mem0 接入 ChatAgent / Orchestrator；自定义 KV 记忆基本未发挥作用。

9. 主要风险或限制：
   - 记忆能力可能在不同入口表现不一致。
   - 语义记忆存储、隐私、过期策略待确认。

10. 建议下一步演进方向：
   - P1：选择一个主记忆层，明确 KV 记忆与 Mem0 的职责边界。
   - P1：修复 skill 启动 pipeline 时的记忆上下文透传。
   - P2：增加记忆治理 API/UI 和可审计写入事件。

### 4. 规划与推理模块

1. 标准定义：规划与推理模块负责把任务拆解为可执行步骤，生成分镜、提示词、复刻方案、编辑策略、QA 判断，并在必要时进行重试或再规划。

2. 当前项目中的对应实现：普通生成由 Orchestrator 拆解分镜，PromptEngineer 生成视频提示词和语音参数，ReplicationPlanner 生成复刻方案，QAReviewer 做规则 + LLM 质量审查。

3. 关键代码/目录/类/函数：
   - `backend/app/agents/stages/orchestrator.py`
   - `backend/app/agents/stages/prompt_engineer.py`
   - `backend/app/agents/stages/replication_planner.py`
   - `backend/app/agents/stages/qa_reviewer.py`
   - `backend/app/prompts/system_prompts.py`

4. 当前成熟度评分（0-5）：**3**

5. 评分依据：规划模块已被主流程使用，并有结构化 schema 约束和 fallback；但评估闭环和规划版本管理较弱。

6. 当前已实现能力：
   - 固定时长模式下的镜头时长可行性校验。
   - LLM 结构化分镜输出，失败时本地脚本切分 fallback。
   - 提示词生成 fallback。
   - 复刻方案清洗、修复与关键帧补充。
   - QAReviewer 可根据硬规则和 LLM 判断推荐 retry。

7. 当前缺失能力：
   - 规划结果没有版本化或 prompt 版本追踪接入。
   - `QAReviewerAgent` 的 `audio_duration_seconds` / `final_video_duration` 读取字段与实际 `duration_ms` 输出不完全对齐，导致部分 QA 规则可能失效。
   - `waiting_prompt_review` 有执行器状态，但未见完整恢复 API/UI。

8. 是否接入主链路：**是**。普通生成和复刻路径均接入。

9. 主要风险或限制：
   - 复刻规划逻辑迁移后，多处仍使用旧 Orchestrator 口径。
   - QA 结果可能给出过于乐观的通过判断。

10. 建议下一步演进方向：
   - P1：对规划/提示词/QA 引入 prompt version 和评估样例。
   - P1：修正 QA 输入字段，使 `duration_ms` 与秒级规则对齐。
   - P2：把动态再规划沉淀为可复用的 planner interface。

### 5. 工具调用与执行层

1. 标准定义：工具调用与执行层负责把 Agent 的意图转成受控工具调用，进行权限检查、参数提取、执行、结果回传、错误处理和审计。

2. 当前项目中的对应实现：`ToolRegistry` 统一注册工具并检查权限；runtime skill loader 从 `SKILL.md` 读取 manifest，命中后 lazy load schema/runtime；`ChatAgent` 通过工具事件流执行 `analyze_video / replicate_video / generate_video`。

3. 关键代码/目录/类/函数：
   - `backend/app/agents/core/tool_registry.py`：`ToolDefinition`、`ToolRegistry`
   - `backend/app/agents/skills/loader.py`：`register_runtime_skills(...)`
   - `backend/app/agents/skills/spec.py`
   - `backend/app/agents/chat/agent.py`：`_execute_selected_tool(...)`、`_build_tool_invocation_kwargs(...)`
   - `backend/app/services/qwen_client.py`：`chat_with_tools(...)`
   - `backend/app/agents/skills/*/runtime.py`

4. 当前成熟度评分（0-5）：**3**

5. 评分依据：工具注册、权限、schema 转换、lazy loading 和 ChatAgent 调用链已经可用；但工具调用审计与持久化不足。

6. 当前已实现能力：
   - 按 agent 授权工具权限。
   - Claude 格式 `input_schema` 与 OpenAI/Qwen tool schema 转换。
   - 依据 required inputs 过滤当前不可用 skill。
   - tool_call / tool_result SSE 事件。
   - skill 参数抽取时按需加载 `SKILL.md` 正文和 `reference.md`。

7. 当前缺失能力：
   - `ToolCall` 模型存在但未见主流程写入。
   - 权限配置是内存态注册，没有用户/角色级持久化。
   - skill runtime 启动后台 pipeline 时没有完整透传 `user_id/mem0`。
   - 缺少工具执行超时、幂等键、重放保护和详细审计。

8. 是否接入主链路：**是**。自动模式 ChatAgent 已使用。

9. 主要风险或限制：
   - 工具调用失败后主要靠错误字符串回传，缺少标准错误码。
   - 工具执行事件不会进入通用 `ToolCall` 表，后续审计和回放困难。

10. 建议下一步演进方向：
   - P1：落地 `ToolCall` 持久化，记录参数摘要、结果摘要、错误与耗时。
   - P1：为工具调用加入幂等 key 和超时控制。
   - P2：扩展为用户/角色/Agent 多维权限模型。

### 6. 状态管理与工作流编排

1. 标准定义：该模块负责管理 run、step、node、artifact、checkpoint、暂停/恢复、取消、重试、并行和分支路由，是长任务 Agent 系统的核心骨架。

2. 当前项目中的对应实现：系统有 `PipelineRun` 和 `AgentExecution` 记录主流程状态；有顺序 Pipeline 与 LangGraph DAG 两种执行器；`AgentContext.save_checkpoint()` 会把 `artifacts` snapshot 写到 `PipelineRun.artifacts_snapshot`。

3. 关键代码/目录/类/函数：
   - `backend/app/models/pipeline.py`：`PipelineRun`、`AgentExecution`
   - `backend/app/agents/core/base.py`：`AgentContext.save_checkpoint(...)`、`restore_checkpoint(...)`
   - `backend/app/agents/executors/pipeline.py`
   - `backend/app/agents/executors/langgraph/executor.py`
   - `backend/app/agents/executors/langgraph/nodes.py`
   - `backend/app/routers/pipeline.py`

4. 当前成熟度评分（0-5）：**2**

5. 评分依据：基础状态机和多执行器已存在，但恢复、复刻确认、前后端 agent 命名和测试存在关键不一致。

6. 当前已实现能力：
   - run 状态：`pending / running / completed / failed / cancelled / waiting_confirmation / waiting_prompt_review`。
   - agent execution 输入、输出、耗时、错误、attempt、progress_text。
   - Agent 级 checkpoint snapshot。
   - 失败 agent 手动 retry 后可继续下游。
   - 复刻确认、调整、取消接口。

7. 当前缺失能力：
   - LangGraph `compile()` 未接入原生 checkpointer。
   - 服务重启时 `recover_interrupted_pipeline_runs()` 直接把 pending/running 标记 failed，而不是从 checkpoint 恢复。
   - 通用 `AgentThread / AgentRun / AgentStep / AgentCheckpoint` 表未见主流程接入。
   - `waiting_prompt_review` 缺少完整确认/恢复路由。
   - 自动模式流程面板对 `replication_planner` 和 `qa_reviewer` 的展示语义仍需结合最新执行记录再核对。

8. 是否接入主链路：**是，但存在关键缺口**。

9. 主要风险或限制：
   - `confirm-plan` 当前查找 `agent_name == "orchestrator"` 的输出；当前执行器复刻路径写入的是 `replication_planner`，可能导致无法确认继续。
   - `test_pipeline_runtime.py` 当前 7 个用例全部在 launch 阶段返回 422，需优先定位路由签名或测试 fixture 的不一致。

10. 建议下一步演进方向：
   - P0：修复复刻等待确认的 agent 名称与输出读取逻辑。
   - P0：修复 pipeline route 422 测试失败，并补充 reference video confirm 回归测试。
   - P1：接入或替换为统一 checkpoint / step / event 数据模型。
   - P2：引入真正的恢复执行策略，而不是启动时全部标记失败。

### 7. 输出生成与结果交付

1. 标准定义：输出生成与结果交付负责把 Agent 执行结果转化为用户可用产物，包括文本回复、分析报告、分镜提示词、音频、字幕、视频片段、成片、仓库保存和平台发布。

2. 当前项目中的对应实现：视频产物由 `VideoGeneratorAgent` 和 `VideoEditorAgent` 生成；交付由 `video_delivery` 生成平台预览、保存仓库、生成抖音草稿和发布；ChatAgent 也能返回分析报告文本。

3. 关键代码/目录/类/函数：
   - `backend/app/agents/stages/audio_subtitle.py`
   - `backend/app/agents/stages/video_generator.py`
   - `backend/app/agents/stages/video_editor.py`
   - `backend/app/services/video_editor_service.py`
   - `backend/app/services/video_delivery.py`
   - `backend/app/routers/repository.py`
   - `backend/app/routers/social_accounts.py`
   - `backend/app/models/video_delivery.py`

4. 当前成熟度评分（0-5）：**3**

5. 评分依据：主流程可产生成片并保存仓库/生成发布草稿；但生产级交付仍需要鉴权、失败重试、外部平台状态同步和质量门禁补强。

6. 当前已实现能力：
   - TTS 音频和字幕生成。
   - 分镜视频并发生成、超时控制和轮询。
   - FFmpeg 合成、转场、BGM、水印、字幕。
   - 自动保存到本地视频仓库。
   - 抖音 / YouTube 预览卡片。
   - 抖音 OAuth 账号与发布草稿/提交发布。

7. 当前缺失能力：
   - 成片访问路径缺少细粒度鉴权。
   - 发布链路缺少后台重试、webhook/回查、平台失败码规范化。
   - QA 不是强门禁，且字段对齐存在问题。

8. 是否接入主链路：**是**。

9. 主要风险或限制：
   - 外部发布依赖凭证和平台权限，不能视为开箱即用。
   - 产物路径暴露在静态目录下，可能带来隐私风险。

10. 建议下一步演进方向：
   - P1：为仓库/生成产物增加授权下载或签名 URL。
   - P1：完善发布状态机和错误码映射。
   - P2：加入质量门禁、人工审核和发布前检查清单。

### 8. 安全与权限控制

1. 标准定义：安全与权限模块负责用户认证、资源隔离、工具权限、文件访问、输入校验、密钥管理、CSRF/rate limit、审计和安全输出控制。

2. 当前项目中的对应实现：本地账号体系、Cookie Session、用户资源隔离 helper、管理员能力和 ToolRegistry 权限已存在；此前未接入主流程的输入安全 helper 已在剪枝中删除，后续需要在真实入口重新接入安全校验。

3. 关键代码/目录/类/函数：
   - `backend/app/auth.py`
   - `backend/app/routers/auth.py`
   - `backend/app/main.py`：`auth_middleware(...)`
   - `backend/app/agents/core/tool_registry.py`
   - `backend/app/config.py`

4. 当前成熟度评分（0-5）：**2**

5. 评分依据：账号和资源隔离基础可用，但生产级安全要求不足，上传、静态资源、密钥与 CSRF/rate limit 仍需系统化治理。

6. 当前已实现能力：
   - PBKDF2 密码哈希。
   - Session token 哈希存储。
   - 当前用户检查和管理员检查。
   - 项目、pipeline run、auto session、material、background template、social account 的用户归属校验 helper。
   - 工具级 required permission。

7. 当前缺失能力：
   - 上传入口缺少统一的文件名、MIME 类型和文件大小校验实现。
   - Agent 文件访问沙箱和 LLM 输出净化尚未形成已接入主流程的统一实现。
   - Cookie `secure=False`，缺少 CSRF 防护。
   - `config.py` 存在非空 API key 形态默认值，是否为真实密钥待确认。
   - `/generated`、`/repository`、`/examples` 在 auth middleware 中豁免。
   - 缺少 rate limiting 和审计日志。

8. 是否接入主链路：**部分接入**。认证/用户隔离接入，输入安全和产物鉴权不足。

9. 主要风险或限制：
   - 文件上传和静态产物暴露是当前最需要优先处理的安全风险。
   - 工具权限只控制 agent 能否调用工具，不等同于用户级授权。

10. 建议下一步演进方向：
   - P0：移除或轮换疑似密钥默认值，改为必填环境变量或本地示例占位。
   - P0：接入上传文件校验和大小限制。
   - P0：为生成产物和仓库文件增加鉴权或签名访问。
   - P1：上线 CSRF、secure cookie、rate limit 和安全审计。

### 9. 观测、日志与评估模块

1. 标准定义：该模块负责记录 Agent 执行过程、模型调用、工具调用、事件、错误、用量、质量评估和回归测试结果，使系统可调试、可审计、可优化。

2. 当前项目中的对应实现：主流程写入 `PipelineRun`、`AgentExecution`、`ModelUsage`；前端通过 SSE 获取进度；`QAReviewerAgent` 做质量评估；`auto_sessions.py` 会把 Chat 错误追加到本地 Markdown 日志。

3. 关键代码/目录/类/函数：
   - `backend/app/models/pipeline.py`
   - `backend/app/models/usage.py`
   - `backend/app/services/usage_service.py`
   - `backend/app/agents/core/base.py`
   - `backend/app/agents/stages/qa_reviewer.py`
   - `backend/app/routers/pipeline.py`
   - `backend/app/routers/auto_sessions.py`
   - `backend/tests/*`

4. 当前成熟度评分（0-5）：**2**

5. 评分依据：运行状态和 token 用量可见，但缺少集中日志、metrics、trace、事件表接入和稳定测试套件。

6. 当前已实现能力：
   - 每个 Agent 的输入/输出/错误/耗时/attempt 落库。
   - pipeline 用量按 agent/model 聚合。
   - SSE 每 2 秒推送 run 和 agent 状态。
   - 复刻 progress_text 可更新前端灰字进度。
   - QAReviewer 提供基础评估报告。

7. 当前缺失能力：
   - `RunEvent`、`ModelCall`、`ToolCall` 表未接入。
   - 没有 OpenTelemetry、Prometheus、结构化日志或集中错误追踪。
   - 没有离线评测集、golden cases、自动质量指标。
   - 测试存在与当前实现不一致的问题。

8. 是否接入主链路：**部分接入**。状态和用量接入；审计/评估体系不足。

9. 主要风险或限制：
   - 线上问题定位会依赖 `AgentExecution` JSON 和普通日志，缺少跨服务 trace。
   - QA 可能漏检音频/成片时长问题。

10. 建议下一步演进方向：
   - P0/P1：修复当前失败测试，至少恢复 pipeline runtime 和复刻链路回归。
   - P1：写入 `ToolCall / RunEvent / ModelCall`。
   - P1：补充 structured logging 和 trace id 贯穿 HTTP、Agent、模型调用、工具调用。
   - P2：建立 Agent 评测集和自动回归评分。

### 10. 多 Agent 协作模块

1. 标准定义：多 Agent 协作模块负责将任务拆分给多个具备不同职责的 Agent，管理共享上下文、依赖关系、协作协议、任务板、冲突处理和人工介入。

2. 当前项目中的对应实现：系统有阶段 Agent 与两类执行器。顺序 Pipeline 和 LangGraph 体现固定 DAG 协作；动态协作执行器已从当前代码中移除。

3. 关键代码/目录/类/函数：
   - `backend/app/agents/stages/orchestrator.py`
   - `backend/app/agents/stages/prompt_engineer.py`
   - `backend/app/agents/stages/audio_subtitle.py`
   - `backend/app/agents/stages/video_generator.py`
   - `backend/app/agents/stages/video_editor.py`
   - `backend/app/agents/stages/qa_reviewer.py`
   - `backend/app/agents/executors/pipeline.py`
   - `backend/app/agents/executors/langgraph/*`

4. 当前成熟度评分（0-5）：**3**

5. 评分依据：多 Agent 阶段主流程已经可用；但协作仍主要是固定流水线，动态协作和持久化协议未充分产品化。

6. 当前已实现能力：
   - Orchestrator / PromptEngineer / AudioSubtitle / VideoGenerator / VideoEditor / QAReviewer 专责拆分。
   - Audio 和 Video 可并行执行。

7. 当前缺失能力：
   - 多 Agent 通用状态表未接入，协作过程主要落在 `AgentExecution`。
   - 前端 AGENT_ORDER 未包含 `replication_planner` 和 `qa_reviewer`。
   - 没有跨 Agent 冲突解决协议、共享 memory governance、工具调用持久化。

8. 是否接入主链路：**是**。固定多阶段 Agent 是主链路。

9. 主要风险或限制：
   - 多执行器之间行为不完全一致，维护成本较高。

10. 建议下一步演进方向：
   - P1：统一执行器接口和事件模型，确保 Pipeline/LangGraph 的状态语义一致。
   - P1：前端过程面板补齐 `replication_planner` 和 `qa_reviewer`。

## 4. 主链路覆盖分析

### 从用户请求进入系统到任务完成，当前项目覆盖了哪些模块

典型自动模式链路：

1. 用户在 `AutoModeStudio` 输入文本、选择素材、上传参考视频或选择背景模板。
2. 前端调用 `auto_sessions` 或 `pipeline` 相关 API。
3. `ChatAgent` 根据消息选择普通对话或 runtime skill；直接 pipeline 请求则由 `pipeline.py` 创建 `PipelineRun`。
4. 执行器根据 `PIPELINE_ENGINE` 运行 `PipelineExecutor / LangGraphPipelineExecutor`。
5. 阶段 Agent 执行规划、提示词、音频字幕、视频生成、视频剪辑和 QA。
6. `PipelineRun`、`AgentExecution`、`ModelUsage` 持续记录状态与用量。
7. 前端通过 SSE 获取进度。
8. 成片完成后保存到仓库，并生成预览卡和抖音发布草稿。

覆盖模块：

- 感知与输入层：已覆盖。
- 意图理解与任务解析：已覆盖，但参考视频分支存在口径差异。
- 记忆系统：部分覆盖，主要是 Mem0。
- 规划与推理模块：已覆盖。
- 工具调用与执行层：ChatAgent 路径已覆盖。
- 状态管理与工作流编排：已覆盖，但一致性不足。
- 输出生成与结果交付：已覆盖。
- 安全与权限控制：部分覆盖。
- 观测、日志与评估模块：部分覆盖。
- 多 Agent 协作模块：已覆盖。

### 哪些模块在主链路中缺位

- 通用 Agent 状态表：`AgentThread / AgentRun / AgentStep / AgentCheckpoint / ToolCall / ModelCall / RunEvent` 没有成为主链路事实来源。
- 生产级安全：上传校验、产物鉴权、CSRF、rate limit、密钥管理未完整接入。
- 记忆治理：没有用户可控的记忆管理与跨入口一致的记忆注入策略。
- 评估体系：缺少稳定评测集、质量指标和模型输出回归。

### 哪些模块虽然存在，但没有真正发挥作用

- `AgentMemoryService`：有模型和服务，但未见主阶段实际 recall/remember。
- `AgentState` 系列表：模型存在，但未见主流程写入。
- `waiting_prompt_review`：状态和 executor 方法存在，但未见完整 API/UI 闭环。
- `PromptVersion / ModelCall / ToolCall / RunEvent`：表结构存在，但未见主流程作为审计/回放系统使用。

## 5. Gap Analysis

### 缺失模块

- 工具调用持久化审计：没有把 ChatAgent 和 Qwen tool calls 写入 `ToolCall`。
- 生产级访问控制：生成产物和仓库静态文件没有细粒度用户鉴权。
- 评测数据集与质量指标：没有面向 Agent 输出的系统化 eval harness。
- 完整恢复执行：有 snapshot，但服务重启后当前实现是标记 failed，不是恢复。

### 薄弱模块

- 安全与权限：账号隔离基础可用，但上传、静态资源、密钥、CSRF/rate limit 薄弱。
- 记忆系统：Mem0 局部可用，自定义记忆未发挥作用，治理缺失。
- 观测评估：状态记录可用，但审计表、trace、集中日志和 eval 不足。
- 工作流编排：多执行器可用，但状态语义、测试、复刻确认路径不一致。

### 伪完整模块

- Agent 状态体系：`agent_state.py` 表看起来很完整，但主链路仍主要使用 `PipelineRun` / `AgentExecution`。
- 输入安全：上传校验、Agent 文件访问沙箱和 LLM 输出净化都需要在真实入口重新设计并接入。
- Prompt review：状态和方法存在，但没有完整用户确认链路。
- 复刻链路：`ReplicationPlannerAgent` 能产出方案，但旧前端/确认接口/测试仍按 Orchestrator 读取，导致能力表面存在但闭环待修。

### 模块边界不清的问题

- 参考视频处理边界：`analyze_video` runtime skill、`replicate_video` runtime skill、`ReplicationPlannerAgent`、文档里的 Orchestrator 三路径之间需要重新统一。
- 记忆边界：`AgentMemoryService` 和 `Mem0Service` 职责重叠但接入程度不同。
- 状态边界：`PipelineRun/AgentExecution` 与 `AgentRun/AgentStep/AgentCheckpoint` 两套状态模型并存。
- 工具边界：runtime skill 是 ChatAgent 工具，但其内部可直接创建 pipeline run，导致工具执行和工作流执行审计分离。

## 6. 演进建议

### P0：必须优先补齐

- 修复复刻确认链路：统一 `replication_planner` 与 `orchestrator` 的输出读取，修正 `confirm-plan`、前端 `replicationOutput` 和相关测试。
- 修复 pipeline runtime 测试：当前 `test_pipeline_runtime.py` 7 个用例在 launch 阶段返回 422，需优先定位 API 签名、Request 注入或测试 fixture 不一致。
- 接入上传安全校验：所有上传入口必须使用文件名、MIME、大小校验，并限制危险路径。
- 处理密钥风险：确认 `backend/app/config.py` 中非空 API key 形态默认值是否真实；若为真实值，立即轮换并改为环境变量。
- 为 `/generated` 和 `/repository` 增加访问控制或签名 URL，避免用户产物公开暴露。

### P1：建议近期增强

- 统一意图路由：明确 reference video 的 analyze / replicate / generate 三类请求在 ChatAgent、skill、pipeline executor 中的单一入口。
- 接入 `ToolCall / RunEvent / ModelCall`：把工具调用、模型调用、路由决策和异常写入可查询事件表。
- 梳理记忆系统：选择 Mem0 或 KV 作为主记忆层，明确另一个作为派生/补充；补充记忆删除、禁用和查看能力。
- 修复 QA 字段对齐：让 `duration_ms` 与 QA 的秒级字段一致，避免时长/音视频同步检查失效。
- 补齐 `replication_planner`、`qa_reviewer` 前端展示，并统一 AGENT_ORDER。
- 为 Pydantic 请求模型加入枚举、范围和长度约束。

### P2：中长期建设

- 将 `AgentThread / AgentRun / AgentStep / AgentCheckpoint` 作为统一 Agent 状态事实来源，或删除/降级未使用表以降低复杂度。
- 接入 LangGraph 原生 checkpointer，或形成统一 checkpoint/resume 抽象。
- 建立 Agent eval harness：覆盖意图路由、分镜规划、复刻方案、提示词质量、QA 推荐和发布前检查。
- 建立可观测性平台：结构化日志、metrics、trace、dashboard、告警。

## 7. 附录

### 关键术语表

- Agent：执行某类智能任务的组件，本项目包括 `OrchestratorAgent`、`PromptEngineerAgent`、`AudioSubtitleAgent`、`VideoGeneratorAgent`、`VideoEditorAgent`、`QAReviewerAgent`、`ReplicationPlannerAgent` 和 `ChatAgent`。
- Runtime Skill：暴露给 `ChatAgent` 的可调用工具，当前采用 `SKILL.md + schema.json + runtime.py` 结构。
- ToolRegistry：内存中的工具注册与权限检查中心。
- PipelineRun：自动生成任务的 run 级状态记录。
- AgentExecution：每个 Agent 每次执行的输入、输出、状态、错误、耗时和 attempt 记录。
- Checkpoint：当前主要指 `AgentContext.artifacts` 写入 `PipelineRun.artifacts_snapshot` 的快照。
- Mem0：语义记忆层，封装在 `Mem0Service`。
- HITL：Human-in-the-Loop，当前主要体现为复刻方案确认；prompt review 只有部分状态和方法。

### 待确认项列表

- `backend/app/config.py` 中非空 API key 形态默认值是否为真实凭证；若是，需要立即轮换。
- 当前产品入口是否主要通过 ChatAgent skill 启动 pipeline，还是仍大量直接调用 `/api/projects/{project_id}/pipeline`。
- `confirm-plan` 在当前最新前端和默认 `langgraph` 引擎下是否可成功继续复刻生成；代码层面存在 agent 名称不一致风险。
- Mem0 默认存储位置、持久化策略、用户删除/退出机制和隐私合规口径。
- `AgentState` 系列表是准备接入的新规范，还是历史/未来预留。
- `/generated` 和 `/repository` 静态文件公开是否为有意设计。
- `test_pipeline_runtime.py` 返回 422 的直接根因；本报告仅记录现象，未做修复。

### 本次验证记录

- 读取范围：`README.md`、`README.zh-CN.md`、`backend/app/README.md`、`backend/app/agents/README.md`、`backend/app/agents/skills/README.md`、`SYSTEM_COMPARISON.zh-CN.md`、`Developer/*` 关键规范、`backend/app` 关键实现、`frontend/src/components/pipeline/AutoModeStudio.vue`、`backend/tests` 相关测试。
- 命令：`python3 -m pytest backend/tests/test_runtime_skill_loader.py backend/tests/test_chat_agent_streaming.py -q`
  结果：`6 passed`，有 Pydantic v2 `Config` deprecation warning 和 SQLite 外键循环 warning。
- 命令：`python3 -m pytest backend/tests/test_orchestrator_replication_prompt.py -q`
  结果：`13 failed`，主要原因是测试仍调用 `OrchestratorAgent` 上已不存在的复刻相关方法。
- 命令：`python3 -m pytest backend/tests/test_pipeline_runtime.py -q`
  结果：`7 failed`，用例在 pipeline launch 阶段收到 `422 Unprocessable Entity`，需进一步定位。
- 未运行全量测试；未启动前后端服务；未验证真实第三方模型和抖音发布。
