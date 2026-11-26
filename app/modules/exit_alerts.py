"""
Module C — Exit Position Alert (RSI / Bollinger Bands on LIVE candles).

Every 10 seconds, for each active exit alert:
  1. Get the live USD price (Jupiter, one batched request for all alerts).
  2. Get OHLCV candles: GMGN (real-time, needs GMGN_API_KEY), otherwise
     GeckoTerminal, otherwise Meteora DLMM (5m/1h/4h only).
  3. Put the live price into the current unfinished candle and compute
     RSI / Bollinger Bands with pandas-ta.
  4. If the condition is met -> alert immediately. While it stays met, a
     reminder is sent every REMINDER_MINUTES (default 5). Auto-expires at the end.

There are deliberately NO extra confirmation filters: the alert reacts to the
live value as fast as possible so you can exit in profit.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .. import formatting as fmt
from ..context import Ctx
from ..datasources import TF_SECONDS, SourceError
from ..indicators import evaluate, merge_live_price, rescale_to_price

log = logging.getLogger("zenobot.exit_alerts")

MODULE = "exit"
INTERVAL_S = 10           # how often every exit alert is re-checked
IDLE_S = 10
REMINDER_MINUTES = 5      # while the condition holds, remind at most this often


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


async def check_alert(ctx: Ctx, a: dict, live_price: float | None, now: float) -> None:
    tf = a["timeframe"]
    need = candles_needed(a)
    # plenty of history so RSI matches charting sites (Wilder smoothing needs warm-up)
    limit = min(1000, max(need * 4, 150))

    cs = await ctx.data.candles(a["ca"], tf, limit, MODULE)
    df = cs.df
    if df.empty:
        raise SourceError("no candles returned")
    if live_price:
        if cs.relative:
            df = rescale_to_price(df, live_price)
        df = merge_live_price(df, live_price, TF_SECONDS[tf], now)
    if len(df) < need:
        raise SourceError(f"only {len(df)} candles exist so far, the indicator needs {need}")

    # pandas-ta runs in a worker thread so the Telegram bot never freezes
    res = await asyncio.to_thread(evaluate, a, df)

    fields: dict = {"last_rsi": res["rsi"], "last_bb_pct": res["bb_pct"], "last_price": res["price"],
                    "last_checked": now, "last_error": None}
    if res["triggered"]:
        last = a.get("last_triggered")
        if not last or now - float(last) >= REMINDER_MINUTES * 60:
            await fire(ctx, a, res, cs, now, fields, reminder=bool(last and a.get("armed") == 0))
    else:
        fields["armed"] = 1  # condition cleared: the next hit is a fresh alert, not a reminder
    await ctx.db.update("exit_alerts", a["id"], fields)


async def fire(ctx: Ctx, a: dict, res: dict, cs, now: float, fields: dict, reminder: bool) -> None:
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
    text = fmt.exit_alert(a, res, tok, "; ".join(cs.notes), pool, reminder=reminder)
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
