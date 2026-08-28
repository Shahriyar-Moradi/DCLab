from functools import lru_cache
from pathlib import Path

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
