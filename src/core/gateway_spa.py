from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse


def mount_war_room_spa(app: FastAPI, *, logger) -> None:
    """Mount the War Room SPA when a compiled frontend is available."""
    warroom_dist = Path(__file__).resolve().parent.parent / "warroom" / "dist"

    if not warroom_dist.is_dir():
        logger.info("War Room SPA not found at %s; skipping mount", warroom_dist)
        return

    def _serve_warroom_index():
        html = (warroom_dist / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    def _redirect_to_warroom() -> RedirectResponse:
        return RedirectResponse(url="/war-room/", status_code=307)

    @app.get("/")
    async def root_to_warroom():
        return _redirect_to_warroom()

    @app.get("/war-room/{full_path:path}")
    async def warroom_spa(full_path: str):
        file_path = warroom_dist / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return _serve_warroom_index()

    @app.get("/war-room")
    async def warroom_root():
        return _redirect_to_warroom()

    logger.info("War Room SPA mounted at /war-room/ from %s", warroom_dist)
