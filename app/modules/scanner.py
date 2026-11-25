"""
Module A — Global Volume Scanner.

Every minute:
  1. Collect candidate tokens from Jupiter (top traded, top trending, recently
     listed) and GeckoTerminal (trending + new pools, refreshed every 5 min).
  2. Drop SOL/USDC/USDT, blacklisted tokens and tokens still in cooldown.
  3. Keep tokens whose market cap is inside your range and whose volume in the
     chosen timeframe is above your threshold (and fees, if you set a minimum).
  4. Send an alert and remember the time (cooldown).

1-minute volume isn't published by Jupiter, so for the 1m timeframe the bot
pre-filters with 5m volume and then checks real 1m candles for the best few
candidates (GeckoTerminal budget is small).
"""
from __future__ import annotations

import logging
import time

from .. import formatting as fmt
from ..context import Ctx
from ..datasources import BASE_TOKENS, SourceError

log = logging.getLogger("zenobot.scanner")

MODULE = "scanner"
INTERVAL_S = 60            # how often a scan runs
IDLE_S = 15                # how often to re-check when the module is OFF
MAX_ALERTS_PER_SCAN = 8    # safety cap so a market-wide pump can't flood you
MAX_1M_CHECKS = 3          # 1m candle look-ups per scan (GeckoTerminal is slow/limited)
JUP_INTERVAL = {"1m": "5m", "5m": "5m", "1h": "1h", "24h": "24h"}


async def collect_candidates(ctx: Ctx, tf: str) -> dict[str, dict]:
    cands: dict[str, dict] = {}
    iv = JUP_INTERVAL[tf]
    for cat in ("toptraded", "toptrending"):
        try:
            for t in await ctx.data.jup_category(cat, iv, MODULE):
                if t.get("mint"):
                    cands.setdefault(t["mint"], t)
        except SourceError as e:
            log.info("Jupiter %s failed: %s", cat, e)
    try:
        for t in await ctx.data.jup_recent(MODULE):
            if t.get("mint"):
                cands.setdefault(t["mint"], t)
    except SourceError as e:
        log.info("Jupiter recent failed: %s", e)

    # GeckoTerminal trending/new pools -> only mints; enrich them with Jupiter data
    extra: list[str] = []
    for kind in ("trending_pools", "new_pools"):
        try:
            extra += await ctx.data.gecko_pool_list(kind, MODULE)
        except SourceError as e:
            log.info("Gecko %s failed: %s", kind, e)
    extra = [m for m in dict.fromkeys(extra) if m not in cands]
    if extra:
        try:
            cands.update(await ctx.data.jup_tokens(extra[:100], MODULE))
        except SourceError as e:
            log.info("Jupiter enrichment failed: %s", e)
    return cands


async def run_cycle(ctx: Ctx) -> float:
    s = await ctx.db.scanner_settings()
    if not s["enabled"]:
        return IDLE_S

    tf = s["timeframe"]
    now = time.time()
    cands = await collect_candidates(ctx, tf)
    blacklist = set(await ctx.db.blacklist())
    last = await ctx.db.scanner_all_last_alerted()
    cooldown_s = float(s["cooldown_minutes"]) * 60

    passed: list[tuple[dict, float, float | None]] = []
    one_minute_queue: list[dict] = []
    for mint, t in cands.items():
        if mint in BASE_TOKENS or mint in blacklist:
            continue
        if cooldown_s and mint in last and now - last[mint] < cooldown_s:
            continue
        mcap = t.get("mcap")
        if s["min_mcap"] and (mcap is None or mcap < s["min_mcap"]):
            continue
        if s["max_mcap"] and (mcap is None or mcap > s["max_mcap"]):
            continue
        if tf == "1m":
            v5 = (t.get("volume") or {}).get("5m")
            if v5 is not None and v5 >= s["min_volume"]:  # 1m volume can never exceed 5m volume
                one_minute_queue.append(t)
            continue
        vol = (t.get("volume") or {}).get(tf)
        if vol is None or vol < s["min_volume"]:
            continue
        passed.append((t, vol, (t.get("change") or {}).get(tf)))

    if one_minute_queue:
        one_minute_queue.sort(key=lambda t: t["volume"].get("5m") or 0, reverse=True)
        for t in one_minute_queue[:MAX_1M_CHECKS]:
            try:
                vol, chg = await ctx.data.volume_1m(t["mint"], MODULE)
            except SourceError as e:
                log.info("1m volume unavailable for %s: %s", t.get("symbol"), e)
                continue
            if vol is not None and vol >= s["min_volume"]:
                passed.append((t, vol, chg))

    passed.sort(key=lambda x: x[1], reverse=True)  # biggest volume first
    sent = 0
    for t, vol, chg in passed:
        if sent >= MAX_ALERTS_PER_SCAN:
            break
        dlmm_fees = await ctx.data.dlmm_lifetime_fees(t["mint"], MODULE)
        if s["min_fees"]:
            fee_value = t.get("fees") if s["fees_source"] == "jupiter" else dlmm_fees
            if fee_value is None or fee_value < s["min_fees"]:
                continue
        if await ctx.notifier.send(fmt.scanner_alert(t, tf, vol, chg, dlmm_fees)):
            sent += 1
            await ctx.db.scanner_mark(t["mint"], now)
            await ctx.db.add_event("scanner", f"${t.get('symbol')} — volume {tf} {fmt.usd(vol)}, MC {fmt.usd(t.get('mcap'))}")
    if sent:
        log.info("scanner: %d alert(s) sent from %d candidates", sent, len(cands))
    await ctx.db.scanner_prune()
    return INTERVAL_S
