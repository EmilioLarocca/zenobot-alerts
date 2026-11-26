"""
diag.py — quick health check of your GMGN key and data sources.

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
            df = await data.gmgn_kline(SAMPLE_TOKEN, "5m", 30, "system")
            print(f"\n[GMGN candles] OK — {len(df)} x 5m candles, last close ${df['close'].iloc[-1]:.4f}"
                  if len(df) else "\n[GMGN candles] answered but no candles")
        except SourceError as e:
            print("\n[GMGN candles] FAILED:", e)
    try:
        p = await data.prices_usd([SAMPLE_TOKEN], "system")
        print("\n[Jupiter price] OK — JUP =", p.get(SAMPLE_TOKEN))
    except SourceError as e:
        print("\n[Jupiter price] FAILED:", e)
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


if __name__ == "__main__":
    asyncio.run(fees() if len(sys.argv) > 1 and sys.argv[1] == "fees" else main())
