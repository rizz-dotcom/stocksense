"""Environment-based configuration and persisted business-setting defaults.

Secrets (the Gemini key) are read from the environment only and are never
stored in SQLite, logged, or shown in the UI.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "stocksense.db"
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "stocksense.log"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

# Business settings stored in the app_settings table (editable in Settings).
SETTING_DEFAULTS: dict[str, str] = {
    "business_name": "My Business",
    "currency": "PKR",
    "default_horizon_days": "7",
    "default_budget": "12000.00",
    "sales_lookback_days": "28",
    "high_risk_cover_days": "2",
    "medium_risk_cover_days": "5",
    "max_quote_age_days": "14",
    "expiry_warning_days": "3",
    "ai_enabled": "true",
    "session_timeout_minutes": "30",
    "count_simulated_po_as_on_order": "true",
}


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    db_path: Path | None
    log_path: Path
    gemini_api_key: str | None
    gemini_model: str
    gemini_timeout_seconds: int
    max_upload_bytes: int
    log_level: str

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logging.getLogger(__name__).warning("Ignoring invalid integer for %s", name)
        return default
    return max(minimum, min(maximum, value))


_MODEL_RE = re.compile(r"^[A-Za-z0-9._\-/]{3,80}$")


def load_config() -> AppConfig:
    """Load configuration from the environment (and a local .env file)."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    db_path_env = os.getenv("STOCKSENSE_DB_PATH", "").strip()
    db_path = Path(db_path_env).expanduser() if db_path_env else DEFAULT_DB_PATH
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    key = os.getenv("GEMINI_API_KEY", "").strip() or None
    if key and key.lower() in {"your-gemini-api-key-here", "changeme", "placeholder"}:
        key = None  # placeholder copied from .env.example

    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    if not _MODEL_RE.match(model):
        logging.getLogger(__name__).warning("GEMINI_MODEL looks invalid; using default")
        model = DEFAULT_GEMINI_MODEL

    return AppConfig(
        database_url=f"sqlite:///{db_path.as_posix()}",
        db_path=db_path,
        log_path=Path(os.getenv("STOCKSENSE_LOG_PATH", "") or DEFAULT_LOG_PATH),
        gemini_api_key=key,
        gemini_model=model,
        gemini_timeout_seconds=_int_env("GEMINI_TIMEOUT_SECONDS", 30, 5, 120),
        max_upload_bytes=_int_env("STOCKSENSE_MAX_UPLOAD_BYTES", 2_000_000, 10_000, 20_000_000),
        log_level=os.getenv("STOCKSENSE_LOG_LEVEL", "INFO").upper(),
    )
