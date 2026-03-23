# VidGen

[English](./README.md)

VidGen 是一个面向短视频生产的 AI 工作台，目前提供两种工作流：

- `一键生成`：通过对话式界面，直接用脚本和图片素材生成视频
- `手动模式`：按上传、分析、选素材、写提示词、生成、剪辑的步骤逐步完成

当前项目采用 React + Vite 前端和 FastAPI 后端。后端通过多 agent 流水线完成规划、提示词设计、音频字幕生成、分镜视频生成和最终剪辑。

## 功能亮点

- 对话式一键生成界面，可在同一页面上传素材、选图、输入脚本并触发生成
- 手动模式，适合希望逐步控制每个环节的创作者
- 多 agent 流水线：
  - `orchestrator`
  - `prompt_engineer`
  - `audio_subtitle`
  - `video_generator`
  - `video_editor`
- 项目级仪表盘，可查看 token 消耗与执行进度
- 支持 Qwen Omni、Qwen TTS，以及 WaveSpeed Kling、Volcengine Seedance 等视频生成能力

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
- `Seedance`
  作为另一种视频生成提供方

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

## 环境变量

在 `vidgen/.env` 中填写你要启用的模型服务配置。

常用配置如下：

```env
DATABASE_URL=sqlite+aiosqlite:///./data/vidgen.db
QWEN_API_KEY=
QWEN_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_OMNI_MODEL=qwen-omni-turbo
QWEN_TTS_MODEL=qwen3-tts-flash

WAVESPEED_API_KEY=
WAVESPEED_API_URL=https://api.wavespeed.ai/api/v3
KLING_MODEL=kling-v3

ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
SEEDANCE_MODEL=doubao-seedance-1-5-pro-251215

FFMPEG_BIN=ffmpeg
```

如果没有配置对应 key，部分服务会根据当前设置自动退回到 mock 实现。

## 当前产品流程

### 一键生成

- 创建或打开一个项目
- 保持在自动模式对话工作台
- 上传素材文件夹或单张图片
- 在左侧素材栏中选择图片，或直接在对话区附加图片
- 输入脚本并发送
- 在同一界面查看 agent 流程进度

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
- 当前仓库更偏向本地开发与功能验证，尚未针对生产部署做完整加固。

## License

在公开发布前，请补充你希望使用的开源协议。
