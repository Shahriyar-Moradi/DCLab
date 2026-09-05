from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/app/config.py → repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://postgres:postgres@localhost:5432/decisionai"
    model_dir: Path = REPO_ROOT / "models" / "revenue_prediction"
    policy_path: Path = REPO_ROOT / "configs" / "policies" / "opportunity_prioritization.yaml"
    layer_path: Path = REPO_ROOT / "configs" / "layers" / "conversion_probability.yaml"
    cors_origins: str = "http://localhost:3001,http://127.0.0.1:3001"
    api_port: int = 8001
    web_port: int = 3001
    # Override in .env for any deployed environment; the default only exists so
    # local dev and the test suite run without extra setup.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    # Long-lived so a browser stays signed in until the person clicks Sign out.
    access_token_minutes: int = 60 * 24 * 30
    # Lab decision agent (LLM). Off by default so local/dev/CI never call a provider.
    # DECISION_AGENT_API_KEY (or OPENAI_API_KEY) is required when this is on.
    decision_agent_enabled: bool = False
    decision_agent_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DECISION_AGENT_API_KEY", "OPENAI_API_KEY"),
    )
    decision_agent_model: str = "gpt-4o-mini"
    # Advisory pipeline auditor. Deterministic verification remains authoritative.
    pipeline_llm_verifier_enabled: bool = False
    pipeline_llm_verifier_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("PIPELINE_LLM_VERIFIER_API_KEY", "OPENAI_API_KEY"),
    )
    pipeline_llm_verifier_model: str = "gpt-5.6-luna"
    pipeline_llm_verifier_deep_model: str = "gpt-5.6-terra"
    pipeline_llm_timeout_seconds: float = 30.0
    # Application-level object storage. Default is local disk for tests/dev.
    # S3/GCS adapters live behind ObjectStorage; core services never import SDKs.
    object_storage_provider: str = "local"
    object_storage_root: Path = REPO_ROOT / "data" / "object_store"
    object_storage_bucket: str = ""
    object_storage_region: str = "us-east-1"
    # Zip training-engine source into object storage as a CodeSnapshot artifact.
    reproducible_code_export_enabled: bool = True
    # Durable ML jobs. Production default persists a row and returns; a worker
    # process claims with FOR UPDATE SKIP LOCKED. `inline` / `thread` are local
    # adapters only — they must be set explicitly and are not the default.
    ml_job_dispatcher: str = "postgres"
    ml_job_max_attempts: int = 3
    ml_job_heartbeat_timeout_seconds: float = 300.0
    ml_job_poll_seconds: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
