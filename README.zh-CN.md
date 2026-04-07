# capy

[English](./README.md)

capy 是一个面向短视频生产的 AI 工作台，目前提供两种工作流：

- `一键生成`：通过对话式界面，直接用脚本和图片素材生成视频
- `手动模式`：按上传、分析、选素材、写提示词、生成、剪辑的步骤逐步完成

当前项目采用 React + Vite 前端和 FastAPI 后端。后端通过多 agent 流水线完成规划、提示词设计、音频字幕生成、分镜视频生成和最终剪辑。

## 功能亮点

- 本地账号体系与 Cookie Session 登录，项目、素材、模板、历史按账号隔离
- 对话式一键生成界面，可在同一页面上传素材、选图、输入脚本并触发生成
- 自动模式支持上传参考视频进入复刻模式，并在执行前确认复刻方案
- 个人中心角色背景模板库，支持预设角色模板、关键词自动生成人设草稿和任务后增量学习
- 手动模式，适合希望逐步控制每个环节的创作者
- 当前默认视频生成模型为 `Seedance 1.5 Pro`
- 多 agent 流水线：
  - `orchestrator`
  - `prompt_engineer`
  - `audio_subtitle`
  - `video_generator`
- `video_editor`
- 项目级仪表盘，可查看 token 消耗与执行进度
- 成片完成后可查看抖音 / YouTube 卡片预览，并将视频保存到仓库
- 支持抖音账号 OAuth 授权；连接账号后，assistant 会自动生成抖音发布草稿，用户确认后再提交发布
- 支持 Qwen Omni、Qwen TTS，以及 WaveSpeed Kling、Volcengine Seedance 等视频生成能力

## 当前支持能力

- 仅输入素材生成提示词：
  当前已经支持基于上传素材进行分析与提示词生成，并正在向“仅素材自动生成完整脚本”这一闭环继续扩展。
- 输入素材和脚本后自动生成分镜提示词：
  这是当前主流水线的核心能力，系统会结合图片内容和脚本内容自动生成 shot 级提示词。
- 根据脚本自动生成音频：
  后端已支持根据脚本文本直接生成配音与字幕时间轴。
- 多个短视频自动拼接并适配平台尺寸：
  系统可对多个短视频片段进行重排、裁剪、拼接、字幕合成，并输出抖音、小红书、B 站等目标平台尺寸。
- 流程可视化与中间产物下载：
  前端支持展示 agent 进度、token 消耗以及中间结果，并允许用户下载提示词、音频和生成片段继续编辑。
- 多平台交付：
  自动模式完成后，系统会额外生成抖音与 YouTube 的卡片化预览，并支持保存成片到本地视频仓库。
- 抖音账号与发布：
  当前已从“固定 `.env` token 发布”切换为“用户级抖音账号授权 + 发布草稿确认 + 按授权账号发布”。

## 架构说明

### 前端

- React 19
- TypeScript
- Vite
- Zustand
- TanStack Query

主要入口文件：

- [frontend/src/App.tsx](./frontend/src/App.tsx)
- [frontend/src/components/pipeline/AutoModeStudio.tsx](./frontend/src/components/pipeline/AutoModeStudio.tsx)
- [frontend/src/components/dashboard/UsageDashboardPage.tsx](./frontend/src/components/dashboard/UsageDashboardPage.tsx)
- [frontend/src/components/dashboard/PersonalCenterPage.tsx](./frontend/src/components/dashboard/PersonalCenterPage.tsx)

### 后端

- FastAPI
- SQLAlchemy Async
- 默认使用 SQLite
- 通过 `httpx` 调用第三方模型服务

主要入口文件：

- [backend/app/main.py](./backend/app/main.py)
- [backend/app/agents/pipeline.py](./backend/app/agents/pipeline.py)
- [backend/app/services/qwen_client.py](./backend/app/services/qwen_client.py)

## Agent 流水线

一键生成流程由 `PipelineExecutor` 统一编排：

1. `OrchestratorAgent`
   负责理解用户需求、解析选中的图片，并生成分镜计划。
2. `PromptEngineerAgent`
   根据分镜计划生成每个镜头的提示词和语音参数。
3. `AudioSubtitleAgent`
   生成配音音频和字幕时间轴。
4. `VideoGeneratorAgent`
   根据图片和提示词逐镜头生成视频片段。
5. `VideoEditorAgent`
   按顺序重排、裁剪并拼接视频片段，同时合入音频和字幕。

核心编排文件：

- [backend/app/agents/pipeline.py](./backend/app/agents/pipeline.py)

集中管理的 system prompt：

- [backend/app/prompts/system_prompts.py](./backend/app/prompts/system_prompts.py)

## 模型与服务提供方

当前项目已接入或预留了以下模型能力：

- `Qwen Omni`
  用于调度、提示词规划、剪辑决策等结构化多模态推理
