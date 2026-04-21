---
name: vidgen-doc-sync
description: Update VidGen Chinese system documentation after implementation changes. Use when code updates affect architecture, APIs, agent workflow, auth, setup, user flows, publishing, delivery, or other documented behavior that must stay aligned with the real codebase.
user-invocable: true
context: direct
---

# VidGen Doc Sync

## Purpose

在修改 VidGen 代码后，同步更新中文系统说明与使用文档，确保文档描述的是“已经实现的真实行为”，而不是计划中的能力。

## Use When

- 代码改动影响 Router / API surface
- 代码改动影响 agent workflow、pipeline stage、chat / skill 行为
- 代码改动影响 auth、账号隔离、个人中心、模板能力
- 代码改动影响自动模式、手动模式、dashboard、会话流或 UI 入口
- 代码改动影响模型配置、provider、环境变量、启动方式
- 代码改动影响结果交付、仓库保存、发布、预览或用户可见行为

## Do Not Use When

- 改动只是内部重构，且不影响任何已文档化行为
- 改动还停留在方案阶段，尚未真正实现
- 当前任务只是临时实验或调试，不应写进长期文档

## Required Inputs

- 实际已经落地的代码改动
- 受影响的文件、路由、模型名、配置项或 UI 入口
- 是否涉及用户可见行为或 setup 变化的判断

## Workflow

1. 先检查真实代码改动，不要根据计划或意图提前写文档。
2. 确认变化是否会影响 `SYSTEM_COMPARISON.zh-CN.md`。
3. 如果变化影响用户使用方式、环境变量、入口位置或 setup，再同步更新 `README.zh-CN.md`。
4. 只 patch 受影响的段落，不要无关重写整份文档。
5. 文案必须实现准确：
   - 写真实 router 名、模型名、env var、UI 入口
   - 不把需要本地配置或第三方凭证的能力写成“开箱即用”
   - 明确区分“当前已实现”和“预留 / 可选”
6. 如果文档发生实质变化，更新 `SYSTEM_COMPARISON.zh-CN.md` 的日期或时间线描述。
7. 完成后复核文档中的行为描述，确保能在代码中找到对应实现。

## Validation

- `SYSTEM_COMPARISON.zh-CN.md` 应只描述当前代码里已经存在的能力。
- 若改动影响用户使用方式或 setup，`README.zh-CN.md` 也必须同步。
- 新增 env var、路由、模型、工作流时，应在文档中使用真实名称。
- 如果某能力仅在 mock 模式、本地假设或额外凭证条件下可用，必须明确说明。
- 不要把未来规划、推测能力或未验证的集成写进文档。

## References

- `SYSTEM_COMPARISON.zh-CN.md`
- `README.zh-CN.md` when the change is user-facing or setup-related

## Content Rules

- Prefer concise bullets over broad marketing language.
- Separate "implemented now" from "reserved or optional".
- For third-party publishing or external providers, explicitly mention config prerequisites.
- If a feature only works in mock mode or with local assumptions, say so.

## Quick Checklist

- `SYSTEM_COMPARISON.zh-CN.md` updated
- `README.zh-CN.md` updated if needed
- New env vars documented if introduced
- New routes/features described with the current behavior, not intended future behavior
