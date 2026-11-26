"""
diag.py — quick health check of your data sources (GMGN is only used for total fees).

Run on the server with:   zenobot test
It checks every source the bot uses: total fees (Metlex, GMGN backup), Jupiter
prices, Jupiter chart candles and the GeckoTerminal backup. Nothing is changed.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from .config import load_config
from .datasources import METLEX_FEES_URL, DataHub, SourceError, _f, unwrap_gmgn

SAMPLE_TOKEN = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"  # JUP, always trading


async def main() -> None:
    cfg = load_config(require_telegram=False)
    data = DataHub(cfg)
    print("GMGN key configured:", "yes" if cfg.gmgn_api_key else "NO")

    # Total fees: Metlex first (what the scanner uses), GMGN as backup
    try:
        raw = await data._get("metlex", METLEX_FEES_URL, {"address": SAMPLE_TOKEN}, "system", wait=10)
        inner = unwrap_gmgn(raw) if isinstance(raw, dict) else None
        v = _f(inner.get("total_fee")) if isinstance(inner, dict) else None
        if v is not None:
            print(f"\n[Total fees · Metlex] OK — JUP total fees = {v:,.2f} SOL (the scanner uses this)")
        else:
            print("\n[Total fees · Metlex] answered without total_fee:", json.dumps(raw)[:200])
    except SourceError as e:
        print("\n[Total fees · Metlex] FAILED:", e, "-> the scanner uses GMGN instead")
    if data.has_gmgn:
        print("(The running bot shares the GMGN key; waiting 3 s so the test doesn't trip GMGN's limit…)")
        await asyncio.sleep(3)
        try:
            info = await data._gmgn("/v1/token/info", {"chain": "sol", "address": SAMPLE_TOKEN}, "token_info")
            v = _f(info.get("total_fee")) if isinstance(info, dict) else None
            if v is not None:
                print(f"[Total fees · GMGN backup] OK — JUP total fees = {v:,.2f} SOL")
            else:
                print("[Total fees · GMGN backup] answered without total_fee:", json.dumps(info)[:200])
        except SourceError as e:
            print("[Total fees · GMGN backup] FAILED:", e)
            print("  -> AUTH_IP_BLOCKED: add this server's IP (zenobot ip) to your GMGN key's whitelist.")
            print("  -> 429: GMGN's per-second limit was hit; wait a minute and run the test again.")
    else:
        print("[Total fees · GMGN backup] skipped — no GMGN key (Metlex alone is used)")
    try:
        p = await data.prices_usd([SAMPLE_TOKEN], "system")
        print("\n[Jupiter price] OK — JUP =", p.get(SAMPLE_TOKEN))
    except SourceError as e:
        print("\n[Jupiter price] FAILED:", e)
    try:
        cs = await data.jup_chart(SAMPLE_TOKEN, "5m", 50, "system")
        last = cs.df.iloc[-1]
        print(f"\n[Jupiter chart candles] OK — {len(cs.df)} x 5m candles, last close ${last['close']:.4f}"
              " (exit alerts use these)")
    except SourceError as e:
        print("\n[Jupiter chart candles] FAILED:", e)
        print("  -> exit alerts automatically use GeckoTerminal candles instead.")
    try:
        cs = await data.candles(SAMPLE_TOKEN, "5m", 50, "system")
        print(f"\n[Backup candles] OK — {len(cs.df)} x 5m candles from {cs.source}")
    except SourceError as e:
        print("\n[Backup candles] FAILED:", e, "(GeckoTerminal may be rate limiting; it recovers by itself)")
    await data.close()


async def scan() -> None:
    """Run ONE scanner pass exactly like the bot (same code), but only print the result."""
    from types import SimpleNamespace

    from .db import Database
    from .modules import scanner as sc

    cfg = load_config(require_telegram=False)
    db = await Database.open(cfg.db_path)
    data = DataHub(cfg)
    ctx = SimpleNamespace(cfg=cfg, db=db, data=data)
    s = await db.scanner_settings()
    print(f"Scanner is {'ON 🟢' if s['enabled'] else 'OFF ⚪  <- turn it ON in Telegram /settings or the dashboard'}")
    print(f"Settings: timeframe {s['timeframe']} · min volume ${s['min_volume']:,.0f} · market cap "
          f"${s['min_mcap']:,.0f}–{'$' + format(s['max_mcap'], ',.0f') if s['max_mcap'] else 'no max'} · "
          f"min fees {s['min_fees_sol']:g} SOL · cooldown {s['cooldown_minutes']} min")
    print("Scanning (takes ~10–30 s)…")
    final, skipped, info = await sc.evaluate(ctx, s, time.time())
    tf = s["timeframe"]
    print("\nSources: " + ", ".join(f"{k} {v}" for k, v in info["sources"].items()))
    print(f"Unique tokens checked: {info['candidates']}")
    if info.get("floor") is not None:
        print(f"Completeness: Jupiter's top-100 by {tf} volume goes down to ${info['floor']:,.0f} — "
              + ("below your threshold, so EVERY token above it was checked ✅" if info.get("complete")
                 else "ABOVE your threshold, so a few tokens may be missing right now ⚠️"))
    print(f"\nWOULD ALERT ({len(final)}):")
    for t, vol, _ in final[:12]:
        fees = f"{t['fees_sol']:.1f} SOL ({t.get('fees_src')})" if t.get("fees_sol") is not None else "n/a"
        print(f"  {t.get('symbol', '?')[:12]:<13} vol {tf} ${vol:>12,.0f}  MC ${(t.get('mcap') or 0):>14,.0f}  fees {fees}")
    near = sorted(skipped, key=lambda x: x[1], reverse=True)[:10]
    print(f"\nBiggest skipped by {tf} volume:")
    for t, vol, why in near:
        print(f"  {t.get('symbol', '?')[:12]:<13} vol {tf} ${vol:>12,.0f}  MC ${(t.get('mcap') or 0):>14,.0f}  -> {why}")
    await data.close()
    await db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(scan() if cmd == "scan" else main())
