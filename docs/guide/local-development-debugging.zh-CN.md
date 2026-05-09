# 本地开发与调试指南

本文说明如何在本地启动 vidgen 后端和前端，以及常见的调试流程。

## 环境准备

### 1. 配置环境变量

```sh
cp .env.example .env
```

编辑 `.env`，填写必要的 API Key（`OPENAI_API_KEY` 等）。无需填写的字段可保留默认值，mock 模式下不需要真实 key。

### 2. 创建并激活 Python 虚拟环境

```sh
cd backend
python3 -m venv venv
source venv/bin/activate
```

### 3. 安装开发依赖

```sh
pip install -r requirements-dev.txt
```

或直接使用脚本（脚本会自动进入 `backend/` 目录）：

```sh
./scripts/backend-install-dev.sh
```

## 启动后端

在项目根目录运行：

```sh
./scripts/backend-dev.sh
```

等效于：

```sh
cd backend
uvicorn app.main:app --reload
```

后端默认监听 `http://localhost:8000`，`--reload` 模式下修改代码自动重启。

API 文档地址：
- Swagger UI：`http://localhost:8000/docs`
- ReDoc：`http://localhost:8000/redoc`

## 启动前端

```sh
cd frontend
npm install
npm run dev
```

前端默认监听 `http://localhost:5173`（Vite 默认端口）。

## Docker Compose 联调

如需完整联调（包括向量数据库 Qdrant），使用 Docker Compose：

```sh
docker compose up --build
```

各服务端口：

| 服务 | 端口 |
|------|------|
| backend | 8000 |
| frontend | 80 |
| qdrant | 6333 |

## 常用脚本

脚本均位于 `scripts/`，在项目根目录执行：

| 脚本 | 作用 |
|------|------|
| `./scripts/backend-install-dev.sh` | 安装开发依赖（`requirements-dev.txt`） |
| `./scripts/backend-dev.sh` | 启动后端（uvicorn --reload） |
| `./scripts/backend-test.sh` | 运行 pytest，支持透传参数 |
| `./scripts/backend-lint.sh` | 运行 ruff 检查 `app/`，支持透传参数 |

示例：

```sh
# 只跑健康检查测试
./scripts/backend-test.sh tests/test_health.py

# lint 并自动修复
./scripts/backend-lint.sh --fix
```

## 调试建议

- **优先用 mock 模式跑通主链路**：不依赖真实 API，速度快，适合开发初期验证流程。
- **需要 RAG / Mem0 时再启动 Qdrant**：本地直接 `docker run qdrant/qdrant` 或 `docker compose up qdrant`，不需要完整启动所有服务。
- **测试失败先看 warning 和 `.env` 配置**：多数本地失败来自缺少环境变量或依赖服务未启动，而不是代码逻辑问题。
- **`--reload` 下的热重载只覆盖 Python 文件**：改动 `.env` 或 `pyproject.toml` 后需要手动重启后端。

## 本地生成物说明

以下目录/文件是本地生成物，不属于源码结构，已被 `.gitignore` 排除：

| 路径 | 来源 |
|------|------|
| `backend/venv/` | Python 虚拟环境 |
| `backend/data/` | 运行时数据（上传文件、SQLite DB 等） |
| `backend/.pytest_cache/` | pytest 缓存 |
| `backend/.ruff_cache/` | ruff 缓存 |
| `backend/coverage.xml` | 测试覆盖率报告 |
| `frontend/node_modules/` | npm 依赖 |
| `frontend/dist/` | 前端构建产物 |

遇到奇怪的本地错误，先尝试删除相关缓存目录后重试。
