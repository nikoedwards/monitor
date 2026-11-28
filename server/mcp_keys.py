"""Issue opaque keys for the remote read-only MCP endpoint."""
import hmac
import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from .db import db


KEY_PATH = "/api/integrations/mcp-keys"
PREFIX = "mon_"
router = APIRouter(prefix=KEY_PATH)


def verifier(token: str, site_code: bytes) -> str:
    # Rotating the site's access code also retires keys issued under it.
    return hmac.digest(token.encode("ascii"), site_code, "sha256").hex()


def accepts(token: str, site_code: bytes) -> bool:
    if len(token) != 47 or not token.startswith(PREFIX) or not token.isascii():
        return False
    with db() as conn:
        return conn.execute("SELECT 1 FROM mcp_access_keys WHERE verifier = ?",
                            (verifier(token, site_code),)).fetchone() is not None


def check_origin(request: Request):
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or origin == f"https://{host}":
        return
    if origin == f"http://{host}" and request.url.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise HTTPException(403, "请在 Monitor 的 HTTPS 接入页操作。")


@router.post("")
def issue(request: Request):
    check_origin(request)
    site_code = getattr(request.state, "mcp_key_context", None)
    if site_code is None:
        return {"key": None, "id": None}
    token = PREFIX + secrets.token_urlsafe(32)
    key_id = secrets.token_hex(16)
    with db() as conn:
        conn.execute("INSERT INTO mcp_access_keys (id, verifier) VALUES (?, ?)",
                     (key_id, verifier(token, site_code)))
    return {"key": token, "id": key_id}


@router.delete("/{key_id}", status_code=204)
def revoke(key_id: str, request: Request):
    check_origin(request)
    if getattr(request.state, "mcp_key_context", None):
        with db() as conn:
            conn.execute("DELETE FROM mcp_access_keys WHERE id = ?", (key_id,))
    return Response(status_code=204)
