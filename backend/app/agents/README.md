# Agents Architecture

`backend/app/agents` 是 VidGen 后端里专门负责“多阶段智能流程”和“对话式决策”的目录。它不只是放几个 LLM 包装类，而是把以下几类能力拆开管理：

- 阶段 Agent：负责单一生产步骤
- 执行器：负责把多个阶段串成完整流程
- Orchestrator 会话入口：负责自动模式里的会话式交互与能力路由
- Tool / Skill：负责让 Orchestrator 能安全、按需调用视频能力
- Core 基础设施：负责上下文、状态、权限和通用抽象

## 当前目录结构

- `core/`
  Agent 基类和共享抽象。这里放 `BaseAgent`、`AgentContext`、`AgentResult`、`ToolRegistry` 等跨 Agent 共用的设施。
- `stages/`
  视频生产主链路里的单职责阶段 Agent。
  当前包括 `OrchestratorAgent`、`ReplicationPlannerAgent`、`PromptEngineerAgent`、`AudioSubtitleAgent`、`VideoGeneratorAgent`、`VideoEditorAgent`、`QAReviewerAgent`。
- `executors/`
  负责把多个阶段 Agent 串成完整运行时。
  当前包括传统顺序 `PipelineExecutor` 与 `LangGraphPipelineExecutor`。
- `executors/langgraph/`
  LangGraph 细分实现目录：
  `state.py` 管状态 schema，`nodes.py` 管节点逻辑，`executor.py` 管图装配与生命周期。
- `skills/`
  暴露给 `OrchestratorAgent.chat_stream(...)` 的工具技能定义。当前主要是 `analyze_video`、`generate_video`、`remix_video`、`replicate_video` 四类视频能力包装。
  这些 runtime skills 现在采用 `SKILL.md + schema.json + runtime.py` 的目录式结构，并会在启动时自动发现、以 lazy loader 方式注册到 `ToolRegistry`，不再需要在 `main.py` 里逐个硬编码挂载。
  这个目录的 Python runtime skill 约定见 `skills/README.md`；它和 `.claude/.codex` 下的 `SKILL.md` 目录式技能不是同一层抽象。

## 目录之间怎么协作

一条典型自动生成链路大致是这样分工的：

1. `OrchestratorAgent.chat_stream(...)` 判断用户是在继续聊天，还是要触发视频动作
2. `skills/` 把可暴露给 Orchestrator 的能力包装成工具定义
3. `executors/` 决定整条 pipeline 如何运行、暂停、恢复、重试
4. `stages/` 真正执行每一阶段的业务逻辑
5. `core/` 为以上所有部分提供统一上下文、状态记录和工具调用基础设施

普通视频生成链路里，`OrchestratorAgent` 是调度核心，而不只是“第一步分镜”。它会按状态机解析用户消息和图片，推断视频类型、发布平台、风格和目标时长，并把图片理解结果与 `source_images` 传给后续内部节点。状态机迁移会写入当前 `AgentExecution.progress_text`，前端可通过 pipeline SSE 展示这些进度。

## 顶层旧文件为什么还在

当前目录下仍然保留了一批旧的顶层模块文件，比如：

- `backend/app/agents/base.py`
- `backend/app/agents/pipeline.py`
- `backend/app/agents/orchestrator.py`
- `backend/app/agents/prompt_engineer.py`
- `backend/app/agents/audio_subtitle.py`
- `backend/app/agents/video_generator_agent.py`
- `backend/app/agents/video_editor.py`
- `backend/app/agents/qa_reviewer.py`
- `backend/app/agents/tool_registry.py`

这些文件现在主要承担兼容导出（shim）职责，避免旧导入路径立即失效。新代码应优先直接从子包导入，而不是继续依赖这些顶层平铺模块。

推荐导入方式示例：

- `from app.agents.core import ToolRegistry`
- `from app.agents.stages import OrchestratorAgent`
- `from app.agents.executors import LangGraphPipelineExecutor`

## 什么时候把代码放进哪个子目录

- 新增跨 Agent 共用抽象、上下文或注册机制：放 `core/`
- 新增视频生产中的单一阶段节点：放 `stages/`
- 新增一套运行时编排方式或恢复机制：放 `executors/`
- 新增自动模式对话能力：优先扩展 Orchestrator 会话入口或 `skills/`
- 新增供 Orchestrator 调用的工具包装：放 `skills/`

一个简单判断标准是：

- 如果代码关注“某一步怎么做”，通常属于 `stages/`
- 如果代码关注“多步怎么串起来”，通常属于 `executors/`
- 如果代码关注“聊天时该调什么能力”，通常属于 Orchestrator 会话入口或 `skills/`

## 对话式链路补充说明

- `orchestrator_chat.py` 记录了 Orchestrator 当前的流式策略，包括“普通对话真流式、skill 调用保留事件流”的实现边界与异常兜底逻辑
- `skills/README.md` 记录了后端 runtime skill 的设计约定，包括为什么这里保留 `snake_case` 工具名，以及如何用 `RuntimeSkillSpec` 统一描述技能元数据
- `VIDEO_GENERATION_FLOW.md` 记录了当前视频生成链路，从用户消息到 `input_config`，再到各阶段 Agent 的输入输出和 artifact 交接关系
