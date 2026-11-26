"""
Module C — Exit Position Alert (RSI / Bollinger Bands on LIVE candles).

Every 10 seconds, for each active exit alert:
  1. Get the live USD price (Jupiter, one batched request for all alerts).
  2. Candles are built LOCALLY: the history (USD) is downloaded from Jupiter's chart
     data — all pools combined, the same candles as the jup.ag chart — with
     GeckoTerminal (then Meteora DLMM, 5m/1h/4h) as backup. It is re-synced every
     RESYNC_MINUTES (2 min from Jupiter, 5 min when on the GeckoTerminal backup);
     between syncs every 10-second live price updates the running candle
     (open/high/low/close) and starts new candles on time.
  3. Compute RSI / Bollinger Bands with pandas-ta on those live candles.
  4. If the condition is met -> alert immediately. While it stays met, a
     reminder is sent every REMINDER_MINUTES (default 5). Auto-expires at the end.

There are deliberately NO extra confirmation filters: the alert reacts to the
live value as fast as possible so you can exit in profit.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pandas as pd

from .. import formatting as fmt
from ..context import Ctx
from ..datasources import TF_SECONDS, SourceError
from ..indicators import evaluate, merge_live_price, rescale_to_price

log = logging.getLogger("zenobot.exit_alerts")

MODULE = "exit"
INTERVAL_S = 10           # how often every exit alert is re-checked
IDLE_S = 10
REMINDER_MINUTES = 5      # while the condition holds, remind at most this often
RESYNC_MINUTES = 2        # re-download candle history from Jupiter's chart this often
RESYNC_BACKUP_MINUTES = 5 # ...and this often when on the GeckoTerminal/Meteora backup (lower limits)
MAX_CANDLES = 1000        # keep at most this many candles in memory per alert


def _store(ctx: Ctx) -> dict:
    """In-memory candle store shared by all alerts: (mint, timeframe) -> entry."""
    if not hasattr(ctx.data, "_live_candles"):
        ctx.data._live_candles = {}
    return ctx.data._live_candles


def candles_needed(a: dict) -> int:
    """The bare mathematical minimum for the indicators to produce a number."""
    need = 2
    if a.get("rsi_enabled"):
        need = max(need, int(a["rsi_length"]) + 1)
    if a.get("bb_enabled"):
        need = max(need, int(a["bb_length"]))
    return need


async def expire_old(ctx: Ctx, alerts: list[dict], now: float) -> list[dict]:
    alive = []
    for a in alerts:
        if a.get("expires_at") and a["expires_at"] <= now:
            await ctx.db.update("exit_alerts", a["id"], {"status": "expired"})
            name = a.get("symbol") or fmt.short_ca(a["ca"])
            await ctx.notifier.send(f"⌛ Exit alert #{a['id']} for <b>${fmt.esc(name)}</b> ({a['timeframe']}) has "
                                    f"finished its monitoring period and is now OFF.")
            await ctx.db.add_event("exit", f"${name} — monitoring period ended")
        else:
            alive.append(a)
    return alive


async def live_candles(ctx: Ctx, a: dict, live_price: float | None, now: float):
    """Return (candles DataFrame, notes) with the live price applied to the running candle."""
    tf = a["timeframe"]
    tf_s = TF_SECONDS[tf]
    key = (a["ca"], tf)
    store = _store(ctx)
    entry = store.get(key)
    every = RESYNC_MINUTES if entry is None or entry.get("source") == "jupiter" else RESYNC_BACKUP_MINUTES
    if entry is None or now - entry["synced"] >= every * 60:
        limit = min(MAX_CANDLES, max(candles_needed(a) * 4, 150))
        try:
            cs = await ctx.data.candles(a["ca"], tf, limit, MODULE, jupiter_chart=True)
            df = cs.df
            if cs.relative and live_price:
                df = rescale_to_price(df, live_price)
            if entry is not None and len(entry["df"]) and len(df):
                # keep the candle we built from live prices if it is newer than the download
                newer = entry["df"][entry["df"]["ts"] > int(df["ts"].iloc[-1])]
                if len(newer):
                    df = pd.concat([df, newer], ignore_index=True)
            entry = {"df": df, "synced": now, "notes": cs.notes, "source": cs.source}
            store[key] = entry
        except SourceError as e:
            if entry is None:
                raise SourceError(f"waiting for candle history ({e})")
            entry["synced"] = now - every * 60 + 60  # retry the sync in a minute
            log.info("resync failed for %s %s, continuing on live candles: %s", a["ca"], tf, e)
    df = entry["df"]
    if df.empty:
        raise SourceError("no candles returned")
    if live_price:
        df = merge_live_price(df, live_price, tf_s, now)
        if len(df) > MAX_CANDLES:
            df = df.iloc[-MAX_CANDLES:].reset_index(drop=True)
        entry["df"] = df
    return df, entry.get("notes") or []


async def check_alert(ctx: Ctx, a: dict, live_price: float | None, now: float) -> None:
    need = candles_needed(a)
    df, notes = await live_candles(ctx, a, live_price, now)
    if len(df) < need:
        raise SourceError(f"only {len(df)} candles exist so far, the indicator needs {need}")

    # pandas-ta runs in a worker thread so the Telegram bot never freezes
    res = await asyncio.to_thread(evaluate, a, df)

    fields: dict = {"last_rsi": res["rsi"], "last_bb_pct": res["bb_pct"], "last_price": res["price"],
                    "last_checked": now, "last_error": None}
    if res["triggered"]:
        last = a.get("last_triggered")
        if not last or now - float(last) >= REMINDER_MINUTES * 60:
            await fire(ctx, a, res, notes, now, fields, reminder=bool(last and a.get("armed") == 0))
    else:
        fields["armed"] = 1  # condition cleared: the next hit is a fresh alert, not a reminder
    await ctx.db.update("exit_alerts", a["id"], fields)


async def fire(ctx: Ctx, a: dict, res: dict, notes: list, now: float, fields: dict, reminder: bool) -> None:
    tok = None
    try:
        tok = await ctx.data.token_info(a["ca"], MODULE)
    except SourceError:
        pass
    pool = None
    try:
        bp = await ctx.data.best_pool(a["ca"], MODULE)
        pool = (bp or {}).get("meteora")
    except SourceError:
        pass
    text = fmt.exit_alert(a, res, tok, "; ".join(notes), pool, reminder=reminder)
    text += f"\n🔔 Reminder every {REMINDER_MINUTES} min while the condition holds."
    if await ctx.notifier.send(text, photo=(tok or {}).get("icon")):
        fields.update(armed=0, last_triggered=now)
        parts = []
        if res.get("rsi") is not None:
            parts.append(f"RSI {res['rsi']:.1f}")
        if res.get("bb_pct") is not None:
            parts.append(f"BB {fmt.pct(res['bb_pct'])}")
        await ctx.db.add_event("exit", f"${a.get('symbol') or fmt.short_ca(a['ca'])} ({a['timeframe']}) — "
                                       + ", ".join(parts) + (" (reminder)" if reminder else ""))


async def run_cycle(ctx: Ctx) -> float:
    mods = await ctx.db.module_settings()
    if not mods["exit_alerts_enabled"]:
        return IDLE_S
    now = time.time()
    alerts = await expire_old(ctx, await ctx.db.list("exit_alerts", ("active",)), now)
    if not alerts:
        return IDLE_S
    prices = await ctx.data.prices_usd(list(dict.fromkeys(a["ca"] for a in alerts)), MODULE)
    # forget candles of alerts that are no longer active
    active = {(a["ca"], a["timeframe"]) for a in alerts}
    store = _store(ctx)
    for k in [k for k in store if k not in active]:
        store.pop(k, None)

    async def one(a: dict) -> None:
        try:
            await check_alert(ctx, a, prices.get(a["ca"]), time.time())
        except (SourceError, ValueError) as e:
            await ctx.db.update("exit_alerts", a["id"], {"last_checked": time.time(), "last_error": str(e)},
                                bump=False)
        except Exception as e:  # noqa: BLE001
            log.exception("exit alert %s crashed", a["id"])
            await ctx.db.update("exit_alerts", a["id"], {"last_error": f"internal error: {e}"}, bump=False)

    # all alerts are checked in parallel so 15 alerts take about as long as 1
    await asyncio.gather(*(one(a) for a in alerts))
    return INTERVAL_S
