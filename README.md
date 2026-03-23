# VidGen

VidGen is an AI-assisted short-video production workspace with two workflows:

- `一键生成` for chat-style video generation from script + images
- `手动模式` for step-by-step upload, analysis, material selection, prompt editing, generation, and timeline editing

The current stack uses a React + Vite frontend and a FastAPI backend. The backend orchestrates multiple agents for planning, prompt design, audio/subtitle generation, clip generation, and final editing.

## Highlights

- Chat-style auto mode for uploading materials, selecting images, entering a script, and triggering generation in one place
- Manual mode for creators who want full control over upload, analysis, material selection, and editing
- Multi-agent pipeline:
  - `orchestrator`
  - `prompt_engineer`
  - `audio_subtitle`
  - `video_generator`
  - `video_editor`
- Project dashboard for token usage and run progress
- Support for Qwen Omni, Qwen TTS, and video generation providers such as WaveSpeed Kling or Volcengine Seedance

## Architecture

### Frontend

- React 19
- TypeScript
- Vite
- Zustand
- TanStack Query

Important entry points:

- [frontend/src/App.tsx](./frontend/src/App.tsx)
- [frontend/src/components/pipeline/AutoModeStudio.tsx](./frontend/src/components/pipeline/AutoModeStudio.tsx)
- [frontend/src/components/dashboard/UsageDashboardPage.tsx](./frontend/src/components/dashboard/UsageDashboardPage.tsx)

### Backend

- FastAPI
- SQLAlchemy Async
- SQLite by default
- `httpx` for third-party API calls

Important entry points:

- [backend/app/main.py](./backend/app/main.py)
- [backend/app/agents/pipeline.py](./backend/app/agents/pipeline.py)
- [backend/app/services/qwen_client.py](./backend/app/services/qwen_client.py)

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
- `Seedance`
  Supported as another video generation provider

Relevant files:

- [backend/app/services/llm_service.py](./backend/app/services/llm_service.py)
- [backend/app/services/tts_service.py](./backend/app/services/tts_service.py)
- [backend/app/services/video_generator.py](./backend/app/services/video_generator.py)

## Project Structure

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
└── README.md
```

## Local Setup

### 1. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The backend reads environment variables from the project root `.env`.

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

If provider keys are not configured, some services may fall back to mock implementations depending on the current settings.

## Current Product Flows

### One-Click Generation

- Create or open a project
- Stay on the auto-mode chat workspace
- Upload a material folder or individual images
- Select images from the left material panel or attach images directly in chat
- Enter a script and send
- Watch the agent pipeline progress in the same screen

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

# frontend production build
cd frontend && npm run build
```

## Notes

- SQLite is the default database for local development.
- Generated assets and local material libraries are ignored in Git by default.
- The repo currently favors local development and experimentation over production deployment hardening.

## License

Add your preferred license before publishing publicly.
