# 文档导航

本目录收纳项目级文档。根目录只保留 `README.md` 与 `README-zh-CN.md` 作为入口；贴近代码的说明仍保留在对应模块旁边，例如 `backend/app/README.md`、`backend/app/agents/README.md` 和 runtime skill 文档。

## 当前事实来源

- [项目中文 README](../README-zh-CN.md)：产品能力、启动方式、主链路和配置说明
- [后端架构说明](../backend/app/README.md)：FastAPI 应用分层和关键模块入口
- [Agent 架构说明](../backend/app/agents/README.md)：Agent 目录结构、执行器和 runtime skill 约定
- [服务层说明](../backend/app/services/SERVICES.md)：外部模型、媒体处理和业务服务边界
- [数据库模型说明](../backend/app/models/MODELS.md)：核心数据表与状态模型说明
- [API 路由说明](../backend/app/routers/ROUTERS.md)：后端路由分组与接口职责

## docs 分区

- [guide/](./guide/)：本地开发、依赖管理、调试运行指南
  - [Python 依赖管理](./guide/python-dependency-management.zh-CN.md)：pyproject.toml / requirements*.txt / uv.lock 职责与新增依赖流程
  - [本地开发与调试](./guide/local-development-debugging.zh-CN.md)：环境准备、后端/前端启动、Docker Compose 联调、常用脚本
- [architecture/](./architecture/)：系统级架构与能力全景
- [api/](./api/)：第三方开发者 API 接入文档
- [plans/](./plans/)：仍可能继续执行的方案或改造计划
- [reports/](./reports/)：流程报告、分析报告和阶段性说明
- [portfolio/](./portfolio/)：简历、面试或对外展示材料
- [archive/](./archive/)：历史规划和历史审计，仅作归档参考
- [development/](./development/)：开发规范、Agent/Skill/数据库设计约定

## 阅读建议

新同学或演示前优先读根目录 `README-zh-CN.md`，再看 `guide/` 下的本地开发指南跑通环境，最后按改动范围进入对应模块文档。`archive/` 中的文档默认不是当前事实来源，引用其中结论前需要回到代码或当前 README 核对。
