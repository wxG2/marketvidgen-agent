# 服务层说明

本目录下每个文件封装一块独立的业务逻辑或外部系统集成，被 Router 层和 Agent 层调用。

大多数有 I/O 或外部依赖的服务采用**抽象基类 + Mock/Real 双实现**的模式，便于测试和环境隔离。

---

## LLM 与 AI 模型

### `llm_service.py`

LLM 调用的统一抽象层。

- `LLMService`（ABC）：定义 `complete`、`stream` 等接口
- `RealLLMService`：调用 Claude（Anthropic）、通义千问等实际模型，根据配置路由
- `MockLLMService`：返回固定内容，用于本地开发和单元测试

### `qwen_client.py`

通义千问 API 的底层 HTTP 客户端封装，处理认证、重试和响应解析，供 `RealLLMService` 调用。

### `tts_service.py`

文字转语音服务。

- `TTSService`（ABC）：定义 `synthesize(text) -> TTSResult` 接口
- `RealTTSService`：调用外部 TTS API（如通义千问语音合成）
- `MockTTSService`：返回空音频文件，用于测试

### `video_analyzer.py`

视频内容 AI 分析服务，用于解析参考视频的场景和风格。

- `VideoAnalyzer`（ABC）：定义 `analyze(video_path, categories) -> AnalysisResult` 接口
- `Qwen3VLAnalyzer`：调用 Qwen3-VL 多模态模型进行视频理解，输出摘要、场景标签和推荐分类
- `MockVideoAnalyzer`：返回预设分析结果

---

## 视频处理

### `video_generator.py`

AI 视频生成服务，对接外部图生视频 API。

- `VideoGenerator`（ABC）：定义 `submit_task`、`poll_status`、`download` 等接口
- `Kling3Generator`：对接可灵（Kling）API
- `SeedanceGenerator`：对接 Seedance API
- `VideoGeneratorRouter`：根据配置（`VIDEO_GENERATOR` 环境变量）将任务路由到具体实现，支持多后端共存

### `video_editor_service.py`

本地视频剪辑合成服务，基于 FFmpeg 实现。

- `VideoEditorService`（ABC）：定义 `compose(clips, config) -> ComposeResult` 接口
- `RealVideoEditorService`：核心能力包括：
  - 多片段拼接（支持 xfade 转场：`fade`/`dissolve`/`slideright`/`slideup`）
  - 字幕叠加（解析 SRT，按时间轴渲染到视频）
  - BGM 混音（按情绪关键词匹配内置 BGM 文件）
  - 视频静音处理
- `MockVideoEditorService`：直接返回第一个输入文件作为结果

### `image_compositor.py`

图像合成服务，用于数字人口播中将人物照片与背景素材合并。

- `ImageCompositor`（ABC）：定义 `composite(model_image, background) -> CompositeTask` 接口
- `FluxInpaintCompositor`：调用 Flux Inpaint 模型，将人物自然融合到背景中
- `MockImageCompositor`：直接返回人物照片作为合成结果

### `lipsync_generator.py`

对口型视频生成服务，用于数字人口播中驱动人物嘴型与音频同步。

- `LipSyncGenerator`（ABC）：定义 `submit`、`poll` 接口
- `LTX23LipSyncGenerator`：调用 LTX-2.3 对口型模型，输入合成图+音频片段，输出口播视频
- `MockLipSyncGenerator`：返回预设视频文件

### `keyframe_extractor.py`

视频关键帧提取服务，用于生成视频缩略图或供 AI 分析使用。

- `KeyframeExtractor`（ABC）
- `FFmpegKeyframeExtractor`：使用 FFmpeg 按时间间隔提取帧
- `MockKeyframeExtractor`：返回空图片列表

---

## 素材与文件

### `material_service.py`

素材库的数据库操作层，被 `materials.py` router 直接调用。

主要功能：
- `scan_materials`：扫描本地目录，批量索引新增文件（跳过已存在）
- `index_uploaded_file`：将单个已上传文件写入 `materials` 表
- `get_categories` / `get_materials_by_category`：分类查询
- `delete_material` / `delete_category`：删除素材及文件

