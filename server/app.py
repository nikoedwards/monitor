"""Monitor Intelligence Hub — FastAPI application entry point.

A brand-rooted, connector-driven brand/competitor intelligence hub.
Run: `python server/app.py` (serves the built SPA from ./dist).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import DIST, HOST, PORT, SNAPSHOT_DIR, apply_credential_overrides
from .connectors.registry import sync_to_db
from .db import db, init_db
from .domains import brands, content, creators, insights, sales, settings, sources, web
from .scheduler import start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with db() as conn:
        sync_to_db(conn)
        stored = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
        apply_credential_overrides(stored)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    start_scheduler()
    yield


app = FastAPI(title="Monitor Intelligence Hub", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (brands, content, creators, sales, web, sources, insights, settings):
    app.include_router(module.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# Static assets (built SPA) + snapshot files.
if SNAPSHOT_DIR.exists():
    app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOT_DIR)), name="snapshots")

_ASSETS = DIST / "assets"
if _ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    candidate = (DIST / full_path)
    if full_path and candidate.is_file():
        return FileResponse(str(candidate))
    index = DIST / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return JSONResponse(
        {"detail": "Frontend not built. Run `npm run build` first."}, status_code=503
    )


def main() -> None:
    print(f"Monitor Intelligence Hub running at http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
