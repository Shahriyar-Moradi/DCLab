import os
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import AliasChoices, Field, SecretStr
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
    # Kill switch for /v1 service-token bearer auth (P3.2-A); off = every token is 401.
    service_tokens_enabled: bool = True
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
    # Platform AI flag (ADR 0009 §3): default off; production must set it explicitly
    # AND have the platform ``global_ai`` switch row on. Every model call goes through
    # the AI gateway (models from the governance policy roles; the provider key is read
    # only by ``app/agents/gateway/providers``). P6.9-A retired the legacy
    # DECISION_AGENT_ENABLED / PIPELINE_LLM_VERIFIER_ENABLED flags (ADR 0008 §1): the Lab
    # decision agent and the advisory auditor run whenever AI_ENABLED is on, governed by
    # kill switches (``agent:<key>`` / ``purpose:<key>`` rows) and decision-point levels.
    ai_enabled: bool = False
    # LEGACY (switched to Anthropic 2026-10-16; kept for now): also register the OpenAI and
    # TypeSafe gateway adapters. Their model ids are out of the router, allowlist and prices,
    # so this alone routes nothing to them. Never a key: keys stay out of Settings.
    ai_legacy_providers_enabled: bool = False
    # Jev (semantic decisions on Claude since 2026-10-16) is paid traffic on every pipeline run:
    # it needs its own platform flag on top of AI_ENABLED and the switches. Default off.
    jev_enabled: bool = False
    # Operator-only (platform env, never per tenant): JSON {"model": [input, output]} micro-USD per
    # 1M tokens. It can only RAISE the code's price table (gateway.budget.price); a lower or
    # unknown entry is ignored.
    ai_price_overrides: dict[str, tuple[int, int]] = Field(default_factory=dict)
    # P6.8-A: the one platform evaluation workspace a live R3 run may ask Jev inside
    # (benchmark datasets registered there); unset = no live R3 run anywhere.
    r3_eval_workspace_id: UUID | None = None
    pipeline_llm_timeout_seconds: float = 30.0
    # Application-level object storage. Default is local disk for tests/dev.
    # S3/GCS adapters live behind ObjectStorage; core services never import SDKs.
    object_storage_provider: str = "local"
    object_storage_root: Path = REPO_ROOT / "data" / "object_store"
    object_storage_bucket: str = ""
    object_storage_region: str = "us-east-1"
    # P1.3-A: per-run working directory for the engine (worker-local scratch).
    # Durable outputs are published to object storage; nothing is written under
    # the repository. Default: <system temp>/dclab-runs.
    run_scratch_root: Path | None = None
    # Development-only compatibility while legacy upload callers are migrated.
    # Production always enforces the audited publication gate.
    dataset_publication_enforced: bool | None = None
    # POST /v1/datasets limit for direct API callers (the BFF enforces its own
    # DCLAB_BFF_MAX_UPLOAD_BYTES, same 256 MiB default, for browser uploads).
    v1_dataset_upload_max_bytes: int = 256 * 1024 * 1024
    # P3.1-B2: queued + running auto-train jobs one workspace may hold at once
    # (root runs and branches); above it a new run is 429 run_quota_exceeded.
    ml_max_active_runs_per_workspace: int = 20
    # P4.1-A: show the frozen Decision.ai vertical pages (opportunities, decisions,
    # insights, dashboards) in Studio navigation. Presentation only: the legacy
    # /app routes keep their own authorization either way.
    legacy_decision_layer_enabled: bool = False
    # P4.12-A: keep the frozen Decision.ai marketing pages (industries, solutions,
    # pricing, showcase, platform, company, resources) reachable for signed-in
    # users. Off (or signed out): the web tier redirects them to `/`. Presentation only; no data is behind these pages.
    legacy_marketing_pages_enabled: bool = False
    # Zip training-engine source into object storage as a CodeSnapshot artifact.
    reproducible_code_export_enabled: bool = True
    # P7.1-A: model versions saved before the safe package format (joblib/pickle) are
    # quarantined. Loaded only when this is true, or when unset AND the DCLAB_ENV variable is
    # a development value (fail safe: unset, staging, a typo or APP_ENV/ENVIRONMENT refuse).
    legacy_pickle_models_enabled: bool | None = None
    # P7.1-A: limits of the child processes that rebuild and probe native boosters (address
    # space in bytes, wall-clock seconds; CPU time is the timeout + 10 s). Validated on macOS
    # only: tune per image if a limit breaks a library import there.
    model_probe_memory_bytes: int = 6 << 30
    model_probe_timeout_seconds: int = 120
    # P7.7-A: batch scoring appends per-row reasons (reason_1..reason_3: the input columns that
    # moved the model's score for the row) to the predictions file. Off: no reason columns and
    # the prediction read says reasons are turned off. A failure to explain never fails scoring.
    batch_reasons_enabled: bool = True
    # Wall-clock limit of the reasons step (seconds); it stops at a chunk / column boundary and
    # the read notes how many rows were explained (none in time: reasons unavailable).
    batch_reasons_deadline_seconds: float = 120.0
    # P7.4-A monitoring: kill switch (no new window jobs, links refused; reads still answer) and
    # the minimums below which a window is ``not_enough_data`` / outcomes ``not_enough_labels``.
    monitoring_enabled: bool = True
    monitoring_min_rows: int = 500
    monitoring_min_labels: int = 100
    monitoring_min_per_class: int = 10
    # A burst of scorings / links collapses into one recompute per window at most this often.
    monitoring_debounce_minutes: float = 10.0
    # Durable ML jobs. Production default persists a row and returns; a worker
    # process claims with FOR UPDATE SKIP LOCKED. `inline` / `thread` are local
    # adapters only — they must be set explicitly and are not the default.
    ml_job_dispatcher: str = "postgres"
    ml_job_max_attempts: int = 3
    ml_job_heartbeat_timeout_seconds: float = 300.0
    ml_job_poll_seconds: float = 1.0
    # P8.5-A observability. OpenTelemetry traces and metrics stay a no-op (nothing imported, nothing
    # sent) unless OTEL_EXPORTER_OTLP_ENDPOINT is set or OBSERVABILITY_ENABLED=true; the SDK is the
    # optional `[observability]` extra. LOG_FORMAT=json: one redacted JSON object per log line.
    observability_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "dclab"
    log_format: str = "text"
    log_level: str = "INFO"
    # GET /health/ready: a worker unseen for longer than this is "stale" (degraded, not a 503).
    worker_heartbeat_stale_seconds: float = 120.0
    # GET /health/ready is unauthenticated: its checks run at most once per this many seconds per process.
    readiness_cache_seconds: float = 5.0
    # Weekly scheduled scoring: the worker checks for due schedules every tick. false is the
    # platform kill switch: schedules still save, every run waits ("paused by the platform").
    scoring_schedule_enabled: bool = True
    scoring_schedule_tick_seconds: float = 60.0
    # P8.4-A usage metering: the worker aggregates usage_records from the existing ledgers every
    # tick (false: no new records; reads still answer, limits are still measured live).
    usage_metering_enabled: bool = True
    usage_metering_tick_seconds: float = 300.0
    # P8.4-A plan limits refuse new runs, scorings and uploads with 429 quota_exceeded when reached.
    # false: limits are shown but never refuse work (a process restart applies a change).
    quotas_enforced: bool = True
    # P8.8-A notifications. NOTIFICATIONS_ENABLED=false is the kill switch: nothing is enqueued or
    # sent (read at process start). EMAIL_PROVIDER: none (default: rows are recorded
    # skipped_disabled, nothing is sent) | smtp (STARTTLS required) | postmark (HTTPS API).
    # Credentials come from the environment only, never the database, and are never logged.
    notifications_enabled: bool = True
    notifications_tick_seconds: float = 30.0
    email_provider: str = "none"
    email_from_address: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    postmark_server_token: SecretStr = SecretStr("")
    # Links in emails and webhook payloads ("https://studio.example.com"); empty: no links.
    public_base_url: str = ""
    # Webhook signing secrets are derived from this key (>= 32 characters); empty: webhooks cannot
    # be created or delivered (fail closed). Changing it changes every endpoint's secret.
    webhook_secret_key: SecretStr = SecretStr("")
    # Destination ports webhooks may use (comma-separated); 443 only by default.
    webhook_allowed_ports: str = "443"
    webhook_max_deliveries_per_minute: int = 60