### `media_utils.py`

媒体处理通用工具函数：
- `ensure_local_file(path_or_url)`：如果输入是 URL 则下载到本地临时目录，如果是本地路径则直接返回
- `preprocess_image_for_platform`：按平台要求调整图片尺寸和格式
- `run_subprocess(*args)`：异步执行 shell 命令（封装 `asyncio.create_subprocess_exec`），返回 `(returncode, stdout, stderr)`

### `pipeline_artifact_repository.py`

Pipeline Agent 中间产物入仓服务，被 `BaseAgent.run(...)` 在 `prompt_engineer`、`audio_subtitle`、`video_generator` 成功后调用。

主要功能：
- 将提示词方案、shot 级提示词、配音参数、音频、字幕和分镜视频转成 `RepositoryAsset`
- 本地文件类产物复制到 `VIDEO_REPOSITORY_DIR/artifacts/...`
- 文本类产物写入 `text_content`
- 远程视频 URL 保留为可预览链接，供自动模式右侧栏和个人仓库展示

## 发布与分发

### `video_delivery.py`

视频投递业务逻辑，被 `pipeline.py` router 调用。

主要功能：
- `save_video_to_repository`：将 pipeline 生成的视频文件复制到本地仓库目录，创建 `VideoDelivery` 记录
- `publish_video_to_douyin`：调用抖音开放平台 API 发布视频（先上传，再创建发布任务）
- `build_douyin_publish_draft`：根据 pipeline 输入配置组装抖音发布参数（标题、话题标签等）
- `derive_delivery_title`：从 pipeline 输入中提取视频标题
- `build_platform_preview_cards`：构建供前端展示的平台预览卡片数据

### `social_accounts.py`

抖音 OAuth 2.0 授权流程的业务逻辑，被 `social_accounts.py` router 调用。

主要功能：
- `build_douyin_authorization_url`：生成含 HMAC 签名 state 的抖音授权跳转 URL
- `verify_douyin_oauth_state`：验证回调 state 的签名和有效期（防 CSRF）
- `exchange_douyin_code`：用授权码换取 access/refresh token
- `refresh_douyin_token`：刷新过期的 access token
- `upsert_douyin_social_account`：创建或更新抖音账号绑定记录
- `ensure_active_douyin_account`：自动续期即将过期的 token

---

## Agent 记忆

### `agent_memory.py`

`AgentMemory` 表的 CRUD service，为 Agent 提供跨会话的持久化记忆读写能力。

主要操作：按 `namespace_key + memory_key` 查询/写入/删除记忆条目，支持按 `scope`（`conversation`/`session`/`user`/`organization`）筛选。

### `mem0_service.py`

[Mem0](https://mem0.ai) 向量记忆服务的集成封装。Mem0 是第三方托管的语义记忆系统，`Mem0Service` 封装其 Python SDK，提供语义搜索和记忆更新接口，作为 `agent_memory.py`（关系型）的补充。

---

## 背景模板学习

### `background_template_learning.py`

从 pipeline 运行结果中自动提取偏好并更新背景模板的业务逻辑。

- `learn_background_template_from_run`：分析某次 pipeline 的输入配置和最终结果，调用 LLM 提炼出用户偏好变化，以 patch 形式写入 `BackgroundTemplate.learned_preferences`，并记录到 `BackgroundTemplateLearningLog`

---

## 用量统计

### `usage_service.py`

`UsageRecorder` 类：记录 Agent 调用 LLM 产生的 token 用量到 `model_usages` 表（旧版实现）。新版由 `agent_state.py` 中的 `ModelCall` 模型替代，提供更细粒度的单次调用记录。

---

## 维护

### `artifact_cleanup.py`

定期清理过期的 pipeline 中间产物文件，释放磁盘空间。

- `cleanup_old_artifacts(retention_days)`：查询超过保留期的 `PipelineRun` 记录，删除对应的临时文件目录
- `periodic_artifact_cleanup`：后台定时任务入口，在 `bootstrap.startup_application(...)` 中以 asyncio 任务形式启动
