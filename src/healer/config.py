"""Typed settings loaded from environment variables."""

import socket
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    anthropic_api_key: SecretStr | None = None
    chroma_path: Path = Path(".healer/chroma")
    ledger_path: Path = Path(".healer/ledger.sqlite3")
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_topic: str = "healer/solutions/v1"
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_tls: bool = False
    mqtt_ca_cert: Path | None = None
    mqtt_timeout_seconds: float = 10.0
    mqtt_max_retries: int = 2
    share_solutions: bool = False
    share_key: SecretStr | None = None
    node_id: str = Field(default_factory=socket.gethostname)
    max_share_bytes: int = 256_000
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
    repair_max_attempts: int = 3
    repair_max_tokens: int = 100_000
