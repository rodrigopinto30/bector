"""Typed settings loaded from environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    anthropic_api_key: SecretStr | None = None
    chroma_path: Path = Path(".healer/chroma")
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    test_command: str = "python -m pytest"
    allowed_test_commands: list[str] = ["pytest", "python -m pytest"]
    test_timeout_seconds: float = 300.0
    max_output_bytes: int = 1_000_000
    max_sandbox_bytes: int = 500_000_000
    max_patch_files: int = 5
    max_patch_bytes: int = 100_000
    max_patch_changed_lines: int = 300
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_max_tokens: int = 16_000
    anthropic_timeout_seconds: float = 120.0
    anthropic_max_retries: int = 2
    context_max_chars: int = 12_000
    context_max_distance: float = 0.7
