---
title: VidGen Skill Development Spec
summary: 面向当前仓库 `.claude/skills` 与 `.codex/skills` 的统一 Skill 设计规范，综合 Anthropic 官方 Skill 约束与 Waylandz Agent Skills 格式规范整理。
---

本文档基于以下两类资料整理，并收敛成当前 `vidgen` 仓库可直接执行的 Skill 规范：

- Anthropic / Claude 官方关于 Agent Skills、Prompt 设计、Tool Use、Subagent 的约束
- Waylandz《AI Agent Architecture》Chapter 5.11 提到的 cross-platform Agent Skills 格式规范

它的目标不是逐字转述外部文档，而是回答一个更实际的问题：

> 在当前项目里，新 Skill 应该长什么样、放在哪里、写哪些 frontmatter、什么时候该拆成脚本或 references、以及哪些字段只是“保留兼容”而不是“当前运行时已生效”。

## 适用范围

- 适用于当前仓库内新增或重构 Skill 的目录设计
- 适用于 `.claude/skills/` 与 `.codex/skills/` 下的 `SKILL.md`
- 适用于 Skill 的主说明、参考材料、模板文件、示例和脚本组织
- 不替代具体运行时实现；如果某个平台当前尚未消费某个字段，以平台实际行为为准

## 一句话定义

> Skill = System Prompt + Tool Whitelist + Invocation Constraints + Workflow packaged together

在当前项目里，可以进一步理解成：

> Skill = 一个可按需加载的能力包，负责告诉 Agent “什么时候应该用我、用了我该怎么做、最多能调哪些工具、失败时怎么兜底”。

## 核心设计原则

### 1. 先解决路由，再解决执行

Skill 首先要让模型正确命中，之后才谈执行质量。

因此：

- `name` 和 `description` 优先承担“发现 / 路由”职责
- `SKILL.md` 正文优先承担“执行 / 决策”职责
- 详细规范、样例、模板和脚本不要一股脑塞进正文

### 2. Skill 必须是目录，不是散文件

按 Waylandz 的 Skill 格式规范，Skill 的基本单位应是一个目录，入口文件固定为 `SKILL.md`。

推荐结构：

```text
my-skill/
├── SKILL.md
├── template.md
├── reference.md
├── examples/
│   └── sample.md
└── scripts/
    └── helper.py
```

在本仓库中，新的 Skill 一律按目录组织：

```text
/Users/weixiang/agent/vidgen/backend/app/agents/skills/<skill-name>/SKILL.md

```

当前仓库里已有一个历史遗留例外：

- `.claude/skills/SKILL.md`

它属于旧的平铺布局。后续如果继续维护这个 Skill，优先把它迁移到独立子目录，而不是继续复制这种写法。

### 3. 渐进披露优先

Skill 应按三层披露信息：

1. frontmatter：始终最先被读取
2. `SKILL.md` 正文：命中 Skill 后读取
3. `reference.md`、`examples/`、`scripts/`、模板文件：只在需要时读取

这意味着：

- `SKILL.md` 要像作战手册，不要写成百科全书
- 参考资料优先拆出去
- 可重复、低自由度、易出错的操作优先变成脚本

### 4. 最小权限原则

Skill 不该默认拥有“能做所有事”的能力。设计时应把工具范围、调用边界、触发方式写清楚。

经验规则：

- 研究 / 评审 / 总结类 Skill：自由度可以高一些
- 写库、发版、推送、批量改写、外部副作用类 Skill：自由度要低，验证要强

### 5. 单一职责

一个 Skill 只解决一类高频任务，不要把完全不同的任务揉进同一个 Skill。

推荐：

- `vidgen-doc-sync`
- `vidgen-auto-github-publish`
- `db-migration-review`

避免：

- `general-helper`
- `backend-and-frontend-and-deploy`
- `utils`

## 当前项目的 Skill 目录约定

### 目录放置位置

