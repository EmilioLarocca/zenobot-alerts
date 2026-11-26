"""
diag.py — quick health check of your GMGN key and data sources.

Run on the server with:   zenobot test
It prints what GMGN returns so you can see that the key works and which
fields (like total fees) are present. Nothing is changed.
"""
from __future__ import annotations

import asyncio
import json

from .config import load_config
from .datasources import DataHub, SourceError

SAMPLE_TOKEN = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"  # JUP, always trading


async def main() -> None:
    cfg = load_config(require_telegram=False)
    data = DataHub(cfg)
    print("GMGN key configured:", "yes" if cfg.gmgn_api_key else "NO")
    if data.has_gmgn:
        try:
            raw = await data._gmgn("/v1/market/rank", {"chain": "sol", "interval": "5m", "limit": 3,
                                                       "order_by": "volume", "direction": "desc"}, "rank")
            items = (raw or {}).get("rank") if isinstance(raw, dict) else raw
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
            print("  -> If it says AUTH_IP_BLOCKED, add this server's IP to your GMGN key's IP whitelist.")
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


if __name__ == "__main__":
    asyncio.run(main())
