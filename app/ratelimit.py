"""
ratelimit.py — "token bucket" rate limiting so we never get banned by the APIs.

How a token bucket works (in plain words):
  * Each API has a bucket that refills at a fixed speed, e.g. 8 tokens/minute.
  * Every request takes one token. If the bucket is empty we wait for a refill.
  * If waiting would take too long, the request is skipped and the bot uses
    cached data or a fallback source instead.

Two layers are used for every request:
  1. a bucket per API (GeckoTerminal / Jupiter / Meteora) = the API's own limit
  2. a bucket per alert module (scanner / token / exit) = your "10–15 requests
     per minute per alert type" budget, so one module can't starve the others.

When an API answers "429 Too Many Requests" the whole API is paused for a while
(the "penalty box").
"""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None):
        self.rate = max(rate_per_sec, 1e-6)
        self.capacity = capacity if capacity is not None else max(1.0, self.rate * 10)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.blocked_until = 0.0  # penalty box after a 429
        self._lock = asyncio.Lock()

    @classmethod
    def per_minute(cls, rpm: float, burst: float | None = None) -> "TokenBucket":
        return cls(rpm / 60.0, burst if burst is not None else max(1.0, min(rpm / 4, 5)))

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def penalize(self, seconds: float) -> None:
        """Pause this bucket (used after HTTP 429)."""
        self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)
        self.tokens = 0

    def wait_time(self, weight: float = 1.0) -> float:
        self._refill()
        block = max(0.0, self.blocked_until - time.monotonic())
        need = 0.0 if self.tokens >= weight else (weight - self.tokens) / self.rate
        return max(block, need)

    async def acquire(self, timeout: float = 30.0, weight: float = 1.0) -> bool:
        """Take `weight` tokens (GMGN routes cost 1–3). False if the wait would exceed `timeout`."""
        async with self._lock:
            wait = self.wait_time(weight)
            if wait > timeout:
                return False
            if wait > 0:
                await asyncio.sleep(wait)
                self._refill()
            self.tokens -= weight
            return True


class RateLimiter:
    """Holds one bucket per API host and one per alert module."""

    # Only these APIs count against the per-module budget (Meteora allows 30 req/s,
    # so it is limited by its own host bucket only).
    BUDGETED_HOSTS = {"jupiter", "gecko"}

    def __init__(self, host_rpm: dict[str, float], module_rpm: float,
                 extra_buckets: dict[str, "TokenBucket"] | None = None):
        self.hosts = {h: TokenBucket.per_minute(r) for h, r in host_rpm.items()}
        self.hosts.update(extra_buckets or {})
        self.module_rpm = module_rpm
        self.modules: dict[str, TokenBucket] = {}

    def _module(self, name: str) -> TokenBucket:
        if name not in self.modules:
            self.modules[name] = TokenBucket.per_minute(self.module_rpm, burst=max(3, self.module_rpm / 3))
        return self.modules[name]

    async def acquire(self, host: str, module: str = "system", timeout: float = 30.0, weight: float = 1.0) -> bool:
        hb = self.hosts.get(host)
        if hb and hb.wait_time(weight) > timeout:
            return False  # API is in the penalty box or far too busy: fail fast
        if module != "system" and host in self.BUDGETED_HOSTS and not await self._module(module).acquire(timeout):
            return False
        return await hb.acquire(timeout, weight) if hb else True

    def penalize(self, host: str, seconds: float) -> None:
        if host in self.hosts:
            self.hosts[host].penalize(seconds)
