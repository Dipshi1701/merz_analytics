"""Merz dashboard settings, YAML config loader, and date utilities."""

import os
import re
import yaml
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# --- Paths ---
APP_ROOT = Path(__file__).parent.parent.resolve()
CONFIG_PATH = APP_ROOT / "config.yaml"
ENV_PATH = APP_ROOT / ".env"

load_dotenv(ENV_PATH, override=True)

# --- Database ---
DATABASE_URL = os.getenv(
    "MERZ_DATABASE_URL",
    "mysql+pymysql://root:root@127.0.0.1:3306/merz_db",
)

# --- Sync behaviour ---
REFRESH_DAYS = int(os.getenv("MERZ_REFRESH_DAYS", "7"))

# Inbenta Reporting API environment filter: development | preproduction | production
INBENTA_ENV = (os.getenv("MERZ_INBENTA_ENV") or "production").strip().lower()

# --- Report output ---
REPORT_DIR = APP_ROOT / "data" / "reports"


# ---------------------------------------------------------------------------
# YAML config loader
# ---------------------------------------------------------------------------

class ConfigLoader:
    """Load config.yaml and substitute ${ENV_VAR} placeholders."""

    def __init__(self, config_path: str = None):
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        load_dotenv(ENV_PATH, override=True)
        self.config = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            text = f.read()
        return yaml.safe_load(self._sub_env_vars(text))

    def _sub_env_vars(self, text: str) -> str:
        def replacer(match):
            var = match.group(1)
            value = os.environ.get(var)
            if value is None:
                logger.warning(f"Env var '{var}' not set")
                return match.group(0)
            return value
        return re.sub(r"\$\{([^}]+)\}", replacer, text)

    def get(self, key_path: str, default: Any = None) -> Any:
        keys = key_path.split(".")
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def get_api_config(self) -> Dict[str, Any]:
        return self.config.get("api", {})

    def get_filters_config(self) -> Dict[str, Any]:
        return self.config.get("filters", {})

    def get_output_config(self) -> Dict[str, Any]:
        return self.config.get("output", {})


# ---------------------------------------------------------------------------
# Date utilities
# ---------------------------------------------------------------------------

def get_week_key(dt: datetime) -> str:
    """Return ISO week key, e.g. '2026-W04'."""
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def split_into_single_days(
    start_date: datetime, end_date: datetime
) -> List[Tuple[datetime, datetime]]:
    """Split a date range into per-day (day, day) tuples for day-by-day API calls."""
    days = []
    current = start_date
    while current <= end_date:
        days.append((current, current))
        current += timedelta(days=1)
    return days


def format_report_date(dt: datetime) -> str:
    return dt.strftime("%d%m%Y")
