"""
Module A — Global Volume Scanner (timeframes: 5m, 1h, 24h).

Every 10 seconds (like your previous bot):
  1. Get Jupiter's lists for your timeframe: "top trending" (the list your previous
     bot used, read through Metlex like before) and "top traded" (the 100 tokens with
     the most volume, read directly from Jupiter). Each list falls back to the other
     route if one doesn't answer. Volume, price change and
     market cap all come from Jupiter.
  2. Completeness check: if the 100th token of "top traded" has LESS volume than
     your threshold, every token above your threshold is guaranteed to be in the
     list. (If not, a warning is logged — that only happens in extreme markets.)
  3. Drop SOL/USDC/USDT, blacklisted tokens and tokens still in cooldown, then
     apply your market-cap range and volume threshold.
  4. TOTAL FEES (SOL) for each remaining token, exactly like your previous bot:
     Metlex `total_fee` first, GMGN as backup. If neither answers, the alert is
     still sent with "Total fees: n/a".
  5. Send the alert and remember the time (cooldown).
"""
from __future__ import annotations

import asyncio
import logging
import time

from .. import formatting as fmt
from ..context import Ctx
from ..datasources import BASE_TOKENS, SourceError

log = logging.getLogger("zenobot.scanner")

MODULE = "scanner"
INTERVAL_S = 10            # how often a scan runs (same as your previous bot)
IDLE_S = 10                # how often to re-check when the module is OFF
MAX_ALERTS_PER_SCAN = 8    # safety cap so a market-wide pump can't flood you
MAX_FEE_LOOKUPS = 20       # per scan, biggest volume first


async def collect_candidates(ctx: Ctx, s: dict) -> tuple[dict[str, dict], dict]:
    """Jupiter top traded + top trending. Returns (mint -> token info, info about the lists)."""
    tf = s["timeframe"]
    cands: dict[str, dict] = {}
    info: dict = {"sources": {}, "floor": None, "complete": None}
    # toptrending = exactly what your previous bot read (via Metlex first);
    # toptraded = sorted by volume, used for the completeness guarantee (via Jupiter first)
    for cat, prefer in (("toptraded", "jupiter"), ("toptrending", "metlex")):
        try:
            toks = await ctx.data.jup_category(cat, tf, MODULE, prefer=prefer)
        except SourceError as e:
            log.warning("Jupiter %s failed: %s", cat, e)
            continue
        info["sources"][f"jupiter {cat}"] = len(toks)
        for t in toks:
            if t.get("mint"):
                cands.setdefault(t["mint"], t)
        if cat == "toptraded" and toks:
            vols = [(t.get("volume") or {}).get(tf) or 0.0 for t in toks]
            info["floor"] = min(vols)
            # a list shorter than 100 already contains every traded token
            info["complete"] = len(toks) < 100 or info["floor"] < s["min_volume"]
    if info["complete"] is False:
        log.warning("Jupiter's top-100 list ends at %.0f USD volume, above your threshold: "
                    "a few tokens above the threshold may be missing this minute", info["floor"])
    return cands, info


async def evaluate(ctx: Ctx, s: dict, now: float) -> tuple[list[tuple[dict, float, float | None]],
                                                        list[tuple[dict, float, str]], dict]:
    """
    Run the full filter chain. Returns (to_alert, skipped_with_reason, info).
    Used by the bot AND by `zenobot scan`, so the diagnostic shows exactly what the bot does.
    """
    tf = s["timeframe"]
    cands, list_info = await collect_candidates(ctx, s)
    blacklist = set(await ctx.db.blacklist())
    last = await ctx.db.scanner_all_last_alerted()
    cooldown_s = float(s["cooldown_minutes"]) * 60

    passed: list[tuple[dict, float, float | None]] = []
    skipped: list[tuple[dict, float, str]] = []
    for mint, t in cands.items():
        if mint in BASE_TOKENS:
            continue
        vol = (t.get("volume") or {}).get(tf)
        mcap = t.get("mcap")
        why = []
        if mint in blacklist:
            why.append("blacklisted")
        if cooldown_s and mint in last and now - last[mint] < cooldown_s:
            why.append("cooldown")
        if (s["min_mcap"] and (mcap is None or mcap < s["min_mcap"])) or \
           (s["max_mcap"] and (mcap is None or mcap > s["max_mcap"])):
            why.append("market cap out of range")
        if vol is None or vol < s["min_volume"]:
            why.append("volume too low")
        if why:
            skipped.append((t, vol or 0.0, ", ".join(why)))
        else:
            passed.append((dict(t), vol, (t.get("change") or {}).get(tf)))
    passed.sort(key=lambda x: x[1], reverse=True)

    # total fees, one lookup per remaining token (in parallel)
    to_check = passed[:MAX_FEE_LOOKUPS]
    results = await asyncio.gather(*(ctx.data.token_total_fees(t["mint"], MODULE) for t, _, _ in to_check))
    sol_usd = None
    if any(v is not None for v, _ in results):
        try:
            sol_usd = await ctx.data.sol_usd(MODULE)
        except SourceError:
            pass
    final: list[tuple[dict, float, float | None]] = []
    for (t, vol, chg), (fees, src) in zip(to_check, results):
        t["fees"] = None
        t["fees_sol"] = fees
        t["fees_src"] = src
        if fees is not None and sol_usd:
            t["fees"] = fees * sol_usd
        if s["min_fees_sol"] and fees is not None and fees < s["min_fees_sol"]:
            skipped.append((t, vol, f"fees too low ({fees:.1f} SOL)"))
            continue
        final.append((t, vol, chg))  # fees unknown (no source answered) -> still alerted, shows n/a
    info = {"candidates": len(cands), "sol_usd": sol_usd, **list_info}
    return final, skipped, info


async def run_cycle(ctx: Ctx) -> float:
    s = await ctx.db.scanner_settings()
    if not s["enabled"]:
        return IDLE_S
    now = time.time()
    final, _, info = await evaluate(ctx, s, now)
    tf = s["timeframe"]
    sent = 0
    for t, vol, chg in final[:MAX_ALERTS_PER_SCAN]:
        if await ctx.notifier.send(fmt.scanner_alert(t, tf, vol, chg)):
            sent += 1
            await ctx.db.scanner_mark(t["mint"], now)
            await ctx.db.add_event("scanner", f"${t.get('symbol')} — volume {tf} {fmt.usd(vol)}, MC {fmt.usd(t.get('mcap'))}")
    if sent:
        log.info("scanner: %d alert(s) sent from %d candidates", sent, info["candidates"])
    await ctx.db.scanner_prune()
    return INTERVAL_S
