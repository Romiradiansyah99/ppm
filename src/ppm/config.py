"""Runtime settings loaded from the repo-root .env file.

PPM runs deterministic data/RAG work locally and uses a private Hermes Agent
API service for the conversational harness. No LangChain or LangGraph settings
exist here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_RESIDENCIES = {"local", "api_approved"}


class ConfigError(Exception):
    """Raised when the environment cannot produce a valid configuration."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    dsn: str
    dsn_test: str
    residency: str
    ollama_host: str
    chat_model: str
    embed_model: str
    embed_dim: int
    num_gpu: int
    num_ctx: int
    top_k: int
    min_similarity: float
    user_label: str
    manifest_path: Path
    taxonomy_path: Path
    app_specs_dir: Path
    llm_api_base: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    analyst_max_parallel: int
    analyst_task_timeout: int
    analyst_allow_code_execution: bool
    analyst_egress_ack: str

    # Public / Hermes additions. Defaults keep existing CLI and tests usable.
    public_mode: bool = False
    session_secret: str = ""
    tool_secret: str = ""
    hermes_analyst_url: str = ""
    hermes_analyst_key: str = ""
    hermes_admin_url: str = ""
    hermes_admin_key: str = ""
    hermes_model: str = "hermes-agent"
    hermes_timeout: int = 180
    cookie_secure: bool = True
    session_days: int = 30
    daily_message_limit: int = 50
    max_input_chars: int = 6000
    db_pool_max: int = 10
    documents_dir: Path = REPO_ROOT / "exports" / "documents"
    incoming_dir: Path = REPO_ROOT / "incoming"

    def validate(self) -> "Settings":
        if not self.dsn:
            raise ConfigError("PPM_DSN is not set - copy .env.example to .env")
        if self.residency not in VALID_RESIDENCIES:
            raise ConfigError(
                f"PPM_RESIDENCY must be one of {sorted(VALID_RESIDENCIES)}: "
                "v3 supports local models or an explicitly approved API"
            )
        if self.embed_dim <= 0:
            raise ConfigError("PPM_EMBED_DIM must be positive")
        if self.num_gpu < 0 or self.num_ctx <= 0:
            raise ConfigError("PPM_NUM_GPU must be >= 0 and PPM_NUM_CTX positive")
        if self.top_k <= 0:
            raise ConfigError("PPM_TOP_K must be positive")
        if not 0 <= self.min_similarity <= 1:
            raise ConfigError("PPM_MIN_SIMILARITY must be between 0 and 1")
        if self.analyst_max_parallel <= 0 or self.analyst_task_timeout <= 0:
            raise ConfigError("analyst parallel/timeout settings must be positive")
        if self.hermes_timeout <= 0:
            raise ConfigError("PPM_HERMES_TIMEOUT must be positive")
        if self.session_days <= 0 or self.daily_message_limit <= 0:
            raise ConfigError("session days and daily message limit must be positive")
        if self.max_input_chars <= 0 or self.db_pool_max <= 0:
            raise ConfigError("input limit and database pool size must be positive")
        if self.public_mode:
            if not self.session_secret:
                raise ConfigError("PPM_SESSION_SECRET is required in public mode")
            if not self.tool_secret:
                raise ConfigError("PPM_TOOL_SECRET is required in public mode")
            if not self.hermes_analyst_url or not self.hermes_analyst_key:
                raise ConfigError("Hermes analyst URL/key are required in public mode")
        return self

    def require_api(self) -> None:
        if self.residency != "api_approved":
            raise ConfigError(
                "Analyst planning/codegen needs PPM_RESIDENCY=api_approved"
            )
        if not self.analyst_egress_ack.strip():
            raise ConfigError(
                "Set PPM_ANALYST_EGRESS_ACK (name + date) before sending project data to an API"
            )
        if not (self.llm_api_base and self.llm_api_key and self.llm_model):
            raise ConfigError(
                "Set PPM_LLM_API_BASE, PPM_LLM_API_KEY and PPM_LLM_MODEL"
            )
        if not self.analyst_allow_code_execution:
            raise ConfigError("PPM_ANALYST_ALLOW_CODE_EXECUTION is false")

    def hermes_for(self, role: str) -> tuple[str, str]:
        """Return the private Hermes base URL and API key for a PPM role."""
        if role == "senior_manager":
            return self.hermes_admin_url, self.hermes_admin_key
        return self.hermes_analyst_url, self.hermes_analyst_key


