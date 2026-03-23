from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/vidgen.db"
    UPLOAD_DIR: str = "./data/uploads"
    MATERIALS_ROOT: str = "../materials"
    EXAMPLES_ROOT: str = "../examples"
    THUMBNAILS_DIR: str = "./data/thumbnails"
    GENERATED_DIR: str = "./data/generated"

    QWEN_API_KEY: str = ""
    QWEN_API_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_OMNI_MODEL: str = "qwen-omni-turbo"
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
    USE_MOCK_QUALITY_ASSESSOR: bool = True

    TTS_API_KEY: str = ""
    TTS_API_URL: str = ""
    QUALITY_ASSESSOR_API_KEY: str = ""
    QUALITY_ASSESSOR_API_URL: str = ""
    FFMPEG_BIN: str = "ffmpeg"

    model_config = SettingsConfigDict(env_file="../.env")


settings = Settings()
