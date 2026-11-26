"""
main.py — starts everything in ONE process:

    ┌──────────────────────────── asyncio event loop ────────────────────────────┐
    │  Telegram bot (polling)   Web dashboard (uvicorn)   3 alert loops           │
    │          └──────────────── shared SQLite database ─────────────────┘        │
    └─────────────────────────────────────────────────────────────────────────────┘

Run locally:  python main.py      (needs TELEGRAM_BOT_TOKEN and CHAT_ID set)
On Railway:   started automatically (see Dockerfile).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import sys

import uvicorn

from app import services, telegram_ui
from app.config import load_config
from app.context import Ctx
from app.datasources import DataHub
from app.db import Database
from app.modules import exit_alerts, scanner, token_alerts
from app.notifier import Notifier
from app.web.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stdout,
)
for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("zenobot")


async def run_forever(name: str, cycle, ctx: Ctx) -> None:
    """Run one alert module forever. Errors are logged; the loop never dies."""
    await asyncio.sleep(5)  # let the bot finish starting
    while True:
        try:
            wait = await cycle(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("%s loop error (will retry in 30s)", name)
            wait = 30
        await asyncio.sleep(wait)


async def housekeeping(db: Database) -> None:
    while True:
        try:
            await db.delete_old_drafts()
        except Exception:  # noqa: BLE001
            log.exception("housekeeping failed")
        await asyncio.sleep(3600)


async def main() -> None:
    cfg = load_config()
    log.info("Database: %s (persistent volume: %s)", cfg.db_path, cfg.volume_persistent)
    db = await Database.open(cfg.db_path)

    # Dashboard password: from the env var, or generated once and stored
    password = cfg.dashboard_password or await db.get_json("dashboard_password")
    generated = False
    if not password:
        password = secrets.token_urlsafe(9)
        await db.set_json("dashboard_password", password, bump=False)
        generated = True

    data = DataHub(cfg)
    tg = telegram_ui.build_application(cfg, db, data, cfg.public_url, password)
    notifier = Notifier(tg.bot, cfg.chat_id)
    ctx = Ctx(cfg=cfg, db=db, data=data, notifier=notifier)
    services.set_notifier(notifier.send)  # "turned ON/OFF" messages for every toggle

    web = create_app(db, data, cfg, password)
    server = uvicorn.Server(uvicorn.Config(web, host="0.0.0.0", port=cfg.port, log_level="warning",
                                           access_log=False))

    async with tg:
        await tg.start()
        await tg.updater.start_polling(drop_pending_updates=True)
        await telegram_ui.set_commands(tg)

        tasks = [
            asyncio.create_task(run_forever("scanner", scanner.run_cycle, ctx)),
            asyncio.create_task(run_forever("token_alerts", token_alerts.run_cycle, ctx)),
            asyncio.create_task(run_forever("exit_alerts", exit_alerts.run_cycle, ctx)),
            asyncio.create_task(housekeeping(db)),
        ]

        hello = "✅ <b>zenobot_alerts is online.</b> Send /settings to open the control panel."
        if cfg.public_url:
            hello += f"\n🌐 Dashboard: {cfg.public_url}"
        if generated:
            hello += (f"\n🔑 Dashboard login → user <code>admin</code>, password <tg-spoiler>{password}</tg-spoiler>"
                      "\n(set DASHBOARD_PASSWORD in Railway to choose your own)")
        if not cfg.gmgn_api_key:
            hello += ("\nℹ️ No GMGN_API_KEY set: total fees aren't available, so the scanner's fees filter is off. "
                      "Add the key with <code>zenobot edit</code>.")
        if not cfg.volume_persistent:
            hello += "\n⚠️ No persistent volume: settings will reset on every deploy. Attach a Railway Volume at /data."
        await notifier.send(hello)
        log.info("zenobot_alerts running — web on port %s", cfg.port)

        try:
            await server.serve()  # runs until Railway stops the service (SIGTERM)
        finally:
            log.info("shutting down…")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await tg.updater.stop()
            await tg.stop()
            await data.close()
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())
