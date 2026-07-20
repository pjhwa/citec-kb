from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_config_dir() -> Path:
    # /app/config in container; repo root/config when running locally
    candidates: list[Path] = [Path("/app/config"), Path.cwd() / "config"]
    here = Path(__file__).resolve()
    # app/settings.py → parents[0]=app, [1]=..., may be shallow in Docker (/app/app/settings.py)
    for i in range(1, min(6, len(here.parents))):
        candidates.append(here.parents[i] / "config")
    for p in candidates:
        if p.is_dir():
            return p
    return Path("/app/config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    # Auth / SSO scaffolding (default off = open pilot)
    # AUTH_MODE: off | apikey | oidc_stub
    auth_mode: str = Field(default="off", alias="AUTH_MODE")
    auth_tokens_json: str | None = Field(default=None, alias="AUTH_TOKENS_JSON")
    oidc_issuer: str | None = Field(default=None, alias="OIDC_ISSUER")
    oidc_client_id: str | None = Field(default=None, alias="OIDC_CLIENT_ID")
    oidc_audience: str | None = Field(default=None, alias="OIDC_AUDIENCE")

    database_url: str = Field(
        default="postgresql+psycopg://citec:citec@localhost:5433/citec_knowledge",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    raw_dir: str = Field(default="/data/raw", alias="RAW_DIR")

    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )
    company_model_id: str = Field(default="glm-5.2", alias="COMPANY_MODEL_ID")
    company_model_profile: str | None = Field(default=None, alias="COMPANY_MODEL_PROFILE")

    # Optional Fabrix (prod) — if all set, prefer over OpenRouter later
    company_endpoint_url: str | None = Field(default=None, alias="COMPANY_ENDPOINT_URL")
    company_client_key: str | None = Field(default=None, alias="COMPANY_CLIENT_KEY")
    company_pass_key: str | None = Field(default=None, alias="COMPANY_PASS_KEY")
    company_email: str | None = Field(default=None, alias="COMPANY_EMAIL")

    config_dir: Path = Field(default_factory=_default_config_dir)

    @property
    def model_profile_key(self) -> str:
        return self.company_model_profile or self.company_model_id

    def load_model_config(self) -> dict[str, Any]:
        path = self.config_dir / "models.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get(self.model_profile_key, {})

    @property
    def openrouter_model_id(self) -> str:
        cfg = self.load_model_config()
        return str(cfg.get("openrouter_id") or "z-ai/glm-5.2")

    @property
    def max_context_tokens(self) -> int:
        cfg = self.load_model_config()
        return int(cfg.get("max_context_tokens") or 130_000)

    @property
    def llm_backend(self) -> str:
        fabrix_ready = all(
            [
                self.company_endpoint_url,
                self.company_client_key,
                self.company_pass_key,
                self.company_email,
            ]
        )
        if fabrix_ready:
            return "fabrix"
        if self.openrouter_api_key:
            return "openrouter"
        return "none"


@lru_cache
def get_settings() -> Settings:
    return Settings()
