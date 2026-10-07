"""Runtime settings, loaded from the repo-root .env file.

The residency flag is the gate the plan mandates (section 6): all model calls
go through ppm.models, and ppm.models refuses to build API-backed providers
while PPM_RESIDENCY=local.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_RESIDENCIES = {"local", "api"}
VALID_PROVIDERS = {"ollama", "api", "stub"}


class ConfigError(Exception):
    """Raised when the environment cannot produce a valid configuration."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    dsn: str
    residency: str
    provider: str
    ollama_host: str
    chat_model: str
    embed_model: str
    embed_dim: int
    api_base: str
    api_key: str
    review_threshold: float
    location_default: str
    user_label: str
    registry_dir: Path
    app_specs_dir: Path

    def validate(self) -> "Settings":
        if not self.dsn:
            raise ConfigError("PPM_DSN is not set - copy .env.example to .env")
        if self.residency not in VALID_RESIDENCIES:
            raise ConfigError(f"PPM_RESIDENCY must be one of {sorted(VALID_RESIDENCIES)}")
        if self.provider not in VALID_PROVIDERS:
            raise ConfigError(f"PPM_MODEL_PROVIDER must be one of {sorted(VALID_PROVIDERS)}")
        if self.residency == "local" and self.provider == "api":
            raise ConfigError(
                "PPM_RESIDENCY=local forbids PPM_MODEL_PROVIDER=api: no writing to an "
                "endpoint outside office hardware until a written residency position allows it"
            )
        if self.embed_dim <= 0:
            raise ConfigError("PPM_EMBED_DIM must be positive")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    return Settings(
        dsn=_env("PPM_DSN"),
        residency=_env("PPM_RESIDENCY", "local"),
        provider=_env("PPM_MODEL_PROVIDER", "ollama"),
        ollama_host=_env("PPM_OLLAMA_HOST", "http://127.0.0.1:11434"),
        chat_model=_env("PPM_CHAT_MODEL", "qwen3:8b"),
        embed_model=_env("PPM_EMBED_MODEL", "nomic-embed-text"),
        embed_dim=int(_env("PPM_EMBED_DIM", "768")),
        api_base=_env("PPM_API_BASE"),
        api_key=_env("PPM_API_KEY"),
        review_threshold=float(_env("PPM_REVIEW_CONFIDENCE_THRESHOLD", "0.7")),
        location_default=_env("PPM_LOCATION_DEFAULT", "Jakarta"),
        user_label=_env("PPM_USER_LABEL", "founder"),
        registry_dir=Path(_env("PPM_REGISTRY_DIR", str(REPO_ROOT / "registry"))),
        app_specs_dir=Path(_env("PPM_APP_SPECS_DIR", str(REPO_ROOT / "app_specs"))),
    ).validate()