- `Qwen3 TTS`
  用于文本转语音
- `WaveSpeed Kling`
  在配置后可用于图生视频
- `Seedance 1.5 Pro`
  是当前配置下默认启用的图生视频提供方

相关代码文件：

- [backend/app/services/llm_service.py](./backend/app/services/llm_service.py)
- [backend/app/services/tts_service.py](./backend/app/services/tts_service.py)
- [backend/app/services/video_generator.py](./backend/app/services/video_generator.py)

## 项目结构

```text
vidgen/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   ├── models/
│   │   ├── prompts/
│   │   ├── routers/
│   │   ├── schemas/
│   │   └── services/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── stores/
│   │   └── types/
├── README.md
└── README.zh-CN.md
```

## 本地启动

### 1. 启动后端

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

后端会从项目根目录的 `.env` 读取环境变量。

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

默认开发地址：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

首次进入前端时会先看到登录 / 注册页。首个注册账号会自动成为管理员。

## 环境变量

在 `vidgen/.env` 中填写你要启用的模型服务配置。

常用配置如下：

```env
DATABASE_URL=sqlite+aiosqlite:///./data/vidgen.db
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
QWEN_API_KEY=
QWEN_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_OMNI_MODEL=qwen3-omni-flash
QWEN_TTS_MODEL=qwen3-tts-flash

WAVESPEED_API_KEY=
WAVESPEED_API_URL=https://api.wavespeed.ai/api/v3
KLING_MODEL=kling-v3

ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
SEEDANCE_MODEL=doubao-seedance-1-5-pro-251215

FFMPEG_BIN=ffmpeg
DOUYIN_CLIENT_KEY=
DOUYIN_CLIENT_SECRET=
DOUYIN_REDIRECT_URI=http://127.0.0.1:8000/api/social-accounts/douyin/callback
DOUYIN_DEFAULT_SCOPE=user_info,video.create
FRONTEND_BASE_URL=http://127.0.0.1:5173
```

如果没有配置对应 key，部分服务会根据当前设置自动退回到 mock 实现。

如果要启用“连接抖音账号并发布”，需要在 `.env` 中额外配置：

- `DOUYIN_CLIENT_KEY`
- `DOUYIN_CLIENT_SECRET`
- `DOUYIN_REDIRECT_URI`

此外，你还需要在抖音开放平台为当前应用申请对应的视频发布能力，并把回调地址配置为上面的 `DOUYIN_REDIRECT_URI`。

未配置时，系统仍会展示抖音卡片预览，但不会完成账号授权与发布。

## 当前产品流程

### 一键生成

- 登录账号
- 创建或打开一个项目
- 保持在自动模式对话工作台
- 可选：在个人中心选择一个预设角色，或输入关键词让 AI 自动生成角色背景草稿后保存
- 上传素材文件夹或单张图片
- 可选：上传参考视频进入复刻模式
- 在左侧会话栏中切换历史会话，或新开一个会话继续创作
- 选择图片素材，或直接在对话区附加图片
- 输入脚本并发送
- 如果是复刻模式，先确认或调整系统给出的复刻方案
- 系统自动生成分镜提示词、配音、短视频片段、字幕和最终合成视频
- 在同一界面查看 agent 流程进度
- 下载提示词、音频、视频片段等中间产物继续编辑
- 成片完成后查看抖音 / YouTube 卡片预览
- 成片会自动保存到视频仓库
- 如果当前平台是抖音且账号已连接，assistant 会自动生成一条抖音发布草稿消息
- 用户可在草稿卡片中修改标题、文案、话题和封面标题后确认发布

### 手动模式

- 上传参考视频
- 执行视频分析
- 查看推荐素材
- 编辑提示词
- 生成视频片段
- 进入时间轴剪辑

## 常用命令

```bash
# 后端语法检查
python3 -m compileall backend/app

# 前端生产构建
cd frontend && npm run build
```

## 说明

- 本地开发默认数据库是 SQLite。
- 生成结果、本地素材库和运行数据默认已加入 Git 忽略。
- 当前默认的视频生成路径使用 `Seedance 1.5 Pro`。
- 个人中心当前采用更偏 `capybara` 风格的角色工作台：其他预设角色以图标卡展示，右侧只展示当前选中角色的背景信息用于确认。
- 角色关键词自动生成功能依赖后端 LLM；如果未配置真实模型，会回退到基于内置预设模板的本地生成逻辑。
- 抖音发布目前是“已连接抖音账号后，由 assistant 自动生成发布草稿，用户确认后提交”，不是静默自动发布。
- 抖音接口提交成功只表示 vidgen 已经把内容提交到开放平台，视频仍可能进入平台审核或仅自己可见阶段。
- 当前仓库更偏向本地开发与功能验证，尚未针对生产部署做完整加固。

## License

在公开发布前，请补充你希望使用的开源协议。
