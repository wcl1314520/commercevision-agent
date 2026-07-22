"""Validated runtime configuration shared by service entrypoints."""

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SecretsSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _secret_directories() -> list[Path]:
    configured = os.getenv("CV_SECRETS_DIR")
    if configured:
        return [Path(value) for value in configured.split(os.pathsep) if value]
    return [path for path in (Path("/run/secrets"), Path("secrets")) if path.is_dir()]


class Settings(BaseSettings):
    """Configuration loaded from environment variables with the CV_ prefix."""

    model_config = SettingsConfigDict(
        env_prefix="CV_",
        env_file=(".env", ".env.local"),
        extra="ignore",
        case_sensitive=False,
        yaml_file="config/base.yaml",
        yaml_file_encoding="utf-8",
    )

    service_name: str = "commercevision"
    version: str = "0.1.0"
    environment: Literal["local", "ci", "staging", "demo", "production"] = "local"
    log_level: str = "INFO"
    readiness_probe_external: bool = False

    mysql_dsn: str = "mysql+aiomysql://commercevision:commercevision@mysql:3306/commercevision"
    redis_url: str = "redis://redis:6379/0"
    rabbitmq_url: str = "amqp://commercevision:commercevision@rabbitmq:5672//"
    milvus_uri: str = "http://milvus:19530"
    milvus_health_uri: str = "http://milvus:9091/healthz"

    object_store_endpoint: str = "http://minio:9000"
    object_store_access_key: str = "commercevision"
    object_store_secret_key: str = "change-me"
    object_store_bucket: str = "task-assets"

    mysql_pool_size: int = Field(default=10, ge=1, le=100)
    mysql_max_overflow: int = Field(default=20, ge=0, le=200)
    mysql_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    workflow_retention_hours: int = Field(default=72, ge=1, le=168)
    workflow_step_lease_seconds: int = Field(default=300, ge=30, le=3600)
    workflow_message_max_attempts: int = Field(default=8, ge=1, le=50)
    worker_consumer_name: str = "agent-worker"
    scheduler_poll_seconds: float = Field(default=2.0, gt=0.1, le=60)
    scheduler_batch_size: int = Field(default=50, ge=1, le=500)
    scheduler_lease_seconds: int = Field(default=30, ge=5, le=300)
    scheduler_recovery_interval_seconds: float = Field(default=10.0, gt=0.5, le=300)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:13000"])
    mcp_transport: Literal["stdio", "sse", "streamable-http"] = "streamable-http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    scheduler_host: str = "0.0.0.0"
    scheduler_port: int = 8002

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        secret_settings = SecretsSettingsSource(
            settings_cls,
            secrets_dir=_secret_directories() or None,
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            secret_settings,
            YamlConfigSettingsSource(settings_cls),
        )


def load_settings(service_name: str) -> Settings:
    """Load settings with a service-specific default name."""

    settings = Settings()
    if settings.service_name == "commercevision":
        settings.service_name = service_name
    return settings
