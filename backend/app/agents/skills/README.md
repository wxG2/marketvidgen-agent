# Runtime Skills

`backend/app/agents/skills` 现在采用更接近 Claude 官方 Skill 的目录式设计，但仍然服务 VidGen 后端的 runtime tool 调用，而不是直接给 Claude Code 使用的外部技能市场包。

## 当前目录结构

每个 runtime skill 现在都应放在一个独立目录里，例如：

- `backend/app/agents/skills/analyze-video/`
- `backend/app/agents/skills/generate-video/`
- `backend/app/agents/skills/remix-video/`
- `backend/app/agents/skills/replicate-video/`

每个 skill 目录的约定文件是：

1. `SKILL.md`
   skill 的入口文档。包含 YAML frontmatter 和正文说明。
2. `schema.json`
   单独存放输入 schema。
3. `runtime.py`
   只存放真正可执行的 `create_*_skill(...)`。
4. `reference.md`
   可选补充资料。只有在选中该 skill 后才按需读取。

顶层保留的 `analyze_video.py / generate_video.py / remix_video.py / replicate_video.py` 现在只是兼容 shim，避免旧导入路径立即失效；真实实现已经迁到目录式 skill 包中。

## Progressive Disclosure

这套实现对应 Claude 官方推荐的渐进性披露：

1. 启动时
   `loader.py` 只扫描各 skill 目录下的 `SKILL.md` frontmatter，读取：
   - `name`
   - `description`
   - `required-inputs`
   - `routing-hints`
   - `required-permission`
   - 其他路由所需 metadata
2. 命中某个 skill 后
   Orchestrator 会话入口再读取该 skill 的 `SKILL.md` 正文，把正文说明提供给参数提取器或 action adapter。
3. 真正执行或 fallback 需要 schema 时
   才继续懒加载：
   - `schema.json`
   - `runtime.py`
   - `reference.md` 这类被 `SKILL.md` 直接引用的支持文件

因此启动阶段不再 import 每个 skill 的 runtime 代码，也不会提前读取全部 schema。

## 自动发现与注册

启动时由 `register_runtime_skills(...)` 自动发现并注册 skills：

- 扫描 `backend/app/agents/skills/*/SKILL.md`
- 只根据 frontmatter 构造 metadata-only manifest
- 注册到 `ToolRegistry` 时使用 lazy loader
- 如果 `required-permission` 有值，也会自动授予 `orchestrator`

这意味着新增 skill 的最小步骤变成：

1. 新建一个 skill 目录
2. 写 `SKILL.md`
3. 写 `schema.json`
4. 写 `runtime.py`
5. 补好 `routing-hints / required-inputs`

不需要再改 `main.py` 手工注册。

## Orchestrator 如何使用

`OrchestratorAgent.chat_stream(...)` 对 runtime skill 的调用链路现在是：

- 先读已注册 skill 的 metadata-only manifest
- 按 `required_inputs` 过滤当前会话不可用的 skill
- 用 `routing_hints` 做轻量候选筛选
- 选中后再加载该 skill 的 `SKILL.md` 正文、`schema.json` 和 `runtime.py`
- 参数提取时按需附带 `reference.md`
- 然后直接执行该 skill

因此这里不仅是“tool 注册目录”，也是真正承担 token 控制和 skill 渐进披露的运行时层。

## 命名约定

- skill 目录名和 `SKILL.md` 里的 `name` 使用连字符：
  - `analyze-video`
  - `generate-video`
  - `remix-video`
  - `replicate-video`
- 真实 runtime tool name 仍保持 `snake_case`，通过 frontmatter 里的 `tool-name` 指定：
  - `analyze_video`
  - `generate_video`
  - `remix_video`
  - `replicate_video`

这样既对齐 Claude 官方的 skill 命名风格，也保留现有 function-calling tool 名的稳定性。
