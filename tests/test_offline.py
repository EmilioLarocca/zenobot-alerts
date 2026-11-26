"""
Offline tests: every external API is replaced by fake data, so these run
without internet. Run with:  pip install pytest && pytest -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import indicators, services as svc  # noqa: E402
from app.config import Config  # noqa: E402
from app.context import Ctx  # noqa: E402
from app.datasources import SOL_MINT, DataHub  # noqa: E402
from app.db import Database  # noqa: E402
from app.modules import exit_alerts, scanner, token_alerts  # noqa: E402

MINT_A = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"   # fake-but-valid base58 addresses
MINT_B = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
MINT_C = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
POOL_A = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"


_OPEN_DBS: list = []


def run(coro):
    """Run a test coroutine, then close any databases it opened (aiosqlite threads
    would otherwise keep the test process alive)."""
    async def wrapper():
        try:
            return await coro
        finally:
            while _OPEN_DBS:
                await _OPEN_DBS.pop().close()
    return asyncio.run(wrapper())


def cfg(tmp: Path, gmgn: bool = False) -> Config:
    return Config(telegram_token="1:x", chat_id=42, dashboard_password="pw", jupiter_api_key=None,
                  gmgn_api_key="gk" if gmgn else None,
                  data_dir=tmp, volume_persistent=True, port=0, public_url="https://example.test",
                  gecko_rpm=600, jupiter_rpm=600, module_rpm=600, meteora_rps=50, gmgn_rps=50)


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str | None]] = []

    async def send(self, text, photo=None):
        self.sent.append((text, photo))
        return True


# ------------------------------------------------------------ fake market
class Market:
    """Fake Jupiter/Gecko/Meteora answering from in-memory dicts."""

    def __init__(self):
        self.price = {SOL_MINT: 150.0, MINT_A: 0.002, MINT_B: 1.5, MINT_C: 0.01}
        self.vol5m = {MINT_A: 80_000.0, MINT_B: 10_000.0, MINT_C: 200_000.0}
        self.mcap = {MINT_A: 2_000_000.0, MINT_B: 30_000_000.0, MINT_C: 900_000_000.0}
        self.candles: list[list[float]] = []
        self.jup_fail = False
        self.calls: list[str] = []
        self.gmgn_params: list[dict] = []

    def tok(self, m):
        return {"id": m, "name": f"Token {m[:3]}", "symbol": m[:3].upper(), "icon": f"https://img/{m}.png",
                "usdPrice": self.price[m], "mcap": self.mcap.get(m), "fees": 1234.5,
                "stats5m": {"buyVolume": self.vol5m.get(m, 0) / 2, "sellVolume": self.vol5m.get(m, 0) / 2,
                            "priceChange": 3.5},
                "stats1h": {"buyVolume": 1e6, "sellVolume": 1e6, "priceChange": 10.0},
                "stats24h": {"buyVolume": 5e6, "sellVolume": 5e6, "priceChange": -2.0}}

    def handler(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        self.calls.append(f"{u.host}{u.path}")
        if u.host == "openapi.gmgn.ai":
            assert req.headers.get("X-APIKEY") == "gk" and "timestamp" in u.params and "client_id" in u.params
            self.gmgn_params.append(dict(u.params))
            if u.path == "/v1/market/rank":
                rank = [{"address": m, "symbol": m[:3].upper(), "name": f"Token {m[:3]}", "logo": "https://img/x.png",
                         "price": self.price[m], "market_cap": self.mcap[m], "volume": self.vol5m[m],
                         "price_change_percent": 4.2, "gas_fee": fee}
                        for m, fee in ((MINT_A, 62.5), (MINT_B, 2.2), (MINT_C, 115.3))]
                # real GMGN answers are double-wrapped (seen live on 2026-11-26)
                return httpx.Response(200, json={"code": 0, "data": {"code": 0, "data": {"rank": rank}}})
            if u.path == "/v1/market/token_kline":
                lst = [{"time": r[0] * 1000, "open": str(r[1]), "high": str(r[2]), "low": str(r[3]),
                        "close": str(r[4]), "volume": "1", "amount": "50"} for r in reversed(self.candles)]
                return httpx.Response(200, json={"code": 0, "data": {"list": lst}})
            return httpx.Response(200, json={"code": 404, "error": "NOT_FOUND", "message": "nope"})
        if u.host == "api.jup.ag":
            if self.jup_fail:
                return httpx.Response(500)
            if u.path == "/price/v3":
                ids = req.url.params["ids"].split(",")
                return httpx.Response(200, json={m: {"usdPrice": self.price[m], "priceChange24h": 1.0}
                                                 for m in ids if m in self.price})
            if u.path == "/tokens/v2/search":
                ids = req.url.params["query"].split(",")
                return httpx.Response(200, json=[self.tok(m) for m in ids if m in self.price])
            if u.path.startswith("/tokens/v2/top"):
                return httpx.Response(200, json=[self.tok(m) for m in (MINT_A, MINT_B, MINT_C, SOL_MINT)])
            if u.path == "/tokens/v2/recent":
                return httpx.Response(200, json=[])
        if u.host == "api.geckoterminal.com":
            p = u.path
            if p.endswith("trending_pools") or p.endswith("new_pools"):
                return httpx.Response(200, json={"data": []})
            if "/simple/" in p:
                addrs = p.rsplit("/", 1)[-1].split(",")
                return httpx.Response(200, json={"data": {"attributes": {"token_prices": {
                    a: str(self.price[a]) for a in addrs if a in self.price}}}})
            if "/ohlcv/" in p:
                return httpx.Response(200, json={"data": {"attributes": {"ohlcv_list": self.candles}}})
            if "/tokens/" in p:
                return httpx.Response(200, json={"data": {"attributes": {"address": MINT_A, "name": "A", "symbol": "A"},
                                                          "relationships": {"top_pools": {"data": [
                                                              {"id": f"solana_{POOL_A}", "type": "pool"}]}}}})
        if u.host == "dlmm.datapi.meteora.ag":
            q = req.url.params.get("query")
            return httpx.Response(200, json={"data": [{"address": POOL_A, "tvl": "1000",
                                                       "token_x": {"address": q}, "token_y": {"address": SOL_MINT},
                                                       "cumulative_metrics": {"fees": "5555.5", "volume": "1"}}]})
        return httpx.Response(404)


async def make_ctx(tmp: Path, market: Market, gmgn: bool = False) -> Ctx:
    """Must be awaited inside the same event loop as the test (aiosqlite is loop-bound)."""
    db = await Database.open(tmp / "t.db")
    _OPEN_DBS.append(db)
    mock = httpx.MockTransport(market.handler)
    c = cfg(tmp, gmgn)
    data = DataHub(c, client=httpx.AsyncClient(transport=mock),
                   gmgn_client=httpx.AsyncClient(transport=mock) if gmgn else None)
    return Ctx(cfg=c, db=db, data=data, notifier=FakeNotifier())


def rising_candles(n=200, tf=300, start=1.0, step=0.004):
    now_bucket = int(time.time()) // tf * tf
    rows = []
    p = start
    for i in range(n):
        ts = now_bucket - (n - 1 - i) * tf
        o = p
        p = p * (1 + step + (0.002 if i % 5 else -0.006))
        rows.append([ts, o, max(o, p), min(o, p), p, 1000.0])
    return list(reversed(rows))  # Gecko returns newest first


# ================================================================ tests
def test_parsers():
    assert svc.parse_number("50k") == 50_000
    assert svc.parse_number("$1.5M") == 1_500_000
    assert svc.parse_number("2,000") == 2000
    assert svc.parse_duration_hours("3d") == 72
    assert svc.parse_duration_hours("90m") == 1.5
    assert svc.parse_duration_hours("1d12h") == 36
    assert svc.parse_duration_hours("24") == 24
    with pytest.raises(svc.ValidationError):
        svc.parse_duration_hours("soon")
    ok, bad = svc.parse_mints(f"{MINT_A}\n{MINT_B}, nope ,{MINT_A}")
    assert ok == [MINT_A, MINT_B] and bad == ["nope"]


def test_indicator_engines_agree():
    rng = np.random.default_rng(1)
    close = pd.Series(np.cumprod(1 + rng.normal(0, 0.01, 300)))
    r_ta = indicators.rsi_series(close, 14).iloc[-1]
    # force the built-in fallback
    old = indicators.HAVE_PANDAS_TA
    indicators.HAVE_PANDAS_TA = False
    try:
        r_fb = indicators.rsi_series(close, 14).iloc[-1]
        bb_fb = indicators.bbands(close, 20, 2)
    finally:
        indicators.HAVE_PANDAS_TA = old
    bb_ta = indicators.bbands(close, 20, 2)
    assert abs(r_ta - r_fb) < 0.05, (r_ta, r_fb)
    assert all(abs(a - b) < 1e-9 for a, b in zip(bb_ta, bb_fb))


def test_merge_live_price():
    df = pd.DataFrame([[0, 1, 1, 1, 1, 0], [300, 1, 2, 1, 2, 0]], columns=["ts", "open", "high", "low", "close", "volume"])
    same = indicators.merge_live_price(df, 3.0, 300, now=400)
    assert len(same) == 2 and same["close"].iloc[-1] == 3.0 and same["high"].iloc[-1] == 3.0
    newc = indicators.merge_live_price(df, 1.5, 300, now=650)
    assert len(newc) == 3 and newc["ts"].iloc[-1] == 600 and newc["open"].iloc[-1] == 2.0


def test_services_and_validation(tmp_path):
    async def go():
        db = await Database.open(tmp_path / "s.db")
        s = await svc.update_scanner(db, {"min_volume": "75k", "timeframe": "1h", "enabled": "on"})
        assert s["min_volume"] == 75_000 and s["timeframe"] == "1h" and s["enabled"] is True
        with pytest.raises(svc.ValidationError):
            await svc.update_scanner(db, {"min_mcap": "10m", "max_mcap": "1m"})
        with pytest.raises(svc.ValidationError):
            await svc.create_exit_alert(db, None, {"ca": MINT_A, "rsi_enabled": False, "bb_enabled": False})
        aid = await svc.create_exit_alert(db, None, {"ca": MINT_A, "rsi_enabled": False, "bb_enabled": True,
                                                    "bb_above_middle": "1.5"})
        a = await db.get("exit_alerts", aid)
        assert a["bb_mode"] == "middle" and a["bb_min_pct"] == 1.5 and a["expires_at"] > time.time()
        with pytest.raises(svc.ValidationError):  # can't turn the last indicator off while active
            await svc.update_exit_alert(db, aid, {"bb_enabled": 0})
        with pytest.raises(svc.ValidationError):
            await svc.update_exit_alert(db, aid, {"bb_above_middle": "1", "bb_above_upper": "2"})
        # draft created in Telegram can have both OFF, but can't be activated
        did = await svc.create_exit_alert(db, None, {"ca": MINT_B, "rsi_enabled": 0}, status="draft")
        with pytest.raises(svc.ValidationError):
            await svc.set_exit_alert_status(db, did, "active")
        r0 = await db.rev()
        tid = await svc.create_token_alert(db, None, {"ca": MINT_B, "metric": "price", "currency": "sol",
                                                     "threshold": "0.01"})
        assert await db.rev() > r0
        t = await db.get("token_alerts", tid)
        assert t["currency"] == "sol" and t["timeframe"] is None
        await db.close()
    run(go())


def test_scanner_cycle_jupiter_fallback(tmp_path):
    m = Market()

    async def go():
        ctx = await make_ctx(tmp_path, m)
        await svc.update_scanner(ctx.db, {"enabled": True, "timeframe": "5m", "min_volume": "50k",
                                          "min_mcap": "1m", "max_mcap": "100m", "cooldown_minutes": 30})
        with pytest.raises(svc.ValidationError):
            await svc.update_scanner(ctx.db, {"timeframe": "1m"})  # 1m removed
        await scanner.run_cycle(ctx)
        sent = [t for t, _ in ctx.notifier.sent]
        assert len(sent) == 1 and "$7GC" in sent[0] and "$80.00K" in sent[0]
        await scanner.run_cycle(ctx)  # cooldown: no repeat
        assert len(ctx.notifier.sent) == 1
        await ctx.db.conn.execute("DELETE FROM scanner_seen")
        await ctx.db.conn.commit()
        await svc.set_blacklist(ctx.db, MINT_A)
        await scanner.run_cycle(ctx)
        assert len(ctx.notifier.sent) == 1
    run(go())


def test_scanner_gmgn_with_total_fees(tmp_path):
    m = Market()
    m.vol5m[MINT_B] = 70_000.0

    async def go():
        ctx = await make_ctx(tmp_path, m, gmgn=True)
        await svc.update_scanner(ctx.db, {"enabled": True, "timeframe": "5m", "min_volume": "50k",
                                          "min_mcap": "1m", "max_mcap": "100m", "min_fees_sol": "20"})
        await scanner.run_cycle(ctx)
        sent = [t for t, _ in ctx.notifier.sent]
        # A passes (62.5 SOL fees); B fails fees (2.2 SOL); C fails max market cap
        assert len(sent) == 1 and "$7GC" in sent[0], sent
        assert "Total fees: <b>62.5 SOL</b> (~$9.38K)" in sent[0]   # 62.5 x $150 SOL
        p = m.gmgn_params[0]
        assert p["interval"] == "5m" and p["min_gas_fee"] == "20.0" and p["max_marketcap"] == "100000000.0"
    run(go())


def test_toggle_notifications(tmp_path):
    m = Market()
    msgs: list[str] = []

    async def fake_send(text, photo=None):
        msgs.append(text)
        return True

    async def go():
        ctx = await make_ctx(tmp_path, m)
        svc.set_notifier(fake_send)
        try:
            await svc.update_scanner(ctx.db, {"enabled": True})
            await svc.update_scanner(ctx.db, {"min_volume": "60k"})          # not a toggle -> silent
            await svc.update_modules(ctx.db, {"exit_alerts_enabled": False})
            tid = await svc.create_token_alert(ctx.db, ctx.data, {"ca": MINT_B, "metric": "mcap", "threshold": "1m"})
            await svc.set_token_alert_status(ctx.db, tid, "paused")
            eid = await svc.create_exit_alert(ctx.db, ctx.data, {"ca": MINT_A})
            await svc.delete_alert(ctx.db, "exit_alerts", eid)
            await asyncio.sleep(0.05)
        finally:
            svc.set_notifier(None)
        joined = "\n".join(msgs)
        assert len(msgs) == 6, msgs
        assert "Volume Scanner</b> turned ON" in joined and "Exit Alerts</b> module turned OFF" in joined
        assert "Token alert #1" in joined and "OFF (paused)" in joined and "cancelled" in joined
    run(go())


def test_token_alert_fires_once_and_rearms(tmp_path):
    m = Market()
    async def go():
        ctx = await make_ctx(tmp_path, m)
        await svc.create_token_alert(ctx.db, ctx.data, {"ca": MINT_B, "metric": "price", "currency": "usd",
                                                        "threshold": "2"})
        await svc.create_token_alert(ctx.db, ctx.data, {"ca": MINT_A, "metric": "mcap", "direction": "below",
                                                        "threshold": "1m"})
        await token_alerts.run_cycle(ctx)
        assert ctx.notifier.sent == []
        m.price[MINT_B] = 2.5
        ctx.data.cache = type(ctx.data.cache)()  # drop cache
        await token_alerts.run_cycle(ctx)
        await token_alerts.run_cycle(ctx)
        assert len(ctx.notifier.sent) == 1 and "Special Alert Hit!" in ctx.notifier.sent[0][0]
        m.price[MINT_B] = 1.0
        ctx.data.cache = type(ctx.data.cache)()
        await token_alerts.run_cycle(ctx)  # re-arms
        m.price[MINT_B] = 3.0
        ctx.data.cache = type(ctx.data.cache)()
        await token_alerts.run_cycle(ctx)
        assert len(ctx.notifier.sent) == 2
    run(go())


def test_price_fallback_to_gecko(tmp_path):
    m = Market()
    m.jup_fail = True

    async def go():
        ctx = await make_ctx(tmp_path, m)
        return await ctx.data.prices_usd([MINT_A, SOL_MINT], "token")
    prices = run(go())
    assert prices[MINT_A] == 0.002 and prices[SOL_MINT] == 150.0
    assert any("geckoterminal" in c for c in m.calls)


def test_exit_alert_fires_instantly_then_reminds(tmp_path):
    m = Market()
    m.candles = rising_candles()
    m.price[MINT_A] = m.candles[0][4] * 1.01

    async def go():
        ctx = await make_ctx(tmp_path, m, gmgn=True)
        aid = await svc.create_exit_alert(ctx.db, ctx.data, {"ca": MINT_A, "timeframe": "5m", "rsi_min": 60,
                                                             "bb_enabled": True, "bb_above_middle": "0"})
        await exit_alerts.run_cycle(ctx)                       # first check -> alert right away
        a = await ctx.db.get("exit_alerts", aid)
        assert a["last_error"] is None, a["last_error"]
        assert len(ctx.notifier.sent) == 1
        text, photo = ctx.notifier.sent[0]
        assert "Exit Position Alert" in text and "RSI(14)" in text and "Reminder every 5 min" in text
        assert photo and photo.endswith(".png")
        assert any("openapi.gmgn.ai/v1/market/token_kline" in c for c in m.calls)  # GMGN candles used
        await exit_alerts.run_cycle(ctx)                       # within 5 min -> silent
        assert len(ctx.notifier.sent) == 1
        await ctx.db.update("exit_alerts", aid, {"last_triggered": time.time() - 301})
        await exit_alerts.run_cycle(ctx)                       # 5 min later, still true -> reminder
        assert len(ctx.notifier.sent) == 2 and "reminder" in ctx.notifier.sent[1][0]
        await ctx.db.update("exit_alerts", aid, {"expires_at": time.time() - 1})
        await exit_alerts.run_cycle(ctx)
        assert (await ctx.db.get("exit_alerts", aid))["status"] == "expired"
        assert "finished" in ctx.notifier.sent[-1][0]
    run(go())


def test_exit_alert_no_extra_safeguards(tmp_path):
    """Live price far from the candles and only a few candles: still evaluated and alerted."""
    m = Market()
    m.candles = rising_candles(n=20)
    m.price[MINT_A] = m.candles[0][4] * 3

    async def go():
        ctx = await make_ctx(tmp_path, m)                    # no GMGN -> GeckoTerminal candles
        await svc.create_exit_alert(ctx.db, ctx.data, {"ca": MINT_A, "rsi_min": 1})
        await exit_alerts.run_cycle(ctx)
        assert len(ctx.notifier.sent) == 1
    run(go())


def test_web_api(tmp_path):
    from app.web.server import create_app
    m = Market()

    async def go():
        ctx = await make_ctx(tmp_path, m)
        app = create_app(ctx.db, ctx.data, ctx.cfg, "pw")
        auth = ("admin", "pw")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/healthz")).status_code == 200
            assert (await c.get("/api/state")).status_code == 401
            assert (await c.get("/api/state", auth=("admin", "wrong"))).status_code == 401
            assert (await c.get("/", auth=auth)).status_code == 200
            r = await c.put("/api/scanner", json={"min_volume": "1m", "enabled": True}, auth=auth)
            assert r.status_code == 200 and r.json()["min_volume"] == 1e6
            r = await c.post("/api/exit-alerts", json={"ca": MINT_A, "rsi_enabled": False, "bb_enabled": False}, auth=auth)
            assert r.status_code == 400 and "at least one" in r.json()["error"]
            r = await c.post("/api/exit-alerts", json={"ca": MINT_A, "rsi_enabled": True, "bb_enabled": True,
                                                       "bb_above_upper": "2", "duration_hours": "3d"}, auth=auth)
            assert r.status_code == 200 and r.json()["duration_hours"] == 72, r.text
            r = await c.post("/api/token-alerts", json={"ca": MINT_A, "metric": "volume", "timeframe": "1h",
                                                        "threshold": "500k"}, auth=auth)
            assert r.status_code == 200, r.text
            tid = r.json()["id"]
            assert r.json()["symbol"] == "7GC"
            r = await c.post(f"/api/token-alerts/{tid}/status", json={"status": "paused"}, auth=auth)
            assert r.json()["status"] == "paused"
            r = await c.put(f"/api/token-alerts/{tid}", json={"threshold": "750k"}, auth=auth)
            assert r.json()["threshold"] == 750_000
            r = await c.put("/api/blacklist", json={"text": f"{MINT_A}, junk"}, auth=auth)
            assert r.json() == {"blacklist": [MINT_A], "invalid": ["junk"]}
            st = (await c.get("/api/state", auth=auth)).json()
            assert st["scanner"]["enabled"] is True and len(st["token_alerts"]) == 1 and len(st["exit_alerts"]) == 1
            assert (await c.delete(f"/api/token-alerts/{tid}", auth=auth)).status_code == 200
    run(go())


def test_telegram_screens_and_flows(tmp_path):
    from telegram import InlineKeyboardMarkup
    from app import telegram_ui as ui
    m = Market()

    def check(screen):
        text, kb = screen
        assert isinstance(kb, InlineKeyboardMarkup) and text
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    assert len(btn.callback_data.encode()) <= 64

    def fake_update(text=None, data=None):
        chat = SimpleNamespace(id=42, send_message=AsyncMock())
        msg = SimpleNamespace(text=text, reply_text=AsyncMock())
        q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()) if data else None
        return SimpleNamespace(effective_chat=chat, effective_user=SimpleNamespace(id=42), message=msg,
                               callback_query=q)

    async def go():
        ctx = await make_ctx(tmp_path, m)
        app = ui.build_application(ctx.cfg, ctx.db, ctx.data, "https://x.test", "pw")
        context = SimpleNamespace(application=app, user_data={})
        db = ctx.db
        for path in (["menu"], ["A"], ["A", "cfg"], ["A", "bl"], ["B"], ["C"]):
            check(await ui.route(path, db, ctx.data, context))
        await ui.route(["A", "tog"], db, ctx.data, context)
        assert (await db.scanner_settings())["enabled"] is True
        await ui.route(["A", "tf", "1h"], db, ctx.data, context)
        assert (await db.scanner_settings())["timeframe"] == "1h"
        with pytest.raises(svc.ValidationError):
            await ui.route(["A", "tf", "1m"], db, ctx.data, context)

        # typed value for scanner
        context.user_data["pending"] = {"kind": "scanner", "field": "min_volume"}
        assert await ui.receive_input(fake_update("120k"), context) == ui.ConversationHandler.END
        assert (await db.scanner_settings())["min_volume"] == 120_000
        # bad value -> stays in INPUT
        context.user_data["pending"] = {"kind": "scanner", "field": "cooldown_minutes"}
        assert await ui.receive_input(fake_update("abc"), context) == ui.INPUT

        # new exit alert flow: CA -> draft -> toggle RSI off -> activate fails -> BB on -> activate
        context.user_data["pending"] = {"kind": "exit_new"}
        await ui.receive_input(fake_update(MINT_A), context)
        a = (await db.list("exit_alerts"))[0]
        assert a["status"] == "draft"
        check(await ui.render_exit_card(db, a["id"]))
        await ui.route(["C", "t", str(a["id"]), "rsi_enabled"], db, ctx.data, context)
        with pytest.raises(svc.ValidationError):
            await ui.route(["C", "st", str(a["id"]), "active"], db, ctx.data, context)
        await ui.route(["C", "t", str(a["id"]), "bb_enabled"], db, ctx.data, context)
        context.user_data["pending"] = {"kind": "exit_field", "id": a["id"], "field": "duration_hours"}
        await ui.receive_input(fake_update("2d"), context)
        await ui.route(["C", "st", str(a["id"]), "active"], db, ctx.data, context)
        a = await db.get("exit_alerts", a["id"])
        assert a["status"] == "active" and a["duration_hours"] == 48 and a["bb_enabled"] == 1

        # new token alert flow
        context.user_data["pending"] = {"kind": "token_new"}
        await ui.receive_input(fake_update(MINT_B), context)
        t = (await db.list("token_alerts"))[0]
        await ui.route(["B", "s", str(t["id"]), "metric", "price"], db, ctx.data, context)
        await ui.route(["B", "s", str(t["id"]), "currency", "sol"], db, ctx.data, context)
        with pytest.raises(svc.ValidationError):  # threshold still 0
            await ui.route(["B", "st", str(t["id"]), "active"], db, ctx.data, context)
        context.user_data["pending"] = {"kind": "token_field", "id": t["id"], "field": "threshold"}
        await ui.receive_input(fake_update("0.02"), context)
        await ui.route(["B", "st", str(t["id"]), "active"], db, ctx.data, context)
        t = await db.get("token_alerts", t["id"])
        assert t["status"] == "active" and t["currency"] == "sol" and t["threshold"] == 0.02
        check(await ui.render_token_card(db, t["id"]))
        check(await ui.render_token_list(db))
        check(await ui.render_exit_list(db))
        await ui.route(["B", "xx", str(t["id"])], db, ctx.data, context)
        assert await db.list("token_alerts") == []

        # button handler end-to-end incl. unauthorized user
        upd = fake_update(data="A")
        await ui.on_button(upd, context)
        upd.callback_query.edit_message_text.assert_awaited()
        bad = fake_update(data="A")
        bad.effective_chat.id = 999
        bad.effective_user.id = 999
        await ui.on_button(bad, context)
        bad.callback_query.answer.assert_awaited_with("⛔ Private bot", show_alert=True)
    run(go())