- Claude / Claude Code 风格 Skill：放 `.claude/skills/`
- Codex 风格 Skill：放 `.codex/skills/`

### Python Runtime Skill 的特殊约定

`backend/app/agents/skills/` 里的 skill 不属于文件系统 `SKILL.md` 技能包，而是后端运行时实际注册给 `ChatAgent` 的 Python skill。

这类 skill 也应遵守同样的设计原则：

- 要有清晰的路由说明
- 要有明确的适用边界和禁用边界
- 要声明必需输入和验证规则
- 要表达调用控制语义

但它们有两个实现层面的差异：

1. 名称继续使用 `snake_case`
   原因是它们直接映射到 function-calling / tool name，例如 `analyze_video`、`generate_video`、`replicate_video`。
2. 元数据通过 Python 结构承载
   当前项目使用 `RuntimeSkillSpec` 统一承载 `description / use_when / do_not_use_when / required_inputs / validation_rules / invocation policy`，而不是通过 `SKILL.md` frontmatter 表达。

### 每个 Skill 的建议结构

```text
<skill-name>/
├── SKILL.md
├── reference.md
├── template.md
├── examples/
│   └── sample.md
└── scripts/
    └── validate.py
```

约定说明：

- `SKILL.md`
  必须存在。负责路由、边界、工作流和校验要求。
- `reference.md`
  可选。放 schema、接口表、领域规则、FAQ 之类的事实材料。
- `template.md`
  可选。放输出模板、固定结构、草稿骨架。
- `examples/`
  可选。放输入输出样例，帮助模型学会目标格式。
- `scripts/`
  可选。放稳定执行、重复使用、需要把行为“锁死”的脚本。

只有模型执行任务时必须看到的内容，才应该进入 Skill 目录。纯面向人类的设计说明，优先留在 `docs/development/` 或普通项目文档中。

## `SKILL.md` 格式规范

`SKILL.md` 采用：

- YAML frontmatter
- Markdown 正文

标准形式：

```markdown
---
name: my-skill
description: What this skill does, when to use it.
allowed-tools: Read, Grep, Glob
disable-model-invocation: false
user-invocable: true
context: direct
agent: default
---

# My Skill

## Purpose

...
```

## Frontmatter 字段规范

### 必填字段

#### `name`

- 当前项目要求显式填写，不依赖“默认等于目录名”
- 仅允许小写字母、数字、连字符
- 建议控制在 64 个字符内
- 必须稳定、可检索、可讨论

推荐：

- `vidgen-doc-sync`
- `vidgen-auto-github-publish`
- `prompt-audit`

避免：

- `helper`
- `misc`
- `tools`

#### `description`

- 当前项目要求必填
- 必须同时说明“做什么”和“什么时候使用”
- 优先写成路由语句，而不是背景介绍
- 不要写成 “I can...” 或 “你可以让我...”

推荐模板：

```yaml
description: Update VidGen system documentation after code changes. Use when backend, frontend, agent workflow, API behavior, setup, or delivery behavior has changed and docs must stay implementation-accurate.
```

### 可选字段

#### `allowed-tools`

- 含义：工具白名单
- 用途：限制 Skill 可用的工具范围，符合最小权限原则
- 当前项目约定：可以写，但只有在对应运行时 / 适配器支持时才会真正生效

适用场景：

- Skill 带明显副作用
- Skill 只需要很小的一组工具
- 希望避免模型在本任务里乱用无关工具

#### `disable-model-invocation`

- 含义：若为 `true`，只允许用户显式触发，不允许模型自动路由触发
- 适合：
  - 发版
  - 推送 GitHub
  - 数据库迁移
  - 任何“误触发代价高”的操作

#### `user-invocable`

- 含义：若为 `false`，用户不直接在菜单或命令里触发，只允许模型内部按需触发
- 适合：
  - 纯背景知识 Skill
  - 模型内部辅助规范
  - 不希望暴露给最终操作者的内部流程

#### `context`

