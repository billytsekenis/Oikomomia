"""Application configuration.

All settings come from environment variables (or a `.env` file). Secrets are
never logged and are only exposed to the API as booleans ("is configured").
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BACKEND_DIR / "data"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Oikonomia Terminal"
    environment: str = "development"

    # Database: SQLite by default (zero-config); set to a PostgreSQL URL in production,
    # e.g. postgresql+psycopg://user:pass@host/db
    database_url: str = f"sqlite:///{DATA_DIR / 'oikonomia.db'}"

    # Security
    secret_key: str = ""
    access_token_minutes: int = 60 * 24 * 14
    allow_registration: bool = True
    cookie_secure: bool = False

    # Demo mode: every data provider is replaced by a clearly labelled synthetic generator.
    demo_mode: bool = False

    # Data providers
    sec_user_agent: str = "OikonomiaTerminal/1.0 (personal research; set SEC_USER_AGENT to 'Name email')"
    http_user_agent: str = "OikonomiaTerminal/1.0 (personal research app)"
    fred_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    # Background jobs
    enable_scheduler: bool = True
    history_years: int = 10
    # Universe size used by the model trainer (smaller = faster on tiny hosts)
    train_max_tickers: int = 120
    track_record_tickers: str = ""  # comma list; empty = full universe
    mc_paths: int = 4000

    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.secret_key:
        # Persist a generated key so sessions survive restarts on a persistent disk.
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key_file = DATA_DIR / ".secret_key"
        if key_file.exists():
            s.secret_key = key_file.read_text().strip()
        else:
            s.secret_key = secrets.token_urlsafe(48)
            key_file.write_text(s.secret_key)
    return s
