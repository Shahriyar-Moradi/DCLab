from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/app/config.py → repository root
REPO_ROOT = Path(__file__).resolve().parents[3]

INSECURE_JWT_SECRET = "dev-only-insecure-secret-change-me"
PRODUCTION_ENV_VALUES = frozenset({"production", "prod"})


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
    dclab_env: str = Field(
        default="development",
        validation_alias=AliasChoices("DCLAB_ENV", "APP_ENV", "ENVIRONMENT"),
    )
    # Override in .env for any deployed environment; the default only exists so
    # local dev and the test suite run without extra setup.
    jwt_secret: str = INSECURE_JWT_SECRET
    # API bearer tokens (POST /auth/tokens). Browser sessions use idle/absolute
    # cookie lifetimes below, not this value.
    access_token_minutes: int = 60 * 24 * 30
    session_cookie_name: str = "dclab_session"
    session_cookie_path: str = "/"
    session_cookie_samesite: str = "lax"
    session_cookie_secure: bool | None = None
    session_idle_minutes: int = 60 * 12
    session_absolute_minutes: int = 60 * 24 * 7
    session_retention_days: int = 30
    session_max_concurrent: int = 5
    csrf_cookie_name: str = "dclab_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    login_throttle_attempts: int = 5
    login_throttle_window_minutes: int = 15
    auth_trust_forwarded: bool = False
    auth_email_delivery_enabled: bool = False
    recovery_token_minutes: int = 60
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


def is_production_env(settings: Settings) -> bool:
    return settings.dclab_env.strip().lower() in PRODUCTION_ENV_VALUES


def cookie_secure(settings: Settings) -> bool:
    if settings.session_cookie_secure is not None:
        return settings.session_cookie_secure
    return is_production_env(settings)


def validate_runtime_settings(settings: Settings) -> None:
    """Fail closed when a production process would ship unsafe auth cookies/secrets."""
    if settings.auth_email_delivery_enabled:
        raise RuntimeError(
            "unsafe authentication configuration: AUTH_EMAIL_DELIVERY_ENABLED "
            "must stay false until a mail provider exists"
        )
    if not is_production_env(settings):
        return
    problems: list[str] = []
    if not settings.jwt_secret or settings.jwt_secret == INSECURE_JWT_SECRET:
        problems.append("JWT_SECRET is missing or the development default")
    if not cookie_secure(settings):
        problems.append("session cookies must be Secure in production")
    same_site = settings.session_cookie_samesite.strip().lower()
    if same_site not in {"lax", "strict", "none"}:
        problems.append("SESSION_COOKIE_SAMESITE must be lax, strict, or none")
    if same_site == "none" and not cookie_secure(settings):
        problems.append("SameSite=None requires Secure cookies")
    if settings.session_cookie_path != "/":
        problems.append("session cookie Path must be / in production")
    if settings.session_max_concurrent < 1:
        problems.append("SESSION_MAX_CONCURRENT must be at least 1")
    if problems:
        raise RuntimeError(
            "unsafe production authentication configuration: " + "; ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
