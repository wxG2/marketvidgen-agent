# Python 依赖管理指南

本文说明 vidgen 后端的依赖管理约定，帮助新成员理解各文件的职责以及新增/升级依赖时的推荐流程。

## 文件职责

### `backend/pyproject.toml`

项目元信息与依赖定义的唯一权威来源。

- `[project].dependencies`：runtime 依赖，版本固定（`==`），与 `requirements.txt` 保持一致。
- `[dependency-groups].dev`：开发和 CI 专用工具（pytest、ruff、coverage），不进入生产镜像。
- `[tool.uv]`、`[tool.pytest.ini_options]`、`[tool.ruff]`、`[tool.coverage.*]`：各工具配置统一放在此文件，不散落在多个配置文件中。

### `backend/requirements.txt`

生产/运行依赖清单，内容与 `pyproject.toml` 的 `[project].dependencies` 保持同步。

- Docker 构建时用 `pip install -r requirements.txt` 安装，**不安装 dev 工具**，保持镜像干净。
- 不包含 `-r requirements-dev.txt`，也不包含测试或 lint 工具。

### `backend/requirements-dev.txt`

本地开发和 CI 依赖清单。

```
-r requirements.txt   # 先安装所有 runtime 依赖

pytest==9.0.3
pytest-asyncio==1.3.0
pytest-cov==7.1.0
ruff==0.15.11
```

- 通过 `-r requirements.txt` 引用 runtime 依赖，保证两份文件不重复维护。
- CI 和本地环境统一用 `pip install -r requirements-dev.txt` 安装完整开发环境。

### `backend/uv.lock`

由 `uv lock` 生成的完整依赖解析锁文件。

- 记录所有直接依赖和传递依赖的精确版本，保证跨机器、跨环境可复现。
- 提交到 Git，不手动编辑。
- 每次改动 `pyproject.toml` 后需要运行 `uv lock` 更新。

## 新增或升级依赖

1. 编辑 `backend/pyproject.toml`，在 `[project].dependencies` 中新增或修改版本。
2. 同步更新 `backend/requirements.txt`，保持两者一致。
3. 如果是 dev 工具，同步更新 `backend/requirements-dev.txt`（直接追加，无需改 `requirements.txt`）。
4. 在激活的虚拟环境下运行：
   ```sh
   pip install -r backend/requirements-dev.txt
   ```
5. 运行 `uv lock`（需要安装 uv）更新锁文件：
   ```sh
   cd backend && uv lock
   ```
6. 运行测试确认无破坏性变更：
   ```sh
   ./scripts/backend-test.sh
   ```

## 约定

- **版本固定使用 `==`**，而非 `>=`，避免隐式升级导致环境漂移。
- **不在 `requirements.txt` 中包含开发工具**，保持生产镜像最小化。
- **`pyproject.toml` 是定义，`requirements*.txt` 是派生**——有改动先改 `pyproject.toml`，再同步 txt 文件。
