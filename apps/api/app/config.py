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
    session_cleanup_batch_size: int = 500
    session_cleanup_max_batches: int = 20
    csrf_cookie_name: str = "dclab_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    login_throttle_attempts: int = 5
    login_throttle_window_minutes: int = 15
    auth_trust_forwarded: bool = False
    auth_email_delivery_enabled: bool = False
    auth_browser_sessions_enabled: bool = True
    # Empty in CI/dev: unkeyed SHA-256 (existing rows). Production requires a
    # dedicated secret; lookups also accept unkeyed SHA-256 and the previous
    # HMAC during rotation. Never put production values in tests.
    auth_token_hash_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUTH_TOKEN_HASH_SECRET", "AUTH_SESSION_HASH_SECRET"
        ),
    )
    auth_token_hash_secret_previous: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUTH_TOKEN_HASH_SECRET_PREVIOUS", "AUTH_SESSION_HASH_SECRET_PREVIOUS"
        ),
    )
    # Empty uses jwt_secret for CSRF HMAC. Production requires a dedicated value.
    auth_csrf_secret: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_CSRF_SECRET"),
    )
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


def csrf_hmac_secret(settings: Settings) -> str:
    return settings.auth_csrf_secret.strip() or settings.jwt_secret


def _secret_is_unsafe(value: str) -> bool:
    return not value.strip() or value.strip() == INSECURE_JWT_SECRET


def validate_runtime_settings(settings: Settings) -> None:
    """Fail closed when a process would ship unsafe auth cookies/secrets."""
    if settings.auth_email_delivery_enabled:
        raise RuntimeError(
            "unsafe authentication configuration: AUTH_EMAIL_DELIVERY_ENABLED "
            "must stay false until a mail provider exists"
        )
    problems: list[str] = []
    if settings.login_throttle_attempts < 1:
        problems.append("LOGIN_THROTTLE_ATTEMPTS must be at least 1")
    if settings.login_throttle_window_minutes < 1:
        problems.append("LOGIN_THROTTLE_WINDOW_MINUTES must be at least 1")
    if settings.session_idle_minutes < 1:
        problems.append("SESSION_IDLE_MINUTES must be at least 1")
    if settings.session_absolute_minutes < settings.session_idle_minutes:
        problems.append("SESSION_ABSOLUTE_MINUTES must be >= SESSION_IDLE_MINUTES")
    if not settings.session_cookie_name.strip():
        problems.append("SESSION_COOKIE_NAME must be set")
    if not settings.csrf_cookie_name.strip() or not settings.csrf_header_name.strip():
        problems.append("CSRF cookie and header names must be set")
    same_site = settings.session_cookie_samesite.strip().lower()
    if same_site not in {"lax", "strict", "none"}:
        problems.append("SESSION_COOKIE_SAMESITE must be lax, strict, or none")
    if not is_production_env(settings):
        if problems:
            raise RuntimeError(
                "unsafe authentication configuration: " + "; ".join(problems)
            )
        return
    if _secret_is_unsafe(settings.jwt_secret):
        problems.append("JWT_SECRET is missing or the development default")
    if _secret_is_unsafe(settings.auth_token_hash_secret):
        problems.append("AUTH_TOKEN_HASH_SECRET is missing or the development default")
    if _secret_is_unsafe(settings.auth_csrf_secret):
        problems.append("AUTH_CSRF_SECRET is missing or the development default")
    if not cookie_secure(settings):
        problems.append("session cookies must be Secure in production")
    if same_site == "none" and not cookie_secure(settings):
        problems.append("SameSite=None requires Secure cookies")
    if settings.session_cookie_path != "/":
        problems.append("session cookie Path must be / in production")
    if not any(origin.strip() for origin in settings.cors_origins.split(",")):
        problems.append("CORS_ORIGINS must list at least one trusted origin")
    if settings.session_max_concurrent < 1:
        problems.append("SESSION_MAX_CONCURRENT must be at least 1")
    if settings.session_cleanup_batch_size < 1:
        problems.append("SESSION_CLEANUP_BATCH_SIZE must be at least 1")
    if settings.session_cleanup_max_batches < 1:
        problems.append("SESSION_CLEANUP_MAX_BATCHES must be at least 1")
    if problems:
        raise RuntimeError(
            "unsafe production authentication configuration: " + "; ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
