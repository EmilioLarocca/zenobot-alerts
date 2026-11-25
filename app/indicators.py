"""
indicators.py — RSI and Bollinger Bands on LIVE candles.

"Live" means: we don't wait for the candle to close. The latest candle from
GeckoTerminal is updated with the current Jupiter price (or a new candle is
started if the current period has no trades yet), exactly like a TradingView
chart that moves in real time.

The math uses pandas-ta. If pandas-ta ever fails to import, an identical
pure-pandas version is used so alerts keep working.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

import pandas as pd

log = logging.getLogger("zenobot.indicators")

try:  # pandas-ta is the preferred engine
    import pandas_ta as ta  # type: ignore
    HAVE_PANDAS_TA = True
except Exception as e:  # pragma: no cover - only when the library is broken
    ta = None
    HAVE_PANDAS_TA = False
    log.warning("pandas-ta not available (%s); using built-in formulas", e)


# ------------------------------------------------------------ live candles
def merge_live_price(df: pd.DataFrame, live_price: float, tf_seconds: int, now: float | None = None) -> pd.DataFrame:
    """Put the live price into the current (unfinished) candle."""
    if df.empty or not live_price or live_price <= 0:
        return df
    now = now or time.time()
    bucket = int(now) // tf_seconds * tf_seconds  # start time of the current candle
    df = df.copy()
    last_ts = int(df["ts"].iloc[-1])
    i = df.index[-1]
    if last_ts >= bucket:
        # current candle exists: move its close to the live price
        df.at[i, "close"] = live_price
        df.at[i, "high"] = max(float(df.at[i, "high"]), live_price)
        df.at[i, "low"] = min(float(df.at[i, "low"]), live_price)
    else:
        # no trade yet in this period: start a new candle from the previous close
        prev_close = float(df["close"].iloc[-1])
        new = {"ts": bucket, "open": prev_close, "high": max(prev_close, live_price),
               "low": min(prev_close, live_price), "close": live_price, "volume": 0.0}
        df = pd.concat([df, pd.DataFrame([new])], ignore_index=True)
    return df


def rescale_to_price(df: pd.DataFrame, live_price: float) -> pd.DataFrame:
    """For fallback candles not in USD: scale so the last close equals the live USD price.
    RSI and Bollinger %-distances are unchanged by scaling, so this is safe."""
    last = float(df["close"].iloc[-1])
    if not last or not live_price:
        return df
    k = live_price / last
    df = df.copy()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * k
    return df


# ------------------------------------------------------------- formulas
def rsi_series(close: pd.Series, length: int) -> pd.Series:
    if HAVE_PANDAS_TA:
        r = ta.rsi(close, length=length, talib=False)
        if r is not None:
            return r
    # Wilder's RSI (same as TradingView)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(avg_loss != 0, 100.0)


def bbands(close: pd.Series, length: int, std: float) -> tuple[float, float, float]:
    """Returns (lower, middle, upper) of the LAST candle."""
    if HAVE_PANDAS_TA:
        # ddof=0 = population standard deviation, the same as TradingView
        bb = ta.bbands(close, length=length, lower_std=std, upper_std=std, ddof=0, talib=False)
        if bb is not None and not bb.empty:
            pick = lambda p: bb[[c for c in bb.columns if c.startswith(p)][0]].iloc[-1]  # noqa: E731
            return float(pick("BBL")), float(pick("BBM")), float(pick("BBU"))
    mid = close.rolling(length).mean()
    sd = close.rolling(length).std(ddof=0)  # population std, same as TradingView
    return float((mid - std * sd).iloc[-1]), float(mid.iloc[-1]), float((mid + std * sd).iloc[-1])


# ------------------------------------------------------------ evaluation
def min_candles_needed(alert: dict[str, Any]) -> int:
    """How much history we need for trustworthy values (avoids false signals on new tokens)."""
    need = 0
    if alert.get("rsi_enabled"):
        need = max(need, int(alert["rsi_length"]) * 2 + 1)  # RSI needs warm-up to stabilise
    if alert.get("bb_enabled"):
        need = max(need, int(alert["bb_length"]) + 1)
    return need


def evaluate(alert: dict[str, Any], df: pd.DataFrame) -> dict[str, Any]:
    """
    Compute indicators on the live candles and decide whether the alert fires.
    Returns a dict with all numbers (used for the message and the dashboard).
    """
    close = df["close"].astype(float)
    res: dict[str, Any] = {"price": float(close.iloc[-1]), "rsi": None, "rsi_ok": None,
                           "bb": None, "bb_pct": None, "bb_ok": None, "triggered": False}
    checks: list[bool] = []

    if alert.get("rsi_enabled"):
        rsi = float(rsi_series(close, int(alert["rsi_length"])).iloc[-1])
        if math.isnan(rsi):
            raise ValueError("RSI not computable yet (not enough candles)")
        res["rsi"] = rsi
        res["rsi_ok"] = rsi >= float(alert["rsi_min"])
        checks.append(res["rsi_ok"])

    if alert.get("bb_enabled"):
        lower, mid, upper = bbands(close, int(alert["bb_length"]), float(alert["bb_std"]))
        if any(math.isnan(x) for x in (lower, mid, upper)) or mid <= 0:
            raise ValueError("Bollinger Bands not computable yet (not enough candles)")
        band = mid if alert.get("bb_mode") == "middle" else upper
        pct = (res["price"] - band) / band * 100 if band else float("nan")
        res["bb"] = {"lower": lower, "middle": mid, "upper": upper}
        res["bb_pct"] = pct
        res["bb_ok"] = pct >= float(alert["bb_min_pct"])
        checks.append(res["bb_ok"])

    if checks:
        res["triggered"] = all(checks) if alert.get("combine", "all") == "all" else any(checks)
    return res
