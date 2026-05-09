# App Architecture

`backend/app` 是 VidGen 后端的主应用包。这里不是单纯放 FastAPI 路由，而是把“接口层、数据层、能力层、Agent 编排层、提示词层”都组织在同一个可组合的应用边界里。

如果把一次典型请求串起来看，通常会经过下面这条链路：

1. `main.py` 建立 FastAPI 生命周期并注册路由
2. `bootstrap.py` 装配模型服务、Agent、runtime skills 和后台清理任务
3. `routers/` 接收 HTTP 请求并做会话级编排
4. `services/` 或 `agents/` 执行业务逻辑 / 模型调用 / 多阶段流程
5. `models/` 持久化数据库状态，`schemas/` 定义输入输出
6. `prompts/` 提供系统提示词和结构化生成提示

## 目录职责

- `main.py`
  FastAPI 应用入口。只保留 lifespan 壳、路由注册和静态文件挂载；具体服务 / Agent 装配已下沉到 `bootstrap.py`。
- `bootstrap.py`
  应用装配层。负责 Mock/真实服务选择、Agent/执行器初始化、runtime skill 注册、Mem0 / AgentMemory 初始化、数据库启动恢复和 artifact 清理任务启动。
- `core/http.py`
  FastAPI HTTP 横切配置。负责全局异常处理、CORS 和 Cookie Session 鉴权中间件。
- `core/config.py`
  集中定义环境变量与运行配置，比如模型 key、目录路径、执行引擎、第三方平台配置。
- `db/session.py`
  数据库引擎、`async_session`、`Base` 和初始化逻辑。
- `core/security.py`
  本地账号体系、Cookie Session、当前用户读取，以及按用户隔离资源的鉴权辅助函数。外部 `/v1` API 使用 `services/api_keys.py` 中的 Bearer API Key 依赖，不走 Cookie。
- `core/`
  应用级基础设施包，收纳配置、HTTP 横切逻辑、安全鉴权和结构化日志。
- `db/`
  数据库连接和 session 生命周期包。
- `routers/`
  API 边界层。每个文件按业务域拆分，例如 `auto_sessions.py`、`pipeline.py`、`materials.py`、`social_accounts.py`、`public_video_jobs.py`。
- `schemas/`
  Pydantic 请求/响应模型，负责 API 入参与返回结构，不承载重业务逻辑。
- `models/`
  SQLAlchemy ORM 模型，描述项目、素材、会话、pipeline run、交付记录、账号授权等持久化状态。
- `services/`
  可复用能力层。这里放第三方模型封装、媒体处理、素材扫描、交付、清理任务、记忆服务等。
- `agents/`
  多阶段智能工作流与对话式 Agent。这里既包含视频生成 pipeline 的阶段 Agent，也包含聊天 Agent、工具注册和执行器。
- `prompts/`
  系统提示词与结构化生成提示模板，供 LLM/Agent 复用。
- `utils/`
  当前放一些轻量工具模块；只有在不适合归到 `services/`、`routers/`、`agents/` 时才建议放这里。
- `data/`
  应用包内的附属数据目录。当前主要逻辑并不依赖这里承载核心业务代码。

## `app` 包里的分层约定

- 路由层只做请求编排、鉴权、参数整理和响应组装，尽量不要把复杂业务直接堆在路由函数里。
- `services/` 更偏“单能力、可复用、可单测”，比如调用 Qwen、FFmpeg、TTS、视频生成器。
- `agents/` 更偏“多步决策和流程编排”，尤其适合需要状态传递、工具选择、阶段衔接的链路。
- `models/` 定义数据库真实状态；`schemas/` 定义 API 读写视图，这两层不要混用成同一层抽象。
- `prompts/` 只放提示词和与提示词强相关的结构约束，不直接承担网络请求与数据库写入。

## 什么时候把代码放进哪个目录

- 新增 HTTP 接口：放 `routers/`
- 新增数据库表或持久化实体：放 `models/`
- 新增 API 请求/响应结构：放 `schemas/`
- 新增模型供应商封装、媒体处理或账号能力：优先放 `services/`
- 新增多阶段 Agent、执行器、对话工具：放 `agents/`
- 新增系统提示词：放 `prompts/`

外部 API facade 的约定：

- API Key 管理路由在 `routers/api_keys.py`，凭证哈希与校验逻辑在 `services/api_keys.py`
- `/v1/video-jobs` 在 `routers/public_video_jobs.py`，只做外部协议、审核续跑和下载鉴权
- 创建项目、导入素材、创建 `PipelineRun`、状态脱敏等复用逻辑放在 `services/public_video_jobs.py`
- 不在 `/v1` 响应中返回本机绝对路径，最终文件只能通过受 API Key 保护的下载接口读取

## 与 `agents/` 的关系

`backend/app/agents` 是 `app` 包里最偏“智能编排”的子系统，但它不是后端的全部。比较稳妥的理解方式是：

- `app/routers` 决定 API 怎么暴露
- `app/services` 决定底层能力怎么调用
- `app/agents` 决定多阶段任务和对话怎么协同

如果你接下来主要在 Agent 体系里改动，继续看 [agents/README.md](./agents/README.md) 会更直接。
