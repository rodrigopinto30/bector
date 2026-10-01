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
