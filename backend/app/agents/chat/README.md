# Chat Agent Streaming Notes

`backend/app/agents/chat/agent.py` 里的 `ChatAgent` 现在采用“两条链路并存”的策略，目的是在保留视频工具能力的同时，让普通 assistant 对话尽可能提供真实流式体验。

## 1. 普通对话为什么走真流式

当用户消息只是继续聊天、润色脚本、聊创意，且当前请求不需要调用视频相关 skill 时，`ChatAgent.chat_stream(...)` 会优先进入 `_stream_direct_llm_reply(...)`：

- 直接调用 `llm.chat_stream(...)`
- 把底层模型返回的文本 chunk 原样转成 `ChatEvent(type="text")`
- 前端通过 `auto_sessions` 路由下的 SSE 逐段收到文本，而不是等待完整回答结束后再统一切块

这条链路的目标很明确：普通 assistant 回复要“能真流就真流”。

## 2. 为什么工具调用仍保留事件流

当用户明确表达某个 runtime skill 的执行意图时，`ChatAgent` 不会走原生 token stream，而是保留工具事件流。当前已内置的 skill 包括：

- `analyze_video`
- `replicate_video`
- `generate_video`
- `force_tool` 指定的强制工具调用

原因不是前端限制，而是当前 DashScope OpenAI-compatible 模式下，`tools` 和 `stream=True` 不能同时启用。也就是说，只要一次请求既要让模型选工具，又要调用工具，再要求底层原生 token stream，就会和当前提供方能力冲突。

因此当前实现采用下面的折中方案：

- 普通 assistant 对话：直接透传 Qwen 原生文本分块
- 显式工具调用：继续发 `tool_call / tool_result / done`

这样前端既能在普通聊天场景里获得更自然的流式体验，也不会破坏现有的视频技能调度链路。

## 3. 当前分流规则

`ChatAgent` 现在已经不再靠硬编码关键字直接判断固定 3 个技能，而是走一条更通用的 runtime skill 路由链：

1. 先从 `ToolRegistry` 读取当前 agent 可见的 runtime skill metadata
2. 按 `required_inputs` 过滤掉当前会话不可执行的 skills
3. 根据每个 skill 的 `routing_hints` 对用户消息做轻量匹配，得到候选集
4. 如果只有一个候选，直接选中
5. 如果多个候选分数接近，只把这些候选 skill 的摘要交给 LLM 做一次轻量路由
6. 命中后才读取该 skill 的 `SKILL.md` 正文、`schema.json` 与 `runtime.py`
7. 未命中时，普通聊天继续走真流式；少数有歧义的请求再回退到 broad tool-calling

这意味着只要在 `backend/app/agents/skills/` 新增符合约定的 runtime skill，并写好 `routing_hints / required_inputs`，`ChatAgent` 就能在无需手工硬编码注册与路由规则的前提下识别它。

## 3.5 按需 Skill 加载

自动路由命中某个 skill 后，请求会进一步走“按需 skill 加载”：

- 启动时不再 import 每个 skill 的 runtime 代码，也不提前读取全部 schema
- 先只用 `SKILL.md` frontmatter metadata 选出一个目标 skill
- 命中后，再读取该 skill 的 `SKILL.md` 正文
- 参数提取时，只对被选中的 skill 加载 `schema.json`
- 如果 `SKILL.md` 直接引用了 `reference.md` 之类支持文件，再按需读取这些文件
- 然后直接执行该 skill 的 `runtime.py`

这样显式视频动作请求的 token 成本，已经从“全量 tool-calling”下降为“单个 skill 的参数提取 + 该 skill 自身执行”。

只有少数没有命中显式 skill、又不能直接走普通聊天的请求，才会回退到 broad tool-calling 路径。

## 4. 异常兜底策略

`_stream_direct_llm_reply(...)` 现在还有一个重要约束：

- 如果流式调用在“首个 chunk 之前”失败，会直接返回通用失败提示
- 如果已经向前端发出过部分 chunk，再发生异常，就直接结束当前流，不再拼接一段 heuristics 文本

这样可以避免出现“前半段是真流回复，后半段突然换成兜底文案”的混合输出。

## 5. 相关入口

- `backend/app/agents/chat/agent.py`
- `backend/app/routers/auto_sessions.py`
- `backend/tests/test_chat_agent_streaming.py`
- `backend/tests/test_qwen_streaming.py`