def _path(name: str, default: Path) -> Path:
    value = Path(_env(name, str(default)))
    return value if value.is_absolute() else (REPO_ROOT / value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    return Settings(
        dsn=_env("PPM_DSN"),
        dsn_test=_env("PPM_TEST_DSN"),
        residency=_env("PPM_RESIDENCY", "local"),
        ollama_host=_env("PPM_OLLAMA_HOST", "http://127.0.0.1:11434"),
        chat_model=_env("PPM_CHAT_MODEL", "qwen2.5:3b"),
        embed_model=_env("PPM_EMBED_MODEL", "nomic-embed-text"),
        embed_dim=int(_env("PPM_EMBED_DIM", "768")),
        num_gpu=int(_env("PPM_NUM_GPU", "0")),
        num_ctx=int(_env("PPM_NUM_CTX", "4096")),
        top_k=int(_env("PPM_TOP_K", "6")),
        min_similarity=float(_env("PPM_MIN_SIMILARITY", "0.25")),
        user_label=_env("PPM_USER_LABEL", "founder"),
        manifest_path=_path("PPM_CORPUS_MANIFEST", REPO_ROOT / "corpus" / "manifest.yaml"),
        taxonomy_path=_path("PPM_TAXONOMY_FILE", REPO_ROOT / "registry" / "template_taxonomy.yaml"),
        app_specs_dir=_path("PPM_APP_SPECS_DIR", REPO_ROOT / "app_specs"),
        llm_api_base=_env("PPM_LLM_API_BASE"),
        llm_api_key=_env("PPM_LLM_API_KEY"),
        llm_model=_env("PPM_LLM_MODEL"),
        llm_temperature=float(_env("PPM_LLM_TEMPERATURE", "0")),
        analyst_max_parallel=int(_env("PPM_ANALYST_MAX_PARALLEL", "4")),
        analyst_task_timeout=int(_env("PPM_ANALYST_TASK_TIMEOUT", "120")),
        analyst_allow_code_execution=_env("PPM_ANALYST_ALLOW_CODE_EXECUTION", "true").lower() == "true",
        analyst_egress_ack=_env("PPM_ANALYST_EGRESS_ACK"),
        public_mode=_env_bool("PPM_PUBLIC_MODE", False),
        session_secret=_env("PPM_SESSION_SECRET"),
        tool_secret=_env("PPM_TOOL_SECRET"),
        hermes_analyst_url=_env("PPM_HERMES_ANALYST_URL"),
        hermes_analyst_key=_env("PPM_HERMES_ANALYST_KEY"),
        hermes_admin_url=_env("PPM_HERMES_ADMIN_URL"),
        hermes_admin_key=_env("PPM_HERMES_ADMIN_KEY"),
        hermes_model=_env("PPM_HERMES_MODEL", "hermes-agent"),
        hermes_timeout=int(_env("PPM_HERMES_TIMEOUT", "180")),
        cookie_secure=_env_bool("PPM_COOKIE_SECURE", True),
        session_days=int(_env("PPM_SESSION_DAYS", "30")),
        daily_message_limit=int(_env("PPM_DAILY_MESSAGE_LIMIT", "50")),
        max_input_chars=int(_env("PPM_MAX_INPUT_CHARS", "6000")),
        db_pool_max=int(_env("PPM_DB_POOL_MAX", "10")),
        documents_dir=_path("PPM_DOCUMENTS_DIR", REPO_ROOT / "exports" / "documents"),
        incoming_dir=_path("PPM_INCOMING_DIR", REPO_ROOT / "incoming"),
    ).validate()
