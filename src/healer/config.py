"""Typed settings loaded from environment variables."""

from pathlib import Path

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
