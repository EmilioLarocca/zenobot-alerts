"""
datasources.py — everything that talks to the outside world (market data).

Sources and what each one is used for:
  * Metlex (metlex.io)                  "Total Fees" (SOL) for any token — same as the previous bot
  * GMGN (openapi.gmgn.ai)              list of every token above your volume threshold (scanner
                                         discovery) + backup for "Total Fees". Needs GMGN_API_KEY.
  * Jupiter  (api.jup.ag)               live USD prices, market cap, 5m/1h/24h volume,
                                         price change, fees, logo, trending lists
  * GeckoTerminal (api.geckoterminal.com) OHLCV candles for RSI/Bollinger, 1-minute volume,
                                         trending/new pools, backup prices
  * Meteora DLMM (dlmm.datapi.meteora.ag) lifetime DLMM pool fees, backup candles

Every call goes through:
  1. a short cache (30–120 s) so the same data is never fetched twice in a row,
  2. the rate limiter (see ratelimit.py),
  3. error handling: failures are logged and a fallback source is tried, the bot
     never crashes because an API is down.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pandas as pd

from .config import Config
from .ratelimit import RateLimiter, TokenBucket

log = logging.getLogger("zenobot.data")

JUP_BASE = "https://api.jup.ag"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
METEORA_BASE = "https://dlmm.datapi.meteora.ag"
GMGN_BASE = "https://openapi.gmgn.ai"
METLEX_FEES_URL = "https://www.metlex.io/api/gmgn/token-info"
# Jupiter's own chart data (what jup.ag draws on a token page). Internal endpoint, not in
# Jupiter's public docs: it can change without notice, so GeckoTerminal stays as backup.
JUP_CHART_URL = "https://api.jup.ag/_datapi/v2/charts/{mint}"
JUP_CHART_TF = {"1m": "1_MINUTE", "5m": "5_MINUTE", "15m": "15_MINUTE", "1h": "1_HOUR", "4h": "4_HOUR"}
# Metlex also relays Jupiter's token lists: this is the exact URL your previous bot used
METLEX_JUP_BASE = "https://www.metlex.io/api/jupiter/api"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
# Tokens the global scanner always ignores (they'd trigger constantly)
BASE_TOKENS = {SOL_MINT, USDC_MINT, USDT_MINT}

# Timeframe helpers
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400}
GECKO_TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15), "1h": ("hour", 1), "4h": ("hour", 4)}
METEORA_TF = {"5m": "5m", "1h": "1h", "4h": "4h"}  # Meteora has no 1m / 15m candles
OHLCV_TTL = {"1m": 30, "5m": 45, "15m": 60, "1h": 90, "4h": 120}  # GeckoTerminal cache seconds
GMGN_ROUTE_WEIGHT = {"rank": 3, "token_info": 1}  # rate-limit cost of each GMGN route
GECKO_TRENDING_DURATION = {"5m": "5m", "1h": "1h", "24h": "24h"}


class SourceError(Exception):
    """Raised when a data source fails; callers catch it and fall back."""


def _f(x: Any) -> float | None:
    """Safely convert API values (often strings) to float."""
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class TTLCache:
    """Tiny in-memory cache: remembers a value for `ttl` seconds."""

    _MISS = object()

    def __init__(self) -> None:
        self._d: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any:
        hit = self._d.get(key)
        if not hit or hit[0] < time.time():
            return self._MISS
        return hit[1]

    def set(self, key: Any, value: Any, ttl: float) -> None:
        self._d[key] = (time.time() + ttl, value)
        if len(self._d) > 5000:  # simple housekeeping
            now = time.time()
            self._d = {k: v for k, v in self._d.items() if v[0] >= now}

    def is_miss(self, value: Any) -> bool:
        return value is self._MISS


@dataclass
class CandleSet:
    """OHLCV candles plus information about where they came from."""
    df: pd.DataFrame                 # columns: ts, open, high, low, close, volume (oldest -> newest)
    source: str                      # "jupiter" | "gecko" | "meteora"
    pool: str
    fetched_at: float
    relative: bool = False           # True = prices are not USD and must be rescaled to the live price
    notes: list[str] = field(default_factory=list)


def normalize_jupiter_token(t: dict) -> dict:
    """Turn a Jupiter Tokens V2 object into the bot's standard token dict."""
    vol: dict[str, float | None] = {}
    chg: dict[str, float | None] = {}
    for tf, key in (("5m", "stats5m"), ("1h", "stats1h"), ("6h", "stats6h"), ("24h", "stats24h")):
        s = t.get(key) or None
        if s:
            vol[tf] = (_f(s.get("buyVolume")) or 0.0) + (_f(s.get("sellVolume")) or 0.0)
            chg[tf] = _f(s.get("priceChange"))
        else:
            vol[tf], chg[tf] = None, None
    audit = t.get("audit") or {}
    return {
        "mint": t.get("id"),
        "symbol": t.get("symbol") or "?",
        "name": t.get("name") or "",
        "icon": t.get("icon"),
        "price_usd": _f(t.get("usdPrice")),
        "mcap": _f(t.get("mcap")) or _f(t.get("fdv")),
        "fdv": _f(t.get("fdv")),
        "liquidity": _f(t.get("liquidity")),
        "fees": _f(t.get("fees")),
        "volume": vol,
        "change": chg,
        "organic_score": _f(t.get("organicScore")),
        "is_sus": "isSus" in audit,
        "source": "jupiter",
    }


