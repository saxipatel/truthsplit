from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRUTHSPLIT_", env_file=".env", extra="ignore")

    database_url: str
    # Source connections are read from TRUTHSPLIT_SOURCE_<NAME>_URL (see app/adapters/postgres.py). Their
    # credentials live only in the environment; the metadata database stores the profile name.
    allowed_source_hosts: str = "source-db,localhost,127.0.0.1"

    storage_dir: Path = Path("var/storage")
    max_upload_mb: int = Field(default=25, gt=0)
    max_rows: int = Field(default=250_000, gt=0)
    statement_timeout_ms: int = Field(default=5000, gt=0)
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def allowed_source_host_set(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.allowed_source_hosts.split(",") if h.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
