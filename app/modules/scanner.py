"""
Module A — Global Volume Scanner (timeframes: 5m, 1h, 24h).

Every minute:
  1. Collect candidate tokens from Jupiter (top traded, top trending, recently
     listed) and GeckoTerminal (trending + new pools, refreshed every 5 min).
     Volume, price change and market cap come from Jupiter.
  2. Drop SOL/USDC/USDT, blacklisted tokens and tokens still in cooldown.
  3. Keep tokens whose market cap is inside your range and whose volume in the
     chosen timeframe is above your threshold.
  4. Total fees (SOL, the gmgn.ai "Total Fees" number) come from GMGN — the
     ONLY thing GMGN is used for. Only asked when at least one token passed step 3.
  5. Send the alert and remember the time (cooldown).

If GMGN is unavailable during a scan, alerts are still sent and show
"Total fees: n/a" (the fees filter can't be checked that time).
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


async def collect_candidates(ctx: Ctx, tf: str) -> dict[str, dict]:
    """Jupiter lists + GeckoTerminal trending/new pools (enriched with Jupiter data)."""
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


async def gmgn_fee_map(ctx: Ctx, tf: str) -> dict[str, float] | None:
    """
    mint -> lifetime total fees in SOL, from GMGN's trending lists.
    Returns None when GMGN couldn't be reached at all (or there's no key).
    """
    if not ctx.data.has_gmgn:
        return None
    fees: dict[str, float] = {}
    reached = False
    for iv in dict.fromkeys((tf, "1h", "24h")):
        try:
            for t in await ctx.data.gmgn_rank(iv, MODULE):
                if t.get("fees_sol") is not None:
                    fees[t["mint"]] = max(fees.get(t["mint"], 0.0), t["fees_sol"])
            reached = True
        except SourceError as e:
            log.info("GMGN fees (%s) unavailable: %s", iv, e)
    return fees if reached else None


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
        passed.append((dict(t), vol, (t.get("change") or {}).get(tf)))

    if not passed:
        await ctx.db.scanner_prune()
        return INTERVAL_S

    # --- total fees from GMGN (only for tokens that passed everything else)
    fee_map = await gmgn_fee_map(ctx, tf)
    sol_usd = None
    if fee_map:
        try:
            sol_usd = await ctx.data.sol_usd(MODULE)
        except SourceError:
            pass
    final: list[tuple[dict, float, float | None]] = []
    for t, vol, chg in passed:
        t["fees"] = None  # don't show Jupiter's own fee figure
        if fee_map is not None:
            t["fees_sol"] = fee_map.get(t["mint"])
            if t["fees_sol"] is not None and sol_usd:
                t["fees"] = t["fees_sol"] * sol_usd
            # GMGN answered: a token it doesn't list, or with fees below your minimum, is skipped
            if s["min_fees_sol"] and (t["fees_sol"] is None or t["fees_sol"] < s["min_fees_sol"]):
                continue
        final.append((t, vol, chg))

    final.sort(key=lambda x: x[1], reverse=True)  # biggest volume first
    sent = 0
    for t, vol, chg in final[:MAX_ALERTS_PER_SCAN]:
        if await ctx.notifier.send(fmt.scanner_alert(t, tf, vol, chg)):
            sent += 1
            await ctx.db.scanner_mark(t["mint"], now)
            await ctx.db.add_event("scanner", f"${t.get('symbol')} — volume {tf} {fmt.usd(vol)}, MC {fmt.usd(t.get('mcap'))}")
    if sent:
        log.info("scanner: %d alert(s) sent from %d candidates", sent, len(cands))
    await ctx.db.scanner_prune()
    return INTERVAL_S