def normalize_gecko_token(attrs: dict) -> dict:
    """Turn a GeckoTerminal token object into the bot's standard token dict."""
    vol = attrs.get("volume_usd") or {}
    return {
        "mint": attrs.get("address"),
        "symbol": attrs.get("symbol") or "?",
        "name": attrs.get("name") or "",
        "icon": attrs.get("image_url") if attrs.get("image_url") not in (None, "missing.png") else None,
        "price_usd": _f(attrs.get("price_usd")),
        "mcap": _f(attrs.get("market_cap_usd")) or _f(attrs.get("fdv_usd")),
        "fdv": _f(attrs.get("fdv_usd")),
        "liquidity": _f(attrs.get("total_reserve_in_usd")),
        "fees": None,
        "volume": {"5m": None, "1h": None, "6h": None, "24h": _f(vol.get("h24"))},
        "change": {"5m": None, "1h": None, "6h": None, "24h": None},
        "organic_score": None,
        "is_sus": False,
        "source": "gecko",
    }


def unwrap_gmgn(d: Any) -> Any:
    """GMGN sometimes nests its answer: {"code":0,"data":{"code":0,"data":{...}}}. Peel every layer."""
    for _ in range(5):
        if isinstance(d, dict) and "data" in d and ("code" in d or len(d) <= 3):
            d = d["data"]
        else:
            break
    return d


def normalize_gmgn_rank(item: dict, interval: str) -> dict:
    """Turn a GMGN trending (rank) item into the bot's standard token dict."""
    vol = {"5m": None, "1h": None, "6h": None, "24h": None}
    chg = {"5m": _f(item.get("price_change_percent5m")), "1h": _f(item.get("price_change_percent1h")),
           "6h": None, "24h": None}
    vol[interval] = _f(item.get("volume"))
    if _f(item.get("price_change_percent")) is not None:
        chg[interval] = _f(item.get("price_change_percent"))
    # GMGN's "gas_fee" on the trending list is the token's lifetime total fees in SOL
    # (the "Total Fees" figure on gmgn.ai) — verified live on 2026-11-26.
    fees_sol = _f(item.get("gas_fee"))
    return {
        "mint": item.get("address"),
        "symbol": item.get("symbol") or "?",
        "name": item.get("name") or "",
        "icon": item.get("logo"),
        "price_usd": _f(item.get("price")),
        "mcap": _f(item.get("market_cap")),
        "fdv": None,
        "liquidity": _f(item.get("liquidity")),
        "fees": None,            # USD, filled in by the scanner (fees_sol x SOL price)
        "fees_sol": fees_sol,
        "volume": vol,
        "change": chg,
        "organic_score": None,
        "is_sus": False,
        "source": "gmgn",
    }


