"""
diag.py — quick health check of your data sources (GMGN is only used for total fees).

Run on the server with:   zenobot test
It prints what GMGN returns so you can see that the key works and which
fields (like total fees) are present. Nothing is changed.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from .config import load_config
from .datasources import DataHub, SourceError

SAMPLE_TOKEN = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"  # JUP, always trading


async def main() -> None:
    cfg = load_config(require_telegram=False)
    data = DataHub(cfg)
    print("GMGN key configured:", "yes" if cfg.gmgn_api_key else "NO")
    if data.has_gmgn:
        print("(The running bot shares this key; waiting 5 s so the test doesn't trip GMGN's limit…)")
        await asyncio.sleep(5)
        try:
            raw = await data._gmgn("/v1/market/rank", {"chain": "sol", "interval": "5m", "limit": 3,
                                                       "order_by": "volume", "direction": "desc"}, "rank")
            items = raw.get("rank") if isinstance(raw, dict) else raw
            if items:
                first = items[0]
                print("\n[GMGN trending] OK —", len(items), "tokens. First:", first.get("symbol"))
                fee_keys = [k for k in first if "fee" in k.lower()]
                print("  fee fields returned:", {k: first[k] for k in fee_keys} or "none")
                print("  all fields:", ", ".join(sorted(first.keys())))
            else:
                print("\n[GMGN trending] answered but returned no tokens:", json.dumps(raw)[:300])
        except SourceError as e:
            print("\n[GMGN trending] FAILED:", e)
            print("  -> AUTH_IP_BLOCKED: add this server's IP (zenobot ip) to your GMGN key's whitelist.")
            print("  -> 429: GMGN's per-second limit was hit; wait 5 minutes and run the test again.")
        await asyncio.sleep(3)
    try:
        p = await data.prices_usd([SAMPLE_TOKEN], "system")
        print("\n[Jupiter price] OK — JUP =", p.get(SAMPLE_TOKEN))
    except SourceError as e:
        print("\n[Jupiter price] FAILED:", e)
    try:
        cs = await data.candles(SAMPLE_TOKEN, "5m", 50, "system")
        print(f"\n[Candle history] OK — {len(cs.df)} x 5m candles from {cs.source}")
    except SourceError as e:
        print("\n[Candle history] FAILED:", e, "(GeckoTerminal may be rate limiting; it recovers by itself)")
    await data.close()


async def fees() -> None:
    """Compare GMGN's gas_fee for the same tokens across 5m / 1h / 24h windows.
    If the number is the same in every window it's a lifetime total (like gmgn.ai's
    "Total Fees"); if it changes, it only covers that window."""
    cfg = load_config(require_telegram=False)
    data = DataHub(cfg)
    if not data.has_gmgn:
        print("No GMGN key configured.")
        return
    print("Asking GMGN (takes ~10 s)…")
    await asyncio.sleep(3)
    windows = ("5m", "1h", "24h")
    by_win: dict[str, dict[str, dict]] = {}
    for w in windows:
        try:
            raw = await data._gmgn("/v1/market/rank", {"chain": "sol", "interval": w, "limit": 100,
                                                       "order_by": "volume", "direction": "desc"}, "rank")
            items = raw.get("rank") if isinstance(raw, dict) else raw
            by_win[w] = {i["address"]: i for i in items or [] if isinstance(i, dict) and i.get("address")}
        except SourceError as e:
            print(f"{w}: FAILED {e}")
            by_win[w] = {}
        await asyncio.sleep(2)
    common = [a for a in by_win["1h"] if all(a in by_win[w] for w in windows)][:5]
    if not common:
        common = list(by_win["1h"])[:5]
    print(f"\nChecked at {time.strftime('%H:%M:%S')} — open these on gmgn.ai NOW and compare 'Total Fees':\n")
    print(f"{'TOKEN':<14}{'gas_fee 5m':>14}{'gas_fee 1h':>14}{'gas_fee 24h':>14}   address")
    for a in common:
        sym = (by_win["1h"].get(a) or {}).get("symbol", "?")[:13]
        vals = []
        for w in windows:
            v = (by_win[w].get(a) or {}).get("gas_fee")
            vals.append(f"{v:>14.2f}" if isinstance(v, (int, float)) else f"{'-':>14}")
        print(f"{sym:<14}{''.join(vals)}   {a}")
    await data.close()


async def scan() -> None:
    """Run ONE scanner pass exactly like the bot, but only print the result (no alerts)."""
    from types import SimpleNamespace

    from .datasources import BASE_TOKENS
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
    tf = s["timeframe"]
    cands = await sc.collect_candidates(ctx, tf)
    print(f"\nJupiter/GeckoTerminal candidates: {len(cands)}")
    blacklist = set(await db.blacklist())
    last = await db.scanner_all_last_alerted()
    now = time.time()
    rows = []
    for m, t in cands.items():
        vol = (t.get("volume") or {}).get(tf) or 0
        mc = t.get("mcap") or 0
        why = []
        if m in BASE_TOKENS:
            continue
        if m in blacklist:
            why.append("blacklisted")
        if s["cooldown_minutes"] and m in last and now - last[m] < s["cooldown_minutes"] * 60:
            why.append("cooldown")
        if (s["min_mcap"] and mc < s["min_mcap"]) or (s["max_mcap"] and mc > s["max_mcap"]):
            why.append("market cap out of range")
        if vol < s["min_volume"]:
            why.append("volume too low")
        rows.append((vol, t, why))
    rows.sort(key=lambda r: r[0], reverse=True)
    fee_map = await sc.gmgn_fee_map(ctx, tf) if any(not r[2] for r in rows) else {}
    if fee_map is None:
        print("GMGN fees: unavailable right now (alerts would show 'Total fees: n/a')")
    print(f"\nTop {min(12, len(rows))} by {tf} volume:")
    for vol, t, why in rows[:12]:
        fees = (fee_map or {}).get(t["mint"])
        if not why and fee_map is not None and s["min_fees_sol"] and (fees is None or fees < s["min_fees_sol"]):
            why.append("fees too low" if fees is not None else "no GMGN fee data")
        fee_txt = f"{fees:>7.1f} SOL" if fees is not None else "      -    "
        print(f"  {t.get('symbol', '?')[:12]:<13} vol {tf} ${vol:>12,.0f}  MC ${(t.get('mcap') or 0):>14,.0f}  "
              f"fees {fee_txt}  -> {'ALERT' if not why else 'skipped: ' + ', '.join(why)}")
    await data.close()
    await db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(fees() if cmd == "fees" else scan() if cmd == "scan" else main())
