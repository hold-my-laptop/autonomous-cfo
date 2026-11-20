from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """Backend configuration. Every field can be overridden by an environment variable
    of the same name in upper case (e.g. DEMO_AS_OF_DATE=2025-05-05) or in a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    demo_mode: bool = True
    demo_as_of_date: date = date(2025, 5, 5)
    statement_path: Path = REPO_ROOT / "local" / "pizzeria-amalfi-bank-statement-2025-v6.csv"
    opening_balance: int = 250_000
    forecast_horizon_days: int = 91
    partner_transfer_movable_days: int = 45

    database_url: str | None = None
    ai_url: str = "http://localhost:8001"
    internal_token: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
