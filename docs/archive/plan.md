# vidgen 项目 vs AI Agents 算法实习生 JD 差距分析与改进建议（历史规划）

> 状态说明：本文是早期面向 JD 的差距分析和改进计划，部分条目已经被后续实现覆盖，部分判断已过期。当前项目事实请以根目录 `README-zh-CN.md`、代码和测试为准；本文仅作为历史规划归档参考。

## 背景

用户希望凭借 vidgen agent 项目面试「AI Agents 算法实习生」岗位。以下对照 JD 6 条要求，逐条评估项目现状，标注 **已覆盖 / 部分覆盖 / 未覆盖**，并给出优先级排序的改进建议。

---

## 一、JD 逐条对照评估

### 1. Python + 强类型语言 + Git + Linux + 容器化

| 子项 | 状态 | 说明 |
|------|------|------|
| Python | ✅ 已覆盖 | 全栈 Python，FastAPI + SQLAlchemy async，代码质量不错 |
| 强类型语言 | ❌ 未覆盖 | 项目无 Go/Java/C++/Rust 代码 |
| Git | ✅ 已覆盖 | 正常使用 |
| Linux | ✅ 已覆盖 | FFmpeg、服务部署等 |
| 容器化 | ❌ 未覆盖 | **无 Dockerfile、无 docker-compose** |

**改进建议：**
- **[P0] 添加 Dockerfile + docker-compose.yml**：这是最低成本高回报的改进，面试官会直接看有没有
- docker-compose 编排：backend + frontend + qdrant（Mem0依赖）
- 多阶段构建，生产镜像精简

### 2. Agent 框架与范式（核心考察项）

| 子项 | 状态 | 说明 |
|------|------|------|
| LangGraph | ✅ 已覆盖 | `StateGraph` 编排多 Agent，条件路由、并行扇出、QA重试循环 |
| LangChain | ✅ 已覆盖 | langchain-core + langchain-openai 作为基础 |
| MCP 协议 | ❌ 未覆盖 | **零实现，JD 明确要求** |
| ReAct | ⚠️ 部分覆盖 | ChatAgent 有 tool-calling loop，但未显式实现标准 ReAct（Thought→Action→Observation 循环） |
| RAG | ⚠️ 部分覆盖 | `RetrievalDocument` 模型存在但无实际检索实现；Mem0 提供语义记忆但不是通用 RAG |
| Function Calling | ✅ 已覆盖 | ToolRegistry 双格式（Claude/OpenAI），ChatAgent 使用 OpenAI function calling |
| Planner | ✅ 已覆盖 | OrchestratorAgent 作为 Planner 生成分镜计划 |
| Memory | ✅ 已覆盖 | 双层记忆：AgentMemory（KV结构化）+ Mem0（语义向量） |

**改进建议：**
- **[P0] 添加 MCP Server/Client 实现**：这是 JD 明确要求的差异化能力
  - 方案：将 ToolRegistry 中的工具通过 MCP 协议暴露为 MCP Server
  - 或：实现一个 MCP Client，连接外部 MCP 工具服务器（如文件系统、数据库）
  - 推荐用 `mcp` Python SDK，实现 2-3 个 MCP tool（如素材检索、视频状态查询）
- **[P1] 补充 RAG 实现**：
  - 基于已有的 `RetrievalDocument` 模型 + Qdrant，实现素材/脚本的向量检索
  - 场景：用户描述需求 → 检索相似历史项目的 prompt/分镜方案作为参考
  - 这样就形成完整链路：RAG 检索 → 上下文增强 → Agent 生成
- **[P1] 显式实现 ReAct 模式**：
  - 在 ChatAgent 或新增一个 ResearchAgent 中，显式记录 Thought → Action → Observation 循环
  - 将推理轨迹持久化到 `AgentStep` 表，面试时可以展示 Agent 的推理过程

### 3. 大模型训练与推理

