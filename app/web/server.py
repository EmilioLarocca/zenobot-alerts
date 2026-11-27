"""
server.py — the web dashboard (FastAPI).

  /            the dashboard page (static/index.html)
  /api/...     JSON endpoints the page uses; they call the SAME services.py
               functions as Telegram, so the rules are identical.
  /healthz     used by Railway to check the bot is alive (no password)

Security: the dashboard is protected by a password (HTTP Basic auth). Your
browser shows a login box: username "admin", password = DASHBOARD_PASSWORD.
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .. import services as svc
from ..db import Database

STATIC = Path(__file__).parent / "static"


def create_app(db: Database, data: Any, cfg: Any, password: str) -> FastAPI:
    app = FastAPI(title="zenobot_alerts", docs_url=None, redoc_url=None, openapi_url=None)
    basic = HTTPBasic(realm="zenobot_alerts")
    started = time.time()

    def auth(creds: HTTPBasicCredentials = Depends(basic)) -> None:
        if not secrets.compare_digest(creds.password.encode(), password.encode()):
            raise HTTPException(401, "Wrong password", headers={"WWW-Authenticate": 'Basic realm="zenobot_alerts"'})

    @app.exception_handler(svc.ValidationError)
    async def _validation(_: Request, exc: svc.ValidationError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # ------------------------------------------------------------ pages
    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "uptime_s": int(time.time() - started)}

    @app.get("/", dependencies=[Depends(auth)])
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- state
    @app.get("/api/rev", dependencies=[Depends(auth)])
    async def rev() -> dict:
        return {"rev": await db.rev()}

    @app.get("/api/state", dependencies=[Depends(auth)])
    async def state() -> dict:
        return {
            "rev": await db.rev(),
            "now": time.time(),
            "scanner": await db.scanner_settings(),
            "scanner_default": await svc.scanner_default(db),
            "exit_default": await svc.exit_default(db),
            "blacklist": await db.blacklist(),
            "modules": await db.module_settings(),
            "token_alerts": await db.list("token_alerts", ("active", "paused")),
            "exit_alerts": await db.list("exit_alerts", ("active", "paused", "expired")),
            "events": await db.recent_events(60),
            "health": data.status() if data else {},
            "volume_persistent": cfg.volume_persistent,
            "jupiter_key": bool(cfg.jupiter_api_key),
        }

    # ----------------------------------------------------------- scanner
    @app.put("/api/scanner", dependencies=[Depends(auth)])
    async def put_scanner(body: dict = Body(...)) -> dict:
        return await svc.update_scanner(db, body)

    @app.put("/api/scanner-default", dependencies=[Depends(auth)])
    async def put_scanner_default(body: dict = Body(...)) -> dict:
        """Save the form's values to the scanner AND as your default."""
        return await svc.save_scanner_default(db, body)

    @app.post("/api/scanner-default/restore", dependencies=[Depends(auth)])
    async def restore_scanner_default() -> dict:
        return await svc.restore_scanner_default(db)

    @app.put("/api/blacklist", dependencies=[Depends(auth)])
    async def put_blacklist(body: dict = Body(...)) -> dict:
        valid, invalid = await svc.set_blacklist(db, body.get("text", ""))
        return {"blacklist": valid, "invalid": invalid}

    @app.put("/api/modules", dependencies=[Depends(auth)])
    async def put_modules(body: dict = Body(...)) -> dict:
        return await svc.update_modules(db, body)

    # ------------------------------------------------------ token alerts
    @app.post("/api/token-alerts", dependencies=[Depends(auth)])
    async def add_token_alert(body: dict = Body(...)) -> dict:
        aid = await svc.create_token_alert(db, data, body, status="active")
        return await db.get("token_alerts", aid)

    @app.put("/api/token-alerts/{aid}", dependencies=[Depends(auth)])
    async def edit_token_alert(aid: int, body: dict = Body(...)) -> dict:
        return await svc.update_token_alert(db, aid, body, data)

    @app.post("/api/token-alerts/{aid}/status", dependencies=[Depends(auth)])
    async def status_token_alert(aid: int, body: dict = Body(...)) -> dict:
        return await svc.set_token_alert_status(db, aid, body.get("status", ""))

    @app.delete("/api/token-alerts/{aid}", dependencies=[Depends(auth)])
    async def del_token_alert(aid: int) -> dict:
        await svc.delete_alert(db, "token_alerts", aid)
        return {"ok": True}

    # ------------------------------------------------------- exit alerts
    @app.put("/api/exit-default", dependencies=[Depends(auth)])
    async def put_exit_default(body: dict = Body(...)) -> dict:
        """Save the form's indicator settings as the default for new exit alerts."""
        return await svc.save_exit_default(db, body)

    @app.post("/api/exit-alerts", dependencies=[Depends(auth)])
    async def add_exit_alert(body: dict = Body(...)) -> dict:
        aid = await svc.create_exit_alert(db, data, body, status="active")
        return await db.get("exit_alerts", aid)

    @app.put("/api/exit-alerts/{aid}", dependencies=[Depends(auth)])
    async def edit_exit_alert(aid: int, body: dict = Body(...)) -> dict:
        return await svc.update_exit_alert(db, aid, body, data)

    @app.post("/api/exit-alerts/{aid}/status", dependencies=[Depends(auth)])
    async def status_exit_alert(aid: int, body: dict = Body(...)) -> dict:
        return await svc.set_exit_alert_status(db, aid, body.get("status", ""))

    @app.delete("/api/exit-alerts/{aid}", dependencies=[Depends(auth)])
    async def del_exit_alert(aid: int) -> dict:
        await svc.delete_alert(db, "exit_alerts", aid)
        return {"ok": True}

    return app
