# VidGen

[中文说明](./README-zh-CN.md)

Documentation map: [docs/README-zh-CN.md](./docs/README-zh-CN.md)

VidGen is an AI-assisted short-video production workspace with two workflows:

- `一键生成` for chat-style video generation from script + images
- `手动模式` for step-by-step upload, analysis, material selection, prompt editing, generation, and timeline editing

The current stack uses a Vue + Vite frontend and a FastAPI backend. The backend orchestrates multiple agents for planning, prompt design, audio/subtitle generation, clip generation, and final editing.

## Highlights

- Chat-style auto mode for uploading materials, selecting images, entering a script, and triggering generation in one place
- Manual mode for creators who want full control over upload, analysis, material selection, and editing
- Default video generation provider is `Seedance 1.5 Pro`
- Multi-agent pipeline:
  - `orchestrator`
  - `prompt_engineer`
  - `audio_subtitle`
  - `video_generator`
  - `video_editor`
- Project dashboard for token usage and run progress
- Support for Qwen Omni, Qwen TTS, and video generation providers such as WaveSpeed Kling or Volcengine Seedance

## Supported Capabilities

- Material-only prompt generation:
  The project can analyze uploaded materials and generate prompts, and it is being extended toward a fully automatic "materials to script" flow.
- Material + script to storyboard prompts:
  The main pipeline already supports taking selected images and a user script, then generating shot-level prompts automatically.
- Script to audio:
  The backend can generate TTS audio and subtitle timing directly from the script.
- Multi-clip editing and platform adaptation:
  Multiple generated clips can be reordered, trimmed, merged with subtitles, and exported to target platform sizes such as Douyin, Xiaohongshu, and Bilibili.
- Full workflow visualization and downloadable intermediates:
  The UI exposes pipeline progress, token usage, and downloadable intermediate assets so users can keep editing control.

## Architecture

### Frontend

- Vue 3
- TypeScript
- Vite
- TanStack Query

Important entry points:

- [frontend/src/main.ts](./frontend/src/main.ts)
- [frontend/src/App.vue](./frontend/src/App.vue)
- [frontend/src/components/pipeline/AutoModeStudio.vue](./frontend/src/components/pipeline/AutoModeStudio.vue)
- [frontend/src/components/dashboard/UsageDashboardPage.vue](./frontend/src/components/dashboard/UsageDashboardPage.vue)

### Backend

- FastAPI
- SQLAlchemy Async
- SQLite by default
- `httpx` for third-party API calls

Important entry points:

- [backend/app/main.py](./backend/app/main.py)
- [backend/app/agents/pipeline.py](./backend/app/agents/pipeline.py)
- [backend/app/services/llm/qwen_client.py](./backend/app/services/llm/qwen_client.py)

## Agent Pipeline

The one-click generation flow is coordinated by `PipelineExecutor`:

1. `OrchestratorAgent`
   Understands the request, resolves selected images, and creates a shot plan.
2. `PromptEngineerAgent`
   Converts the plan into shot prompts and voice parameters.
3. `AudioSubtitleAgent`
   Generates TTS audio and subtitle timing.
4. `VideoGeneratorAgent`
   Generates shot-level clips from images and prompts.
5. `VideoEditorAgent`
   Reorders, trims, and concatenates clips with audio and subtitles.

Core pipeline file:

- [backend/app/agents/pipeline.py](./backend/app/agents/pipeline.py)

Centralized system prompts:

- [backend/app/prompts/system_prompts.py](./backend/app/prompts/system_prompts.py)

## Model Providers

The project is wired for the following providers:

- `Qwen Omni`
  Used for orchestration, prompt planning, editing decisions, and other structured multimodal reasoning
- `Qwen3 TTS`
  Used for text-to-speech generation
- `WaveSpeed Kling`
  Used for image-to-video generation when configured
- `Seedance 1.5 Pro`
  Default image-to-video provider in the current configuration

Relevant files:

- [backend/app/services/llm_service.py](./backend/app/services/llm_service.py)
- [backend/app/services/tts_service.py](./backend/app/services/tts_service.py)
- [backend/app/services/video_generation/router.py](./backend/app/services/video_generation/router.py)

## Project Structure

```text
vidgen/
├── .github/
│   └── workflows/
├── backend/
│   ├── alembic/
│   ├── app/
│   │   ├── agents/
│   │   ├── core/
│   │   ├── db/
│   │   ├── mcp/
│   │   ├── models/
│   │   ├── prompts/
│   │   ├── routers/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── llm/
│   │   │   ├── video_editing/
│   │   │   └── video_generation/
│   │   └── utils/
│   ├── tests/
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── uv.lock
├── docs/
│   ├── api/
│   ├── architecture/
│   ├── archive/
│   ├── development/
│   ├── plans/
│   ├── portfolio/
│   └── reports/
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── composables/
│   │   ├── lib/
│   │   ├── stores/
│   │   └── types/
│   ├── Dockerfile
│   ├── package.json
│   └── vite.config.ts
├── scripts/
├── docker-compose.yml
├── README.md
└── README-zh-CN.md
```

## Local Setup

### 1. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

The backend reads environment variables from the project root `.env`.

Useful backend shortcuts:

- `./scripts/backend-install-dev.sh`
- `./scripts/backend-dev.sh`
- `./scripts/backend-test.sh`
- `./scripts/backend-lint.sh`

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Default dev server:

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`

## Environment Variables

Create `vidgen/.env` and fill in the providers you want to use.

Common settings:

```env
DATABASE_URL=sqlite+aiosqlite:///./data/vidgen.db
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
```

If provider keys are not configured, some services may fall back to mock implementations depending on the current settings.

## Current Product Flows

### One-Click Generation

- Create or open a project
- Stay on the auto-mode chat workspace
- Upload a material folder or individual images
- Select images from the left material panel or attach images directly in chat
- Enter a script and send
- Let the system generate shot prompts, audio, clips, subtitles, and a merged final video
- Watch the agent pipeline progress in the same screen
- Download editable intermediate outputs such as prompts, audio, and generated clips

### Manual Mode

- Upload a reference video
- Run analysis
- Review recommended materials
- Edit prompts
- Generate clips
- Edit timeline

## Useful Commands

```bash
# backend syntax check
python3 -m compileall backend/app

# code file line guard
./scripts/check-code-file-lines.sh

# code file line guard self-test
./scripts/check-code-file-lines.sh --self-test

# frontend production build
cd frontend && npm run build
```

## Code Size Governance

- Business source files should stay at or below 500 lines unless there is a clear reason not to split them yet.
- The limit is an architecture warning line, not a mechanical ban. When a file needs a temporary exception, add it to [scripts/code-file-line-exceptions.txt](./scripts/code-file-line-exceptions.txt) with the intended extraction direction.
- The CI guard runs [scripts/check-code-file-lines.sh](./scripts/check-code-file-lines.sh) and fails when a tracked business source file exceeds 500 lines without an exception.
- New code should prefer clear boundaries such as routers, services, schemas, agents, composables, components, stores, and helpers instead of growing an existing file indefinitely.

## Notes

- SQLite is the default database for local development.
- Generated assets and local material libraries are ignored in Git by default.
- The current default video generation path uses `Seedance 1.5 Pro` via the backend provider switch.
- The repo currently favors local development and experimentation over production deployment hardening.

## License

Add your preferred license before publishing publicly.