- 常见值：`direct`、`fork`
- 含义：是否在独立上下文 / 子代理里执行
- 当前项目约定：只有当平台真正支持子代理 / fork 上下文时才依赖该字段；否则它只作为设计意图保留

#### `agent`

- 含义：指定执行该 Skill 的子代理类型
- 当前项目约定：仅在运行平台支持 agent 类型选择时使用
- 设计上只在“确实需要独立角色或委派语义”时填写

## 当前项目的字段兼容约定

这是本仓库最重要的一条补充说明：

- `name`
- `description`

这两个字段是当前所有 Skill 都应稳定提供的最小公共子集。

下面这些字段属于“推荐的跨平台标准字段”：

- `allowed-tools`
- `disable-model-invocation`
- `user-invocable`
- `context`
- `agent`

它们可以写入文档和 frontmatter，以保持 Skill 设计的跨平台一致性；但在当前仓库里，是否真正生效，取决于对应平台和适配器是否消费这些字段。

换句话说：

- 文档设计可以统一按这套字段来写
- 运行时行为必须以当前平台真实支持情况为准
- 不要只因为写了 `allowed-tools`，就假设所有执行环境都已经自动完成权限限制

## `SKILL.md` 正文推荐结构

建议正文采用下面的稳定结构：

```markdown
# Skill Name

## Purpose

一句话说明目标。

## Use When

- 典型触发场景
- 用户常见表达

## Do Not Use When

- 不适用边界
- 应改用别的 skill / tool / flow 的情况

## Required Inputs

- 必要输入
- 缺失时如何补齐

## Workflow

1. ...
2. ...
3. ...

## Validation

- 如何自检
- 失败后如何回退或重试

## References

- 什么时候读取哪个参考文件
```

写法要求：

- 以步骤、检查项、决策规则为主
- 少写大段背景介绍
- 对脆弱操作直接给 checklist
- 对格式敏感的输出直接给模板
- 对需要引用事实的任务，明确告诉模型去读哪个 reference 文件

## Invocation Control 设计规范

Waylandz 规范里有两条非常重要的调用控制规则，这里直接落成项目约定：

### 1. 只允许用户触发

如果 Skill 带明显副作用，建议写：

```yaml
disable-model-invocation: true
```

典型场景：

- 提交并推送仓库
- 执行数据库 schema 变更
- 触发真实外部发布
- 删除 / 覆盖敏感资源

### 2. 只允许模型内部触发

如果 Skill 是内部知识包或后台辅助流程，建议写：

```yaml
user-invocable: false
```

典型场景：

- 专门的格式审计规则
- 背景知识型 Skill
- 用户无需主动感知的辅助路由逻辑

## 工具与权限设计规范

Skill 设计时，优先决定“该不该用工具”，然后才是“正文怎么写”。

规则如下：

- 只给完成任务所需的最小工具集
- 工具描述要能回答“何时调用、输入什么、返回什么”
- 不要鼓励“能不用工具也随便用工具”
- 结构严格的参数优先靠 schema 或脚本保证，不靠自然语言兜底
- 如果平台还没实现工具白名单强制限制，正文里也要重复写出禁止事项

一句经验规则：

> 执行交给脚本，判断交给正文，事实查找交给 reference，权限交给 allowlist。

## 何时拆到 `scripts/`

满足下列任一条件时，优先把动作写成脚本而不是只写自然语言：

- 逻辑会重复出现
- 手工执行容易出错
- 需要稳定且可验证的结果
- 行为自由度应该被锁死
- 输出需要程序化校验

典型例子：

- schema 校验
- 批量文件检查
- 生成固定格式产物
- 运行最小验证集

## 何时拆到 `reference.md` / `examples/`

适合放 `reference.md`：

- API 约束
- schema
- 字段说明
- 领域规则
- FAQ

适合放 `examples/`：

- 输入输出对照样例
- 成功 / 失败案例
- 风格模板

不要把所有 reference 内容直接塞到 `SKILL.md` 里。

