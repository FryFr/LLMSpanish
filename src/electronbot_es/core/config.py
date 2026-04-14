from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    groq_api_key: str | None = Field(default=None)
    anthropic_api_key: str | None = Field(default=None)
    deepgram_api_key: str | None = Field(default=None)
    cartesia_api_key: str | None = Field(default=None)
    elevenlabs_api_key: str | None = Field(default=None)

    voice_mode: str = "cloud"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


def get_settings() -> Settings:
    return Settings()
