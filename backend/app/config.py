from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PIPELINE_ENGINE: str = "langgraph"  # pipeline | langgraph | swarm
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/vidgen.db"
    UPLOAD_DIR: str = "./data/uploads"
    MATERIALS_ROOT: str = "../materials"
    EXAMPLES_ROOT: str = "../examples"
    THUMBNAILS_DIR: str = "./data/thumbnails"
    GENERATED_DIR: str = "./data/generated"
    VIDEO_REPOSITORY_DIR: str = "./data/video_repository"
    BGM_DIR: str = "../bgm"  # royalty-free background music by mood
    WATERMARKS_DIR: str = "./data/watermarks"  # per-project watermark images

    QWEN_API_KEY: str = ""
    QWEN_API_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_OMNI_MODEL: str = "qwen3-omni-flash"
    QWEN_TTS_MODEL: str = "qwen3-tts-flash"

    WAVESPEED_API_KEY: str = ""
    WAVESPEED_API_URL: str = "https://api.wavespeed.ai/api/v3"
    KLING_MODEL: str = "kling-v3"

    KLING_API_KEY: str = ""
    KLING_API_URL: str = ""

    # Volcengine Ark / Seedance
    ARK_API_KEY: str = ""
    ARK_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"
    SEEDANCE_MODEL: str = "doubao-seedance-1-5-pro-251215"
    SEEDANCE_DURATION: int = 5
    SEEDANCE_SUPPORTED_DURATIONS: list[int] = [5]  # each image generates exactly 5s video
    SEEDANCE_RESOLUTION: str = "720p"
    # Platform target resolutions (width x height)
    PLATFORM_RESOLUTIONS: dict[str, tuple[int, int]] = {
        "generic": (1280, 720),      # 16:9 landscape
        "douyin": (720, 1280),        # 9:16 portrait
        "xiaohongshu": (1080, 1440),  # 3:4 portrait
        "bilibili": (1280, 720),      # 16:9 landscape
    }
    SEEDANCE_NO_AUDIO: bool = True  # default to silent mode; user can override
    VIDEO_GENERATOR_PROVIDER: str = "seedance"  # "seedance" | "wavespeed" | "mock"

    LLM_API_KEY: str = ""
    LLM_API_URL: str = ""

    IMAGE_COMPOSITOR_API_KEY: str = ""
    IMAGE_COMPOSITOR_API_URL: str = ""
    LTX_API_KEY: str = ""
    LTX_API_URL: str = ""

    USE_MOCK_ANALYZER: bool = True
    USE_MOCK_LLM: bool = True
    USE_MOCK_GENERATOR: bool = True
    USE_MOCK_COMPOSITOR: bool = True
    USE_MOCK_LIPSYNC: bool = True
    USE_MOCK_TTS: bool = True
    USE_MOCK_VIDEO_EDITOR: bool = True

    TTS_API_KEY: str = ""
    TTS_API_URL: str = ""
    FFMPEG_BIN: str = "ffmpeg"
    DOUYIN_OPEN_BASE_URL: str = "https://open.douyin.com"
    DOUYIN_CLIENT_KEY: str = ""
    DOUYIN_CLIENT_SECRET: str = ""
    DOUYIN_REDIRECT_URI: str = "http://127.0.0.1:8000/api/social-accounts/douyin/callback"
    DOUYIN_DEFAULT_SCOPE: str = "user_info,video.create"
    FRONTEND_BASE_URL: str = "http://127.0.0.1:5173"

    # Keyframe extraction
    KEYFRAME_MAX_EXTRACT: int = 20
    KEYFRAME_SCENE_THRESHOLD: float = 0.3

    # ── Reliability: timeouts & concurrency ──────────────────────────────────
    # Per-shot video generation polling timeout (seconds). 600 = 10 min.
    VIDEO_GENERATION_TIMEOUT_SECONDS: int = 600
    # Max concurrent shot generation tasks (prevents API rate-limit hammering).
    MAX_CONCURRENT_SHOTS: int = 5
    # Overall agent execution timeout (wraps the agent's execute() call).
    AGENT_TIMEOUT_SECONDS: int = 900

    # ── QA Reviewer ──────────────────────────────────────────────────────────
    # Enable the QA reviewer node after video_editor in the pipeline.
    QA_REVIEW_ENABLED: bool = True
    # Automatically retry the recommended upstream agent when QA fails.
    QA_AUTO_RETRY_ENABLED: bool = True
    # Maximum QA-triggered retries before giving up and delivering anyway.
    MAX_QA_RETRIES: int = 1

    # ── Human-in-the-Loop ────────────────────────────────────────────────────
    # When True, the pipeline pauses after PromptEngineer for user review
    # before proceeding to audio/video generation.
    HUMAN_IN_LOOP_PROMPT_REVIEW: bool = False

    # ── Agent Memory ─────────────────────────────────────────────────────────
    # Persist cross-run user preferences (voice params, platform style, etc.)
    AGENT_MEMORY_ENABLED: bool = True

    # ── Security: input validation ───────────────────────────────────────────
    MAX_UPLOAD_SIZE_MB: int = 500
    MAX_IMAGE_SIZE_MB: int = 50
    ALLOWED_IMAGE_TYPES: list[str] = ["image/jpeg", "image/png", "image/webp", "image/gif"]
    ALLOWED_VIDEO_TYPES: list[str] = ["video/mp4", "video/quicktime", "video/webm", "video/avi"]

    CORS_ALLOWED_ORIGINS: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://localhost:4173,"
        "http://127.0.0.1:4173"
    )

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


settings = Settings()
