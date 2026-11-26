"""
Module C — Exit Position Alert (RSI / Bollinger Bands on LIVE candles).

Every 30 seconds, for each active exit alert:
  1. Get the live USD price (Jupiter, fallback GeckoTerminal).
  2. Get OHLCV candles for the token's most liquid pool (GeckoTerminal,
     fallback Meteora DLMM for 5m/1h/4h).
  3. Safety checks against false signals:
       * candles must be fresh and the token must still be trading,
       * enough history for the indicator lengths,
       * live price and last candle must agree (no >50% jump = bad data),
       * the condition must hold on 2 checks in a row (≥20 s apart).
  4. Insert the live price into the current unfinished candle and compute
     RSI / Bollinger Bands with pandas-ta.
  5. Fire once, then re-arm when the condition clears. Auto-expire at the end.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .. import formatting as fmt
from ..context import Ctx
from ..datasources import OHLCV_TTL, TF_SECONDS, SourceError
from ..indicators import evaluate, merge_live_price, min_candles_needed, rescale_to_price

log = logging.getLogger("zenobot.exit_alerts")

MODULE = "exit"
INTERVAL_S = 30
IDLE_S = 15
CONFIRM_CHECKS = 2        # condition must be seen this many times in a row
CONFIRM_MIN_GAP_S = 20    # …with at least this many seconds between sightings
MAX_PRICE_GAP = 0.5       # live price vs last candle may differ by at most 50%
STALE_CANDLES = 60        # no trades for 60 candles = data too stale to judge


async def expire_old(ctx: Ctx, alerts: list[dict], now: float) -> list[dict]:
    alive = []
    for a in alerts:
        if a.get("expires_at") and a["expires_at"] <= now:
            await ctx.db.update("exit_alerts", a["id"], {"status": "expired"})
            name = a.get("symbol") or fmt.short_ca(a["ca"])
            await ctx.notifier.send(f"⌛ Exit alert for <b>${fmt.esc(name)}</b> ({a['timeframe']}) has finished "
                                    f"its monitoring period and is now OFF.")
            await ctx.db.add_event("exit", f"${name} — monitoring period ended")
        else:
            alive.append(a)
    return alive


async def check_alert(ctx: Ctx, a: dict, live_price: float | None, now: float) -> None:
    if not live_price:
        raise SourceError("no live price available right now")
    tf = a["timeframe"]
    tf_s = TF_SECONDS[tf]
    need = min_candles_needed(a)
    limit = min(1000, max(need * 3, 150))  # extra history makes RSI match charting sites

    cs = await ctx.data.candles(a["ca"], tf, limit, MODULE)
    df = cs.df
    if df.empty:
        raise SourceError("no candles returned")
    if now - cs.fetched_at > OHLCV_TTL[tf] * 4:
        raise SourceError("candle data is not fresh")
    if now - float(df["ts"].iloc[-1]) > tf_s * STALE_CANDLES:
        raise SourceError("no trades for a long time — skipping to avoid false signals")
    if cs.relative:
        df = rescale_to_price(df, live_price)
    else:
        last_close = float(df["close"].iloc[-1])
        if last_close > 0 and abs(live_price / last_close - 1) > MAX_PRICE_GAP:
            raise SourceError("live price and candles disagree (>50%) — waiting for fresh data")
    df = merge_live_price(df, live_price, tf_s, now)
    if len(df) < need:
        raise SourceError(f"only {len(df)} candles of history, need {need} (token too new for these settings)")

    # pandas-ta runs in a worker thread so the Telegram bot never freezes
    res = await asyncio.to_thread(evaluate, a, df)

    fields: dict = {"last_rsi": res["rsi"], "last_bb_pct": res["bb_pct"], "last_price": live_price,
                    "last_checked": now, "last_error": None}
    if res["triggered"] and a["armed"]:
        count = int(a.get("pending_count") or 0)
        since = a.get("pending_since")
        if count == 0:
            fields.update(pending_count=1, pending_since=now)
        elif now - float(since or now) >= CONFIRM_MIN_GAP_S * count:
            count += 1
            fields["pending_count"] = count
        if fields.get("pending_count", count) >= CONFIRM_CHECKS:
            await fire(ctx, a, res, cs, now, fields)
    elif not res["triggered"]:
        fields.update(pending_count=0, pending_since=None)
        if not a["armed"]:
            fields["armed"] = 1  # condition cleared -> can alert again on the next exit signal
    await ctx.db.update("exit_alerts", a["id"], fields)


async def fire(ctx: Ctx, a: dict, res: dict, cs, now: float, fields: dict) -> None:
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
    note = "; ".join(cs.notes)
    text = fmt.exit_alert(a, res, tok, note, pool)
    if await ctx.notifier.send(text, photo=(tok or {}).get("icon")):
        fields.update(armed=0, last_triggered=now, pending_count=0, pending_since=None)
        parts = []
        if res.get("rsi") is not None:
            parts.append(f"RSI {res['rsi']:.1f}")
        if res.get("bb_pct") is not None:
            parts.append(f"BB {fmt.pct(res['bb_pct'])}")
        await ctx.db.add_event("exit", f"${a.get('symbol') or fmt.short_ca(a['ca'])} ({a['timeframe']}) — "
                                       + ", ".join(parts))


async def run_cycle(ctx: Ctx) -> float:
    mods = await ctx.db.module_settings()
    if not mods["exit_alerts_enabled"]:
        return IDLE_S
    now = time.time()
    alerts = await expire_old(ctx, await ctx.db.list("exit_alerts", ("active",)), now)
    if not alerts:
        return IDLE_S
    prices = await ctx.data.prices_usd(list(dict.fromkeys(a["ca"] for a in alerts)), MODULE)
    for a in alerts:
        try:
            await check_alert(ctx, a, prices.get(a["ca"]), time.time())
        except (SourceError, ValueError) as e:
            await ctx.db.update("exit_alerts", a["id"], {"last_checked": time.time(), "last_error": str(e)},
                                bump=False)
        except Exception as e:  # noqa: BLE001
            log.exception("exit alert %s crashed", a["id"])
            await ctx.db.update("exit_alerts", a["id"], {"last_error": f"internal error: {e}"}, bump=False)
    return INTERVAL_S
