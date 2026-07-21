"""LLM / app settings: stored server-side, secret never returned to client."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from .. import ai
from ..config import CREDENTIALS, apply_credential_overrides
from ..schemas import SettingsIn
from ..util import utc_now
from .common import get_conn

router = APIRouter(prefix="/api/settings", tags=["settings"])

# request field -> settings key
FIELD_TO_KEY = {
    "api_key": "llm_api_key",
    "base_url": "llm_base_url",
    "model": "llm_model",
    "app_title": "llm_app_title",
    "max_tokens": "llm_max_tokens",
    "sellersprite_secret_key": "sellersprite_secret_key",
    "ensembledata_token": "ensembledata_token",
    "youtube_api_key": "youtube_api_key",
    "google_search_api_key": "google_search_api_key",
    "google_search_cx": "google_search_cx",
}

# Secret fields: empty value on update means "leave unchanged".
SECRET_FIELDS = {"api_key", "sellersprite_secret_key", "ensembledata_token", "youtube_api_key", "google_search_api_key"}


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, utc_now()),
    )


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}…{key[-4:]}"


def _stored_sellersprite_key(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", ("sellersprite_secret_key",)).fetchone()
    stored = (row["value"] if row else "") or ""
    return stored.strip() or (CREDENTIALS.get("sellersprite_secret_key") or "").strip()


def _stored_credential(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    stored = (row["value"] if row else "") or ""
    return stored.strip() or (CREDENTIALS.get(key) or "").strip()


@router.get("")
def get_settings(conn: sqlite3.Connection = Depends(get_conn)):
    cfg = ai.get_config(conn)
    key = cfg.get("llm_api_key") or ""
    ss_key = _stored_sellersprite_key(conn)
    ed_token = _stored_credential(conn, "ensembledata_token")
    yt_key = _stored_credential(conn, "youtube_api_key")
    google_search_key = _stored_credential(conn, "google_search_api_key")
    google_search_cx = _stored_credential(conn, "google_search_cx")
    return {
        "configured": bool(key),
        "key_hint": _mask(key),
        "base_url": cfg.get("llm_base_url"),
        "model": cfg.get("llm_model"),
        "app_title": cfg.get("llm_app_title"),
        "max_tokens": cfg.get("llm_max_tokens"),
        "sellersprite_configured": bool(ss_key),
        "sellersprite_key_hint": _mask(ss_key),
        "ensembledata_configured": bool(ed_token),
        "ensembledata_key_hint": _mask(ed_token),
        "youtube_configured": bool(yt_key),
        "youtube_key_hint": _mask(yt_key),
        "google_search_configured": bool(google_search_key and google_search_cx),
        "google_search_key_hint": _mask(google_search_key),
        "google_search_cx": google_search_cx,
    }


@router.put("")
def update_settings(payload: SettingsIn, conn: sqlite3.Connection = Depends(get_conn)):
    data = payload.model_dump(exclude_none=True)
    for field, key in FIELD_TO_KEY.items():
        if field not in data:
            continue
        value = data[field]
        # Empty secret means "leave unchanged"; other empty values allowed.
        if field in SECRET_FIELDS and not str(value).strip():
            continue
        clean = str(value).strip()
        _set(conn, key, clean)
        # Mirror tier-2 connector credentials into the runtime map immediately.
        apply_credential_overrides({key: clean})
    return get_settings(conn)