class DataHub:
    def __init__(self, cfg: Config, client: httpx.AsyncClient | None = None,
                 gmgn_client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        gmgn_rate = max(1, cfg.gmgn_rps)
        gmgn_capacity = max(3, gmgn_rate)  # must fit the heaviest route (trending = 3 units)
        self.limiter = RateLimiter(
            {"jupiter": cfg.jupiter_rpm, "gecko": cfg.gecko_rpm, "meteora": cfg.meteora_rps * 60,
             "metlex": 40, "jupchart": 20},
            cfg.module_rpm,
            # GMGN uses a weighted bucket: each route costs 1–3 units (free plan: 5 units/s)
            extra_buckets={"gmgn": TokenBucket(gmgn_rate, capacity=gmgn_capacity)},
        )
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": "zenobot_alerts/1.0", "Accept": "application/json"},
            follow_redirects=True,
        )
        # GMGN only works over IPv4: binding to 0.0.0.0 forces IPv4 connections
        self.gmgn_client = gmgn_client or (httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
            headers={"User-Agent": "zenobot_alerts/1.1", "Accept": "application/json"},
        ) if cfg.gmgn_api_key else None)
        self.cache = TTLCache()
        self.health: dict[str, dict[str, Any]] = {
            s: {"ok_at": None, "err_at": None, "error": None}
            for s in ("gmgn", "jupiter", "jupchart", "gecko", "meteora", "metlex")
        }

    @property
    def has_gmgn(self) -> bool:
        return bool(self.cfg.gmgn_api_key and self.gmgn_client)

    async def close(self) -> None:
        await self.client.aclose()
        if self.gmgn_client:
            await self.gmgn_client.aclose()

    # ------------------------------------------------------------ raw GET
    async def _get(self, host: str, url: str, params: dict | None = None, module: str = "system",
                   wait: float = 20.0) -> Any:
        if not await self.limiter.acquire(host, module, timeout=wait):
            raise SourceError(f"{host}: rate budget busy, request skipped")
        headers = {"x-api-key": self.cfg.jupiter_api_key} if host == "jupiter" and self.cfg.jupiter_api_key else None
        if host in ("metlex", "jupchart"):
            headers = {"User-Agent": "Mozilla/5.0 (compatible; zenobot_alerts/1.3)"}
        try:
            r = await self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            self._bad(host, f"network error: {e.__class__.__name__}")
            raise SourceError(f"{host}: network error {e!r}") from e
        if r.status_code == 429:
            retry = _f(r.headers.get("retry-after")) or 60.0
            self.limiter.penalize(host, min(max(retry, 10.0), 300.0))
            self._bad(host, f"429 rate limited (pausing {int(retry)}s)")
            raise SourceError(f"{host}: 429 rate limited")
        if r.status_code >= 400:
            self._bad(host, f"HTTP {r.status_code}")
            raise SourceError(f"{host}: HTTP {r.status_code} for {url}")
        try:
            data = r.json()
        except ValueError as e:
            self._bad(host, "invalid JSON")
            raise SourceError(f"{host}: invalid JSON") from e
        self.health[host]["ok_at"] = time.time()
        return data

    def _bad(self, host: str, msg: str) -> None:
        self.health[host]["err_at"] = time.time()
        self.health[host]["error"] = msg
        log.warning("[%s] %s", host, msg)

    def status(self) -> dict[str, Any]:
        """Health summary for the dashboard / Telegram /status."""
        out = {}
        for h, s in self.health.items():
            ok = s["ok_at"] is not None and (s["err_at"] is None or s["ok_at"] >= s["err_at"])
            out[h] = {**s, "healthy": ok if (s["ok_at"] or s["err_at"]) else None}
        return out

    # =================================================================== GMGN
    async def _gmgn(self, path: str, params: dict, route: str, module: str = "system", wait: float = 20.0) -> Any:
        """Call GMGN OpenAPI (read-only "exist" auth: X-APIKEY + timestamp + client_id)."""
        if not self.has_gmgn:
            raise SourceError("gmgn: no GMGN_API_KEY configured")
        weight = GMGN_ROUTE_WEIGHT.get(route, 1)
        if not await self.limiter.acquire("gmgn", module, timeout=wait, weight=weight):
            raise SourceError("gmgn: rate budget busy, request skipped")
        q = {**params, "timestamp": int(time.time()), "client_id": str(uuid.uuid4())}
        try:
            r = await self.gmgn_client.get(f"{GMGN_BASE}{path}", params=q,
                                           headers={"X-APIKEY": self.cfg.gmgn_api_key})
        except httpx.HTTPError as e:
            self._bad("gmgn", f"network error: {e.__class__.__name__}")
            raise SourceError(f"gmgn: network error {e!r}") from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 429 or body.get("code") == 429:
            reset = _f(body.get("reset_at")) or _f(r.headers.get("x-ratelimit-reset"))
            pause = (reset - time.time()) if reset else 30.0
            self.limiter.penalize("gmgn", min(max(pause, 5.0), 300.0))
            self._bad("gmgn", f"429 {body.get('error') or 'rate limited'}")
            raise SourceError("gmgn: 429 rate limited")
        if r.status_code >= 400 or body.get("code", 0) != 0:
            msg = f"{body.get('error') or ''} {body.get('message') or ''}".strip() or f"HTTP {r.status_code}"
            self._bad("gmgn", msg[:200])
            raise SourceError(f"gmgn: {msg}")
        self.health["gmgn"]["ok_at"] = time.time()
        return unwrap_gmgn(body.get("data"))

    async def gmgn_rank(self, interval: str, module: str, **filters: Any) -> list[dict]:
        """GMGN trending tokens on Solana for 1m/5m/1h/6h/24h, with server-side filters."""
        params = {"chain": "sol", "interval": interval, "limit": 100, "order_by": "volume", "direction": "desc"}
        params.update({k: v for k, v in filters.items() if v not in (None, 0, "")})
        key = ("grank", tuple(sorted(params.items())))
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit
        data = await self._gmgn("/v1/market/rank", params, "rank", module)
        items = (data or {}).get("rank") if isinstance(data, dict) else data
        toks = [normalize_gmgn_rank(i, interval) for i in (items or []) if isinstance(i, dict) and i.get("address")]
        self.cache.set(key, toks, 60)
        return toks

    async def token_total_fees(self, mint: str, module: str) -> tuple[float | None, str | None]:
        """
        Lifetime "Total Fees" of ANY token, in SOL — exactly like the previous bot:
        Metlex token-info `total_fee` first, GMGN (your key) as backup. -> (value, source) or (None, None).
        """
        key = ("tfees", mint)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit
        res: tuple[float | None, str | None] = (None, None)
        try:
            data = await self._get("metlex", METLEX_FEES_URL, {"address": mint}, module, wait=10)
            inner = unwrap_gmgn(data) if isinstance(data, dict) else None
            v = _f(inner.get("total_fee")) if isinstance(inner, dict) else None
            if v is not None:
                res = (v, "Metlex")
        except SourceError as e:
            log.info("Metlex fees failed for %s: %s", mint, e)
        if res[0] is None and self.has_gmgn:
            try:
                data = await self._gmgn("/v1/token/info", {"chain": "sol", "address": mint}, "token_info", module)
                v = _f((data or {}).get("total_fee")) if isinstance(data, dict) else None
                if v is not None:
                    res = (v, "GMGN")
            except SourceError as e:
                log.info("GMGN token info failed for %s: %s", mint, e)
        # remember a found value for 3 min; a miss for 1 min (so a short outage heals quickly)
        self.cache.set(key, res, 180 if res[0] is not None else 60)
        return res

    # ================================================================ JUPITER
    async def jup_prices(self, mints: list[str], module: str) -> dict[str, dict]:
        """Live USD price + 24h change for up to 50 mints per request."""
        out: dict[str, dict] = {}
        missing = []
        for m in dict.fromkeys(mints):
            hit = self.cache.get(("jprice", m))
            if self.cache.is_miss(hit):
                missing.append(m)
            elif hit:
                out[m] = hit
        for chunk in chunks(missing, 50):
            data = await self._get("jupiter", f"{JUP_BASE}/price/v3", {"ids": ",".join(chunk)}, module)
            for m in chunk:
                v = (data or {}).get(m)
                entry = None
                if isinstance(v, dict) and _f(v.get("usdPrice")) is not None:
                    entry = {"usd": _f(v["usdPrice"]), "change24h": _f(v.get("priceChange24h")),
                             "ts": time.time(), "source": "jupiter"}
                    out[m] = entry
                self.cache.set(("jprice", m), entry, 20)
        return out

    async def jup_tokens(self, mints: list[str], module: str) -> dict[str, dict]:
        """Full token info (mcap, volume, fees, logo…) for up to 100 mints per request."""
        out: dict[str, dict] = {}
        missing = []
        for m in dict.fromkeys(mints):
            hit = self.cache.get(("jtok", m))
            if self.cache.is_miss(hit):
                missing.append(m)
            elif hit:
                out[m] = hit
        for chunk in chunks(missing, 100):
            data = await self._get("jupiter", f"{JUP_BASE}/tokens/v2/search", {"query": ",".join(chunk)}, module)
            found = {t.get("id"): normalize_jupiter_token(t) for t in (data or []) if isinstance(t, dict)}
            for m in chunk:
                if m in found:
                    out[m] = found[m]
                self.cache.set(("jtok", m), found.get(m), 30)
        return out

    async def jup_category(self, category: str, interval: str, module: str, limit: int = 100,
                           prefer: str = "jupiter") -> list[dict]:
        """
        Jupiter lists: toptrending / toptraded / toporganicscore over 5m|1h|6h|24h.
        Two routes to the same Jupiter data: directly (api.jup.ag) or relayed by Metlex
        (what your previous bot used). `prefer` picks the first route; the other is the backup.
        Spreading the scanner's lists over both routes keeps each one far below its rate limit.
        """
        key = ("jcat", category, interval)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit
        routes = [("jupiter", JUP_BASE), ("metlex", METLEX_JUP_BASE)]
        if prefer == "metlex":
            routes.reverse()
        data, err = None, None
        for host, base in routes:
            try:
                data = await self._get(host, f"{base}/tokens/v2/{category}/{interval}", {"limit": limit},
                                       module, wait=8)
                if isinstance(data, dict):  # some relays wrap the list
                    data = data.get("data") or data.get("tokens") or []
                break
            except SourceError as e:
                err = e
                log.info("%s %s via %s failed: %s", category, interval, host, e)
        if data is None:
            raise err or SourceError(f"{category}: no route answered")
        toks = [normalize_jupiter_token(t) for t in (data or []) if isinstance(t, dict)]
        self.cache.set(key, toks, 8)    # the scanner runs every 10 s: always fresh data
        for t in toks:
            self.cache.set(("jtok", t["mint"]), t, 30)
        return toks

    async def jup_recent(self, module: str) -> list[dict]:
        """Recently listed tokens (new pools) from Jupiter."""
        hit = self.cache.get(("jrecent",))
        if not self.cache.is_miss(hit):
            return hit
        data = await self._get("jupiter", f"{JUP_BASE}/tokens/v2/recent", None, module)
        toks = [normalize_jupiter_token(t) for t in (data or []) if isinstance(t, dict)]
        self.cache.set(("jrecent",), toks, 60)
        return toks

    # ========================================================== GECKOTERMINAL
    async def gecko_token(self, mint: str, module: str) -> dict | None:
        key = ("gtok", mint)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit
        data = await self._get("gecko", f"{GECKO_BASE}/networks/solana/tokens/{mint}", None, module)
        d = (data or {}).get("data") or {}
        attrs = d.get("attributes") or {}
        pools = [p.get("id", "").replace("solana_", "", 1)
                 for p in ((d.get("relationships") or {}).get("top_pools") or {}).get("data") or []]
        res = {"info": normalize_gecko_token(attrs), "top_pools": [p for p in pools if p]} if attrs else None
        self.cache.set(key, res, 180)
        return res

    async def gecko_prices(self, mints: list[str], module: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for chunk in chunks(list(dict.fromkeys(mints)), 30):
            data = await self._get("gecko", f"{GECKO_BASE}/simple/networks/solana/token_price/{','.join(chunk)}",
                                   None, module)
            prices = (((data or {}).get("data") or {}).get("attributes") or {}).get("token_prices") or {}
            for m, p in prices.items():
                if _f(p):
                    # Gecko may lowercase addresses: map back to the original mint
                    orig = next((x for x in chunk if x.lower() == m.lower()), m)
                    out[orig] = _f(p)
        return out

    async def gecko_ohlcv(self, pool: str, tf: str, token: str, limit: int, module: str) -> list[list]:
        key = ("gohlcv", pool, tf, token, limit)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit
        timeframe, agg = GECKO_TF[tf]
        params = {"aggregate": agg, "limit": min(limit, 1000), "currency": "usd", "token": token}
        data = await self._get("gecko", f"{GECKO_BASE}/networks/solana/pools/{pool}/ohlcv/{timeframe}",
                               params, module)
        rows = ((((data or {}).get("data") or {}).get("attributes")) or {}).get("ohlcv_list") or []
        self.cache.set(key, rows, OHLCV_TTL[tf])
        return rows

    async def gecko_pool_list(self, kind: str, module: str, duration: str | None = None) -> list[str]:
        """kind = 'trending_pools' | 'new_pools'. Returns base-token mints."""
        key = ("glist", kind, duration)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit
        params: dict[str, Any] = {"page": 1}
        if duration and kind == "trending_pools":
            params["duration"] = duration  # trending over the same window as your scanner
        try:
            data = await self._get("gecko", f"{GECKO_BASE}/networks/solana/{kind}", params, module, wait=5)
        except SourceError:
            if "duration" not in params:
                raise
            params.pop("duration")
            data = await self._get("gecko", f"{GECKO_BASE}/networks/solana/{kind}", params, module, wait=5)
        mints = []
        for p in (data or {}).get("data") or []:
            bid = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
            if bid.startswith("solana_"):
                mints.append(bid[len("solana_"):])
        self.cache.set(key, mints, 300)  # refresh every 5 min to save Gecko budget
        return mints

    # ================================================================ METEORA
    async def meteora_pools(self, mint: str, module: str) -> list[dict]:
        """All Meteora DLMM pools containing this token (cached 10 min)."""
        key = ("mpools", mint)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit

        def mine(p: dict) -> bool:
            return mint in ((p.get("token_x") or {}).get("address"), (p.get("token_y") or {}).get("address"))

        data = await self._get("meteora", f"{METEORA_BASE}/pools", {"query": mint, "page_size": 50}, module)
        pools = [p for p in (data or {}).get("data") or [] if mine(p)]
        if not pools:  # some API versions need an explicit filter
            for side in ("token_x", "token_y"):
                try:
                    d2 = await self._get("meteora", f"{METEORA_BASE}/pools",
                                         {"filter_by": f"{side}={mint}", "page_size": 50}, module)
                    pools += [p for p in (d2 or {}).get("data") or [] if mine(p)]
                except SourceError:
                    pass
        self.cache.set(key, pools, 600)
        return pools

    async def dlmm_lifetime_fees(self, mint: str, module: str) -> float | None:
        """Sum of lifetime fees (USD) earned by all Meteora DLMM pools of this token."""
        try:
            pools = await self.meteora_pools(mint, module)
        except SourceError:
            return None
        if not pools:
            return None
        return sum((_f((p.get("cumulative_metrics") or {}).get("fees")) or 0.0) for p in pools)

    async def meteora_ohlcv(self, pool: str, tf: str, module: str, periods: int) -> list[dict]:
        key = ("mohlcv", pool, tf)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit):
            return hit
        start = int(time.time()) - TF_SECONDS[tf] * (periods + 2)
        data = await self._get("meteora", f"{METEORA_BASE}/pools/{pool}/ohlcv",
                               {"timeframe": METEORA_TF[tf], "start_time": start}, module)
        rows = (data or {}).get("data") or []
        self.cache.set(key, rows, OHLCV_TTL[tf])
        return rows

    # ============================================================ COMPOSITES
    async def prices_usd(self, mints: list[str], module: str) -> dict[str, float]:
        """USD prices: Jupiter first, GeckoTerminal as fallback for anything missing."""
        out: dict[str, float] = {}
        if not mints:
            return out
        try:
            for m, v in (await self.jup_prices(mints, module)).items():
                out[m] = v["usd"]
        except SourceError as e:
            log.info("Jupiter prices failed (%s) -> falling back to GeckoTerminal", e)
        missing = [m for m in mints if m not in out]
        if missing:
            try:
                out.update(await self.gecko_prices(missing, module))
            except SourceError as e:
                log.warning("Fallback price source failed too: %s", e)
        return out

    async def sol_usd(self, module: str) -> float | None:
        return (await self.prices_usd([SOL_MINT], module)).get(SOL_MINT)

    async def token_infos(self, mints: list[str], module: str, gecko_fallback_max: int = 3) -> dict[str, dict]:
        """Standard token info for many mints: Jupiter first, Gecko for a few missing ones."""
        out: dict[str, dict] = {}
        if not mints:
            return out
        try:
            out.update(await self.jup_tokens(mints, module))
        except SourceError as e:
            log.info("Jupiter token info failed (%s), trying GeckoTerminal", e)
        for m in [m for m in mints if m not in out][:gecko_fallback_max]:
            try:
                g = await self.gecko_token(m, module)
                if g:
                    out[m] = g["info"]
            except SourceError:
                break
        return out

    async def token_info(self, mint: str, module: str) -> dict | None:
        return (await self.token_infos([mint], module)).get(mint)

    async def best_pool(self, mint: str, module: str) -> dict | None:
        """Most liquid pool for a token, used for candles. Cached 1 hour."""
        key = ("bestpool", mint)
        hit = self.cache.get(key)
        if not self.cache.is_miss(hit) and hit:
            return hit
        res: dict[str, Any] = {"gecko": None, "meteora": None, "meteora_token_is_x": True}
        try:
            g = await self.gecko_token(mint, module)
            if g and g["top_pools"]:
                res["gecko"] = g["top_pools"][0]
        except SourceError:
            pass
        try:
            pools = await self.meteora_pools(mint, module)
            if pools:
                best = max(pools, key=lambda p: _f(p.get("tvl")) or 0.0)
                res["meteora"] = best.get("address")
                res["meteora_token_is_x"] = (best.get("token_x") or {}).get("address") == mint
        except SourceError:
            pass
        if not res["gecko"] and not res["meteora"]:
            return None
        self.cache.set(key, res, 3600 if res["gecko"] else 300)
        return res

    async def jup_chart(self, mint: str, tf: str, limit: int, module: str, quote: str = "usd") -> CandleSet:
        """
        Candles from Jupiter's chart data: all pools of the token combined, the same
        candles as the chart on jup.ag. Prices in USD by default (quote="native" = SOL).
        """
        if tf not in JUP_CHART_TF:
            raise SourceError(f"jupchart: timeframe {tf} not supported")
        params = {"interval": JUP_CHART_TF[tf], "to": int(time.time() * 1000), "candles": int(limit),
                  "type": "price", "quote": quote}
        data = await self._get("jupchart", JUP_CHART_URL.format(mint=mint), params, module, wait=10)
        rows = data.get("candles") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            self._bad("jupchart", "unexpected answer format")
            raise SourceError("jupchart: unexpected answer format")
        recs = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            v = [_f(r.get(k)) for k in ("time", "open", "high", "low", "close", "volume")]
            if None in v[:5] or v[4] <= 0:
                continue
            recs.append(v[:5] + [v[5] or 0.0])
        if not recs:
            raise SourceError("jupchart: no candles for this token")
        df = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close", "volume"])
        if df["ts"].max() > 1e12:  # milliseconds -> seconds (today the API answers in seconds)
            df["ts"] = df["ts"] / 1000
        df["ts"] = df["ts"].astype(int)
        df = df.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)
        return CandleSet(df=df, source="jupiter", pool="", fetched_at=time.time())

    async def candles(self, mint: str, tf: str, limit: int, module: str,
                      jupiter_chart: bool = False) -> CandleSet:
        """
        OHLCV candle history in USD for RSI/Bollinger.
        jupiter_chart=True (exit alerts): Jupiter's chart data first.
        Then GeckoTerminal, then Meteora DLMM (only 5m/1h/4h). Raises SourceError when nothing works.
        """
        errors: list[str] = []
        if jupiter_chart:
            try:
                return await self.jup_chart(mint, tf, limit, module)
            except SourceError as e:
                errors.append(str(e))
                log.info("Jupiter chart failed for %s %s, using GeckoTerminal: %s", mint, tf, e)
        pools = await self.best_pool(mint, module)
        if not pools:
            raise SourceError("; ".join(errors + ["no liquidity pool found for this token"]))
        if pools["gecko"]:
            try:
                rows = await self.gecko_ohlcv(pools["gecko"], tf, mint, limit, module)
                cols = ["ts", "open", "high", "low", "close", "volume"]
                df = pd.DataFrame([r[:6] for r in rows if len(r) >= 6], columns=cols)
                if len(df):
                    df = df.astype(float).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
                    df["ts"] = df["ts"].astype(int)
                    return CandleSet(df=df, source="gecko", pool=pools["gecko"], fetched_at=time.time())
                errors.append("GeckoTerminal returned no candles")
            except SourceError as e:
                errors.append(str(e))
        if pools["meteora"] and tf in METEORA_TF:
            try:
                rows = await self.meteora_ohlcv(pools["meteora"], tf, module, limit)
                df = pd.DataFrame([{k: _f(r.get(k)) for k in ("timestamp", "open", "high", "low", "close", "volume")}
                                   for r in rows]).dropna()
                if len(df):
                    df = df.rename(columns={"timestamp": "ts"})
                    if df["ts"].max() > 1e12:  # milliseconds -> seconds
                        df["ts"] = df["ts"] / 1000
                    df["ts"] = df["ts"].astype(int)
                    if not pools["meteora_token_is_x"]:  # price is quoted the other way round
                        o, h, l, c = 1 / df["open"], 1 / df["low"], 1 / df["high"], 1 / df["close"]
                        df["open"], df["high"], df["low"], df["close"] = o, h, l, c
                    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
                    return CandleSet(df=df, source="meteora", pool=pools["meteora"], fetched_at=time.time(),
                                     relative=True, notes=["Meteora fallback candles (rescaled to USD)"])
            except (SourceError, ZeroDivisionError) as e:
                errors.append(str(e))
        raise SourceError("; ".join(errors) or "no candle source available for this timeframe")

    async def volume_1m(self, mint: str, module: str) -> tuple[float | None, float | None]:
        """
        Approx. USD volume of the last minute + 1m price change (%), from 1m candles.
        Uses the larger of the running minute and the last completed minute so a
        brand-new minute doesn't hide a spike that just happened.
        """
        cs = await self.candles(mint, "1m", 3, module)
        df = cs.df
        if df.empty:
            return None, None
        now_bucket = int(time.time()) // 60 * 60
        cur = df[df["ts"] == now_bucket]
        prev = df[df["ts"] == now_bucket - 60]
        vols = [float(x["volume"].iloc[0]) for x in (cur, prev) if len(x)]
        vol = max(vols) if vols else 0.0
        chg = None
        if len(df) >= 2 and df["close"].iloc[-2]:
            chg = (df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100
        return vol, chg