| 子项 | 状态 | 说明 |
|------|------|------|
| SFT/LoRA/QLoRA | ❌ 未覆盖 | 纯 API 调用，无任何模型训练 |
| 后训练与对齐 | ❌ 未覆盖 | — |
| vLLM/TGI 推理服务化 | ❌ 未覆盖 | 使用 DashScope API |

**改进建议：**
- **[P1] 添加一个轻量 SFT 微调模块**：
  - 场景：收集 QA Reviewer 的反馈数据，微调一个小模型（如 Qwen2.5-1.5B）做视频质量打分
  - 或：基于历史 prompt → 优质视频的配对数据，LoRA 微调 prompt 改写模型
  - 用 Transformers + PEFT 实现，代码量不大但直接命中 JD 第 3 条
- **[P2] 添加 vLLM 本地推理选项**：
  - 在 config.py 中添加 `LLM_BACKEND: Literal["dashscope", "vllm"]`
  - 实现一个 vLLM 兼容的 client，通过 OpenAI 兼容 API 连接本地 vLLM 服务
  - docker-compose 中加一个 vLLM 服务容器

### 4. 实验设计与问题定位能力（Agent 可观测性）

| 子项 | 状态 | 说明 |
|------|------|------|
| Agent 轨迹分析 | ⚠️ 部分覆盖 | AgentExecution + AgentStep 记录存在，但无分析/可视化工具 |
| 工具失败率 | ⚠️ 部分覆盖 | ToolCall 模型记录调用结果，但无聚合统计 |
| 检索召回 | ❌ 未覆盖 | 无 RAG，无召回率指标 |
| 推理路径分析 | ⚠️ 部分覆盖 | 有 trace_id 但无链路追踪集成 |

**改进建议：**
- **[P0] 添加 Agent 可观测性仪表板**：
  - 新增 `/api/analytics/` 路由，提供：
    - 各 Agent 平均耗时、成功率、重试率
    - 工具调用失败率统计（按工具类型分组）
    - Token 消耗趋势（已有 UsageRecorder 数据）
    - QA 打回率和打回原因分布
  - 前端加一个简单的 Dashboard 页面展示这些指标
- **[P1] 集成 OpenTelemetry / LangSmith**：
  - 添加 `opentelemetry-api` + `opentelemetry-sdk`
  - 在 BaseAgent.run() 中添加 span，形成完整的 pipeline trace
  - 或集成 LangSmith（LangGraph 原生支持），一行配置即可

### 5. 工程化与平台化能力

| 子项 | 状态 | 说明 |
|------|------|------|
| API/SDK 设计 | ✅ 已覆盖 | FastAPI + 18 个 Router，结构清晰 |
| 异步任务编排 | ✅ 已覆盖 | 全 async，Semaphore 并发控制，但无外部任务队列 |
| 状态管理 | ✅ 已覆盖 | LangGraph state + DB checkpoint + pipeline status |
| 日志与链路追踪 | ❌ 未覆盖 | 仅 stdlib logging，**无结构化日志、无 tracing** |
| 单元测试 | ⚠️ 部分覆盖 | 仅 ~11 个测试用例，覆盖率极低 |
| 可维护代码 | ✅ 已覆盖 | 清晰的分层架构、配置管理、mock 模式 |

**改进建议：**
- **[P0] 补充单元测试到 30+ 用例**：
  - 重点测试：ToolRegistry（权限检查、工具发现）、LangGraph 状态流转、Agent 取消/超时
  - Pipeline 集成测试（mock 所有外部服务，验证完整流程）
  - 测试覆盖率报告（pytest-cov）
- **[P0] 添加结构化日志**：
  - 引入 `structlog`，JSON 格式输出
  - 在每个请求中注入 request_id / trace_id
  - Agent 执行日志中包含 agent_name、pipeline_id、step_index
- **[P1] 添加 CI/CD（GitHub Actions）**：
  - lint（ruff）+ type check（mypy）+ test + build docker image
  - 面试时展示 green CI badge 很加分

### 6. 沟通与用户体验

