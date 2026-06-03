---
name: vidgen-auto-github-publish
description: Commit and push VidGen repository changes to GitHub after task-scoped code or documentation updates are complete. Use when the user explicitly asks to commit or push, or when the workflow requires a safe git commit and push as part of task completion.
disable-model-invocation: true
user-invocable: true
context: direct
---

# Vidgen Auto GitHub Publish

## Purpose

在 `vidgen` 仓库代码或项目文档更新完成后，安全地完成一次 Git 提交与推送，让任务可以在“改动已进入远端仓库”的状态下收尾。

## Use When

- 本次任务修改了 `/Users/weixiang/agent/vidgen` 下的代码或项目文档
- 用户明确要求“提交 github”“提交并 push”“更新后推送远端”
- 当前任务的完成定义里包含一次真实的 Git 提交与推送

## Do Not Use When

- 用户明确说这次不要 commit、不要 push、只保留本地修改
- 仓库里存在无法安全区分归属的脏改动
- 改动包含密钥、token、`.env`、本地缓存、无关日志或不应入库的生成文件
- 关键验证失败，且当前任务不允许带着明显失败结果直接推送
- 远端同步需要 `force`、`--no-verify` 或其他破坏性手段才能完成

## Required Inputs

- 当前仓库路径：`/Users/weixiang/agent/vidgen`
- 本次任务涉及的目标文件
- 本次改动摘要
- 至少一项可用的验证结果，或无法验证时的原因说明

## Workflow

1. 先检查当前仓库状态，确认哪些文件被修改。
2. 识别本次任务范围，只提交与当前任务直接相关的文件。
3. 审查高风险文件，重点排除 `.env`、密钥、凭证、本地生成日志、缓存和无关产物。
4. 运行最小必要验证；优先选择与改动直接相关的测试、lint、脚本或静态检查。
5. 生成清晰的提交说明，优先使用 `fix:`、`feat:`、`docs:`、`refactor:` 等明确前缀。
6. 精准暂存本次任务文件，避免把无关改动一并提交。
7. 创建提交；若没有可提交内容，不要制造空提交。
8. 推送到 `origin` 的当前分支；若失败，停止并报告原因。
9. 在最终答复中说明 commit / push 结果、分支、短 hash、验证情况和剩余风险。

## Validation

- 推送前应再次核对已暂存内容，避免误提交。
- 至少运行与改动直接相关的最小验证集合；无法运行时必须明确说明。
- 如果 `git push` 因非快进冲突失败，停止自动推送；不要自动 `force push`。
- 如果 `git commit` 因无变更失败，应明确说明“没有可提交的变更”。
- 如果存在未纳入提交但与任务相关的改动，最终答复里必须说明。

## Commit Message Rules

- 用一句话概括本次改动的用户价值或技术变化
- 不写模糊信息，如 `update`、`fix stuff`、`changes`
- 优先格式：

```text
fix: handle agent memory list payloads safely
feat: add auto session retry support
docs: align skill specs with project conventions
refactor: simplify chat agent memory normalization
```

## Safety Rules

- 不提交 `.env`、密钥、凭证、token、cookies、本地配置秘密
- 不提交无关的生成日志、调试输出、缓存文件
- 不自动执行 `git push --force`
- 不自动执行 `git reset --hard`、`git checkout --` 之类的破坏性命令
- 如果当前任务和已有脏改动冲突，先停下并说明，而不是擅自覆盖

## Output Requirements

最终答复应包含：

- 是否已成功 commit
- 是否已成功 push 到 GitHub
- 当前分支名
- commit hash 的短版本
- 执行过的验证
- 若未完全推送成功，给出阻塞原因

## References

- 仓库根目录：`/Users/weixiang/agent/vidgen`
- 远端仓库：`origin -> https://github.com/wxG2/marketvidgen-agent.git`
- 项目级 Skill 规范：`Developer/Codex-skill-development-spec.md`