EMAIL_PROVIDERS = frozenset({"none", "smtp", "postmark"})


def is_production_env(settings: Settings) -> bool:
    return settings.dclab_env.strip().lower() in PRODUCTION_ENV_VALUES


def publication_enforced(settings: Settings) -> bool:
    if settings.dataset_publication_enforced is not None:
        return settings.dataset_publication_enforced
    return is_production_env(settings)


def dclab_env_is_development() -> bool:
    """``DCLAB_ENV`` in the process environment names a development environment. Only that
    variable counts (not ``APP_ENV``/``ENVIRONMENT``, not ``.env``, not the settings default):
    an unset variable, staging or a typo is treated as production (fail safe)."""

    from app.agents.governance.platform_default import is_development_env

    return is_development_env(os.environ.get("DCLAB_ENV", ""))


def legacy_pickle_models_allowed(settings: Settings) -> bool:
    if settings.legacy_pickle_models_enabled is not None:
        return settings.legacy_pickle_models_enabled
    return dclab_env_is_development()


# P7.1-A: ``dclab worker run`` marks its process; outside an explicit development
# environment only a marked process loads models (``package_loader.load_model``).
_PROCESS_ROLE = {"worker": False}


def mark_worker_process() -> None:
    _PROCESS_ROLE["worker"] = True


def is_worker_process() -> bool:
    return _PROCESS_ROLE["worker"]


def cookie_secure(settings: Settings) -> bool:
    if settings.session_cookie_secure is not None:
        return settings.session_cookie_secure
    return is_production_env(settings)


def csrf_hmac_secret(settings: Settings) -> str:
    return settings.auth_csrf_secret.strip() or settings.jwt_secret


def cursor_hmac_secret(settings: Settings) -> str:
    """Key material for signed /v1 page cursors (domain-separated by the codec).

    Reuses secrets production already requires (no new setting); rotating
    them only invalidates in-flight cursors, which clients restart from page 1.
    """

    return settings.auth_token_hash_secret.strip() or settings.jwt_secret


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
    if settings.email_provider.strip().lower() not in EMAIL_PROVIDERS:
        problems.append("EMAIL_PROVIDER must be none, smtp or postmark")
    if not is_production_env(settings):
        if problems:
            raise RuntimeError(
                "unsafe authentication configuration: " + "; ".join(problems)
            )
        return
    if not publication_enforced(settings):
        problems.append("DATASET_PUBLICATION_ENFORCED cannot be false in production")
    # AI in production (P6.9-A lifted the P6.2-B2 block): the gateway enforces every dataset
    # and column llm_exposure_policy (ADR 0009 §8), the kill switches and budgets, and the
    # legacy decision writers apply nothing above the ADR 0008 decision-point levels.
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