| 子项 | 状态 | 说明 |
|------|------|------|
| 前端交互 | ✅ 已覆盖 | Vue 3 + SSE 实时进度 |
| 用户体验 | ✅ 已覆盖 | HITL 审核、实时进度流 |

**基本达标，无需大改。**

---

## 二、改进优先级总结

按面试回报率（投入产出比）排序：

| 优先级 | 改进项 | 预计工作量 | 面试加分 | 命中 JD 条目 |
|--------|--------|-----------|---------|-------------|
| **P0** | MCP Server/Client 实现 | 2-3 天 | ⭐⭐⭐⭐⭐ | 第 2 条（MCP） |
| **P0** | Dockerfile + docker-compose | 0.5 天 | ⭐⭐⭐⭐ | 第 1 条（容器化） |
| **P0** | Agent 可观测性指标 API + 仪表板 | 2 天 | ⭐⭐⭐⭐ | 第 4 条（实验分析） |
| **P0** | 补充单元测试 30+ | 1-2 天 | ⭐⭐⭐ | 第 5 条（测试） |
| **P0** | 结构化日志 + trace_id 贯穿 | 1 天 | ⭐⭐⭐ | 第 5 条（链路追踪） |
| **P1** | RAG 检索增强（素材/历史方案） | 2 天 | ⭐⭐⭐⭐ | 第 2 条（RAG） |
| **P1** | 显式 ReAct 循环 + 轨迹记录 | 1 天 | ⭐⭐⭐ | 第 2 条（ReAct） |
| **P1** | LoRA 微调质量评分模型 | 2-3 天 | ⭐⭐⭐⭐ | 第 3 条（训练） |
| **P1** | GitHub Actions CI/CD | 0.5 天 | ⭐⭐⭐ | 第 5 条（工程化） |
| **P2** | vLLM 本地推理支持 | 1 天 | ⭐⭐ | 第 3 条（推理） |
| **P2** | OpenTelemetry / LangSmith 集成 | 1 天 | ⭐⭐ | 第 4、5 条 |

---

## 三、面试官视角的项目亮点（已有，需重点准备话术）

1. **LangGraph 多 Agent 编排**：条件路由、并行扇出、QA 重试循环 — 直接展示 DAG 图
2. **双层记忆架构**：结构化 KV + 语义向量（Mem0），可以对比两者的使用场景
3. **HITL 人机协作**：pipeline 暂停/恢复机制，展示对 Agent 可控性的思考
4. **ToolRegistry 双格式**：同时支持 Claude 和 OpenAI tool format，展示对多模型生态的理解
5. **Runtime Skill 插件系统**：类 Claude Code 的 SKILL.md + 懒加载设计
6. **完整的状态持久化**：AgentExecution、ToolCall、ModelCall 全链路记录

---

## 四、面试准备建议

1. **准备一个完整的 demo 视频**：从用户输入到最终视频产出的全流程录屏
2. **画一张清晰的架构图**：展示 Agent 之间的数据流和控制流
3. **准备 Agent 失败案例分析**：QA 打回 → 重试 → 成功的真实轨迹，展示问题定位能力
4. **对比方案准备**：为什么选 LangGraph 而不是 CrewAI/AutoGen？为什么双层记忆而不是纯 RAG？

---

## 五、实施计划

如果用户同意，按以下顺序实施改进：

### Phase 1（基础工程化，1-2 天）
1. 添加 Dockerfile + docker-compose.yml
2. 添加 structlog 结构化日志 + trace_id 贯穿
3. 添加 GitHub Actions CI（lint + test）
4. 补充核心单元测试

### Phase 2（Agent 能力补强，3-4 天）
5. 实现 MCP Server（暴露 ToolRegistry 工具）
6. 实现 RAG 检索（基于 Qdrant + 历史项目数据）
7. 添加 Agent 可观测性 API + 前端 Dashboard

### Phase 3（模型训练加分项，2-3 天）
8. LoRA 微调视频质量评分模型
9. 显式 ReAct 循环实现 + 轨迹可视化
