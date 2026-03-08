from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_id: int = Field(alias="TELEGRAM_ALLOWED_USER_ID")
    telegram_chat_id: int | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    cursor_api_key: str = Field(alias="CURSOR_API_KEY")
    cursor_api_base_url: str = Field(
        default="https://api.cursor.com",
        alias="CURSOR_API_BASE_URL",
    )
    github_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GITHUB_TOKEN", "GITHUB_PAT"),
    )
    github_api_base_url: str = Field(
        default="https://api.github.com",
        alias="GITHUB_API_BASE_URL",
    )
    github_default_merge_method: str = Field(
        default="squash",
        alias="GITHUB_DEFAULT_MERGE_METHOD",
    )
    cursor_api_max_retries: int = Field(default=3, alias="CURSOR_API_MAX_RETRIES")
    cursor_api_retry_backoff_seconds: float = Field(
        default=1.0,
        alias="CURSOR_API_RETRY_BACKOFF_SECONDS",
    )
    sqlite_path: Path = Field(default=Path("/data/connector.db"), alias="SQLITE_PATH")
    poll_interval_seconds: float = Field(default=10.0, alias="POLL_INTERVAL_SECONDS")
    followup_poll_interval_seconds: float = Field(
        default=5.0,
        alias="FOLLOWUP_POLL_INTERVAL_SECONDS",
    )
    followup_poll_timeout_seconds: float = Field(
        default=180.0,
        alias="FOLLOWUP_POLL_TIMEOUT_SECONDS",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator(
        "poll_interval_seconds",
        "followup_poll_interval_seconds",
        "followup_poll_timeout_seconds",
        "cursor_api_retry_backoff_seconds",
    )
    @classmethod
    def validate_positive_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("intervals must be positive")
        return value

    @field_validator("cursor_api_max_retries")
    @classmethod
    def validate_non_negative_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max retries must be zero or greater")
        return value

    @field_validator("cursor_api_base_url", "github_api_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("github_default_merge_method")
    @classmethod
    def validate_merge_method(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"merge", "squash", "rebase"}:
            raise ValueError("github default merge method must be merge, squash, or rebase")
        return normalized

    def resolve_chat_id(self, stored_chat_id: int | None) -> int | None:
        return stored_chat_id or self.telegram_chat_id or self.telegram_allowed_user_id
