"""
Module B — Token-specific Volume / Price / Market-cap alerts.

Every 30 seconds each active alert is checked. An alert fires once when its
condition becomes true, then "re-arms" automatically after the condition is
false again — so you get one message per crossing, not one every 30 seconds.
"""
from __future__ import annotations

import logging
import time

from .. import formatting as fmt
from ..context import Ctx
from ..datasources import SOL_MINT, SourceError

log = logging.getLogger("zenobot.token_alerts")

MODULE = "token"
INTERVAL_S = 30
IDLE_S = 15


async def current_value(ctx: Ctx, a: dict, infos: dict, prices: dict, one_min: dict) -> float | None:
    info = infos.get(a["ca"]) or {}
    if a["metric"] == "price":
        usd = prices.get(a["ca"]) or info.get("price_usd")
        if usd is None:
            return None
        if a.get("currency") == "sol":
            sol = prices.get(SOL_MINT)
            return usd / sol if sol else None
        return usd
    if a["metric"] == "mcap":
        return info.get("mcap")
    # volume
    tf = a.get("timeframe") or "5m"
    if tf == "1m":
        if a["ca"] not in one_min:
            one_min[a["ca"]] = (await ctx.data.volume_1m(a["ca"], MODULE))[0]
        return one_min[a["ca"]]
    return (info.get("volume") or {}).get(tf)


async def run_cycle(ctx: Ctx) -> float:
    mods = await ctx.db.module_settings()
    if not mods["token_alerts_enabled"]:
        return IDLE_S
    alerts = await ctx.db.list("token_alerts", ("active",))
    if not alerts:
        return IDLE_S

    mints = list(dict.fromkeys(a["ca"] for a in alerts))
    # one batched request gives market cap, 5m/1h/24h volume and names for all tokens
    infos = await ctx.data.token_infos(mints, MODULE)
    prices: dict = {}
    if any(a["metric"] == "price" for a in alerts):
        prices = await ctx.data.prices_usd(mints + [SOL_MINT], MODULE)

    now = time.time()
    one_min: dict = {}
    for a in alerts:
        try:
            value = await current_value(ctx, a, infos, prices, one_min)
            err = None if value is not None else "no data from any source right now"
        except SourceError as e:
            value, err = None, str(e)
        except Exception as e:  # noqa: BLE001 - one bad alert must not stop the others
            log.exception("token alert %s failed", a["id"])
            value, err = None, f"internal error: {e}"
        if value is None:
            await ctx.db.update("token_alerts", a["id"], {"last_checked": now, "last_error": err}, bump=False)
            continue

        hit = value >= a["threshold"] if a["direction"] == "above" else value <= a["threshold"]
        fields: dict = {"last_value": value, "last_checked": now, "last_error": None}
        if hit and a["armed"]:
            if await ctx.notifier.send(fmt.token_alert(a, value, infos.get(a["ca"]))):
                fields.update(armed=0, last_triggered=now)
                await ctx.db.add_event(
                    "token", f"${a.get('symbol') or fmt.short_ca(a['ca'])} — {fmt.metric_label(a)} "
                             f"{fmt.metric_value(a, value)}")
        elif not hit and not a["armed"]:
            fields["armed"] = 1  # condition cleared -> ready to fire again next time
        await ctx.db.update("token_alerts", a["id"], fields)
    return INTERVAL_S
