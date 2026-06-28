"""Runtime configuration for the Monitor Intelligence Hub backend."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "monitor.db"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

PORT = int(os.environ.get("PORT", "8790"))
HOST = os.environ.get("HOST", "127.0.0.1")

# Background scheduler interval (seconds). Set MONITOR_SCHEDULER=0 to disable.
SCHEDULER_SECONDS = int(os.environ.get("MONITOR_SCHEDULER_SECONDS", "3600"))
SCHEDULER_ENABLED = os.environ.get("MONITOR_SCHEDULER", "1") != "0"
DEFAULT_CRAWL_LIMIT = int(os.environ.get("MONITOR_CRAWL_LIMIT", "20"))

USER_AGENT = "MonitorIntelligenceHub/1.0 (+brand intelligence monitor)"

# Optional credentials for tier-2/3 connectors (filled in later by the user).
CREDENTIALS = {
    "youtube_api_key": os.environ.get("YOUTUBE_API_KEY", ""),
    "ensembledata_token": os.environ.get("ENSEMBLEDATA_TOKEN", ""),
    "reddit_bearer_token": os.environ.get("REDDIT_BEARER_TOKEN", ""),
    "reddit_user_agent": os.environ.get("REDDIT_USER_AGENT", USER_AGENT),
    "discord_bot_token": os.environ.get("DISCORD_BOT_TOKEN", ""),
    "facebook_access_token": os.environ.get("FACEBOOK_ACCESS_TOKEN", "")
    or os.environ.get("META_ACCESS_TOKEN", ""),
    "keepa_api_key": os.environ.get("KEEPA_API_KEY", ""),
    "sellersprite_secret_key": os.environ.get("SELLERSPRITE_SECRET_KEY", ""),
}


def has_credential(name: str) -> bool:
    return bool(CREDENTIALS.get(name))


def apply_credential_overrides(values: dict) -> None:
    """Merge settings-table credential values into the runtime CREDENTIALS map.

    Lets the user supply tier-2 credentials (YouTube key, Ensemble Data token,
    SellerSprite secret) from the in-app settings dialog instead of env vars, and
    keeps env + settings consistent for everything that reads ``has_credential``
    (connector status, run_collector, scheduler). Only non-empty values override.
    """
    for name, value in (values or {}).items():
        if name in CREDENTIALS and value and str(value).strip():
            CREDENTIALS[name] = str(value).strip()
