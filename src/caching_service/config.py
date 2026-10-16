from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, read from CACHE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="CACHE_")

    database_url: str = "sqlite+aiosqlite:///./cache.db"
    # Simulated latency of the external transformer; makes the effect of caching visible.
    transformer_latency_seconds: float = Field(default=0.1, ge=0)
