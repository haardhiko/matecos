"""Application configuration — loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore")
    url: str = "postgresql+asyncpg://matecos:secret@localhost:5432/matecos"
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    echo: bool = False


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")
    url: str = "redis://localhost:6379/0"
    max_connections: int = 50


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    llm_provider: Literal["openai", "anthropic", "gemini", "ollama"] = "openai"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "gpt-4o-mini"


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SANDBOX_", extra="ignore")
    docker_image: str = "matecos-sandbox:latest"
    network_mode: str = "none"
    max_memory_mb: int = 2048
    max_cpu_period: int = 100000
    max_cpu_quota: int = 50000


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    jwt_secret_key: str = "change-me-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    api_key_header: str = "X-API-Key"
    app_secret_key: str = "change-me-in-production"


class OTelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")
    enabled: bool = True
    service_name: str = "matecos"
    exporter_otlp_endpoint: str = "http://localhost:4317"
    traces_sampler: str = "always_on"


class BudgetSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEFAULT_", extra="ignore")
    max_cost_usd: float = 10.0
    max_duration_seconds: int = 3600
    max_agent_iterations: int = 50
    max_tool_calls: int = 200
    max_agent_depth: int = 5
    max_concurrent_agents: int = 10


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", extra="ignore")
    default_policy: Literal["conservative", "balanced", "permissive"] = "conservative"
    auto_approve_low: bool = True
    escalation_webhook: str = ""


class MemorySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    working_memory_ttl_hours: int = 24
    episodic_memory_retention_days: int = 90
    event_store_archive_days: int = 365


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core
    app_name: str = "matecos"
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = True
    log_level: str = "INFO"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # Sub-settings (nested, loaded from env via sub-model prefixes)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    otel: OTelSettings = Field(default_factory=OTelSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)

    # Tool registry
    tool_registry_path: str = "./config/tools"
    max_tool_timeout_seconds: int = 300
    default_tool_timeout_seconds: int = 120

    # GitHub adapter
    github_token: str = ""
    github_clone_base_dir: str = "/tmp/matecos/repos"
    github_max_repo_size_mb: int = 500

    # MCP server — the HTTP endpoint is mounted in the main ASGI app.
    mcp_enabled: bool = True
    mcp_transport: Literal["streamable-http", "stdio"] = "streamable-http"
    mcp_server_name: str = "MATECOS"
    mcp_path: str = "/mcp"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    mcp_import_repositories: str = ""

    # Imported repository tools execute untrusted code unless explicitly allowed.
    allow_untrusted_tools: bool = False

    @field_validator("mcp_path")
    @classmethod
    def _normalize_mcp_path(cls, value: str) -> str:
        path = value.strip()
        if not path or path == "/":
            return "/"
        return f"/{path.strip('/')}"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return cached application settings singleton."""
    return AppSettings()
