"""
Module A — Global Volume Scanner (timeframes: 5m, 1h, 24h).

Every minute:
  1. Get trending Solana tokens for the chosen timeframe.
       * With a GMGN key (recommended): GMGN's trending list, filtered by GMGN
         itself on volume, market cap and TOTAL FEES — the same numbers you see on gmgn.ai.
       * Without a GMGN key: Jupiter's top-traded / top-trending / new lists
         (total fees are then not available and the fees filter is skipped).
  2. Drop SOL/USDC/USDT, blacklisted tokens and tokens still in cooldown.
  3. Double-check volume, market cap and fees ourselves, then send the alert
     and remember the time (cooldown).
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


async def gmgn_candidates(ctx: Ctx, s: dict) -> dict[str, dict]:
    """GMGN trending list with server-side filters (volume, market cap, total fees)."""
    toks = await ctx.data.gmgn_rank(
        s["timeframe"], MODULE,
        min_volume=s["min_volume"],
        min_marketcap=s["min_mcap"],
        max_marketcap=s["max_mcap"],
        min_total_fee=s["min_fees"],
    )
    return {t["mint"]: t for t in toks if t.get("mint")}


async def jupiter_candidates(ctx: Ctx, tf: str) -> dict[str, dict]:
    """Fallback when there is no GMGN key or GMGN is down."""
    cands: dict[str, dict] = {}
    for cat in ("toptraded", "toptrending"):
        try:
            for t in await ctx.data.jup_category(cat, tf, MODULE):
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
    return cands


async def run_cycle(ctx: Ctx) -> float:
    s = await ctx.db.scanner_settings()
    if not s["enabled"]:
        return IDLE_S

    tf = s["timeframe"]
    now = time.time()
    cands: dict[str, dict] = {}
    fees_known = False
    if ctx.data.has_gmgn:
        try:
            cands = await gmgn_candidates(ctx, s)
            fees_known = True
        except SourceError as e:
            log.warning("GMGN scan failed (%s) -> using Jupiter this time", e)
    if not cands and not fees_known:
        cands = await jupiter_candidates(ctx, tf)

    blacklist = set(await ctx.db.blacklist())
    last = await ctx.db.scanner_all_last_alerted()
    cooldown_s = float(s["cooldown_minutes"]) * 60

    passed: list[tuple[dict, float, float | None]] = []
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
        vol = (t.get("volume") or {}).get(tf)
        if vol is None or vol < s["min_volume"]:
            continue
        if s["min_fees"] and fees_known:
            # GMGN already filtered on total fees; if it also sent the number, double-check it
            if t.get("fees") is not None and t["fees"] < s["min_fees"]:
                continue
        passed.append((t, vol, (t.get("change") or {}).get(tf)))

    passed.sort(key=lambda x: x[1], reverse=True)  # biggest volume first
    sent = 0
    for t, vol, chg in passed[:MAX_ALERTS_PER_SCAN]:
        if await ctx.notifier.send(fmt.scanner_alert(t, tf, vol, chg)):
            sent += 1
            await ctx.db.scanner_mark(t["mint"], now)
            await ctx.db.add_event("scanner", f"${t.get('symbol')} — volume {tf} {fmt.usd(vol)}, MC {fmt.usd(t.get('mcap'))}")
    if sent:
        log.info("scanner: %d alert(s) sent from %d candidates", sent, len(cands))
    await ctx.db.scanner_prune()
    return INTERVAL_S