## Skill 与 Subagent 的边界

更适合做成 Skill：

- 同一类知识或流程会反复复用
- 目标是“补充路由与执行规则”
- 不一定需要独立权限或独立记忆

更适合做成 Subagent：

- 需要独立工具权限
- 需要独立模型或角色
- 需要独立 memory
- 需要明确 handoff / delegation 语义

如果某个能力同时需要“专门知识 + 独立执行边界”，优先做成：

- Subagent
- 再给这个 Subagent 预加载 Skill

## 验证清单

每个 Skill 至少要过下面这些检查：

1. 路由命中测试：典型请求能否命中正确 Skill
2. 误触发测试：相似请求是否会误命中
3. Happy path：主路径能否稳定完成
4. 缺输入场景：缺文件、缺参数、缺上下文时是否有补救策略
5. Validation 回路：失败后是否有修复并重试规则
6. 工具边界：无权限或参数不符时行为是否可控
7. 安全审计：是否存在越权读写、敏感信息泄露、隐式网络依赖

推荐最低交付标准：

- 3 个正向样例
- 2 个反向样例
- 1 个失败恢复样例

## 安全规范

Skill 应被当作“可执行的软件包”来审查，而不是普通文档。

必须审查：

- `SKILL.md`
- `scripts/`
- 模板文件
- 示例文件
- 外部 URL 依赖

重点关注：

- 是否读取了超出任务范围的文件
- 是否可能把敏感信息发往外部系统
- 是否引导模型执行不必要的高风险命令
- 是否让模型在权限不清晰的情况下默认做 destructive 操作

## 当前仓库的附加约定

### 1. 新 Skill 必须采用目录式布局

新建 Skill 时，不再接受“单个 `SKILL.md` 直接平铺在 `.claude/skills/` 根目录”的写法。

### 2. 先写 `description`，再写正文

如果 `description` 不能清楚回答“什么时候用”，正文写得再完整也很难被正确命中。

### 3. 正文优先写动作，不写宣传

Skill 文案应偏“可执行说明”，不要写成产品介绍、设计说明会或人类 PR 文案。

### 4. 脆弱任务优先加 `Validation`

涉及 Git、数据库、发布、批量改写、代码生成的 Skill，必须写清：

- 怎么验证
- 验证失败怎么办
- 什么时候必须停止

### 5. 平台未支持的字段也允许保留

只要字段遵循本规范，就允许保留在 frontmatter 中，用来表达设计意图和跨平台兼容性；但必须在实现文档里明确“该字段当前是否被消费”。

## 推荐模板

```markdown
---
name: my-skill
description: Handle X. Use when the task involves Y, mentions Z, or requires W workflow.
allowed-tools: Read, Grep, Glob
disable-model-invocation: false
user-invocable: true
context: direct
agent: default
---

# My Skill

## Purpose

完成 X 任务，并确保结果满足 Y 约束。

## Use When

- 用户要求 X
- 输入包含 Z 文件

## Do Not Use When

- 任务其实属于 A
- 只需要一次性回答，不需要领域流程

## Required Inputs

- 输入 1
- 输入 2

## Workflow

1. 识别任务类型
2. 读取最小必要文件
3. 执行主流程
4. 运行验证
5. 仅在验证通过后输出结果

## Validation

- 若验证失败，修复后重试
- 无法验证时明确说明风险

## References

- API 细节：`reference.md`
- 示例：`examples/sample.md`
```

## 参考资料

- Anthropic Prompt engineering overview
  https://docs.anthropic.com/en/docs/prompt-engineering
- Anthropic Prompting best practices
  https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- Anthropic Agent Skills overview
  https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Anthropic Skill authoring best practices
  https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Anthropic Tool use with Claude
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- Claude Code custom subagents
  https://code.claude.com/docs/en/sub-agents
- Waylandz AI Agent Book, Chapter 5 Skills System
  https://www.waylandz.com/ai-agent-book-en/chapter-05-skills-system/
