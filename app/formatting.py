"""
formatting.py — turns numbers into readable text and builds alert messages.

Edit the message templates here if you want alerts to look different.
Telegram "HTML" formatting is used: <b>bold</b>, <i>italic</i>, <code>code</code>.
"""
from __future__ import annotations

import html
import time
from typing import Any

TF_LABEL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "24h": "24h"}


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def usd(x: float | None, decimals: int = 2) -> str:
    """$1.23M style for big numbers."""
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    x = abs(x)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if x >= div:
            return f"{sign}${x / div:.{decimals}f}{unit}"
    return f"{sign}${x:,.{decimals}f}"


def price(x: float | None, cur: str = "usd") -> str:
    """Prices of memecoins can be tiny: keep 4 significant digits."""
    if x is None:
        return "n/a"
    sym = "$" if cur == "usd" else ""
    suffix = "" if cur == "usd" else " SOL"
    if x == 0:
        return f"{sym}0{suffix}"
    if abs(x) >= 1:
        return f"{sym}{x:,.4f}".rstrip("0").rstrip(".") + suffix
    return f"{sym}{x:.4g}" + suffix if abs(x) >= 1e-4 else f"{sym}{x:.4e}{suffix}"


def pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{'+' if x >= 0 else ''}{x:.2f}%"


def number(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86_400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def hours_label(hours: float) -> str:
    return duration(hours * 3600)


def short_ca(ca: str) -> str:
    return f"{ca[:4]}…{ca[-4:]}" if ca and len(ca) > 10 else (ca or "")


def links(ca: str, meteora_pool: str | None = None) -> str:
    parts = [
        f'<a href="https://gmgn.ai/sol/token/{ca}">GMGN</a>',
        f'<a href="https://jup.ag/tokens/{ca}">Jupiter</a>',
    ]
    if meteora_pool:
        parts.append(f'<a href="https://app.meteora.ag/dlmm/{meteora_pool}">Meteora</a>')
    return " | ".join(parts)


def token_title(symbol: str | None, name: str | None) -> str:
    sym = esc(symbol or "?")
    return f"<b>${sym}</b> — {esc(name or '')}"


# ----------------------------------------------------------- alert bodies
def scanner_alert(tok: dict, tf: str, vol: float | None, chg: float | None) -> str:
    src = "GMGN" if tok.get("source") == "gmgn" else "Jupiter"
    fees_line = f"Total fees: {usd(tok.get('fees'))} ({src})" if tok.get("fees") is not None else "Total fees: n/a"
    return (
        "📡 <b>Volume Scanner Hit</b>\n"
        f"{token_title(tok.get('symbol'), tok.get('name'))}\n\n"
        f"Volume ({tf}): <b>{usd(vol)}</b>\n"
        f"Price change ({tf}): {pct(chg)}\n"
        f"Market cap: {usd(tok.get('mcap'))}\n"
        f"{fees_line}\n\n"
        f"<code>{esc(tok.get('mint'))}</code>\n"
        f"{links(tok.get('mint'))}"
    )


def metric_label(a: dict) -> str:
    if a["metric"] == "volume":
        return f"Volume ({a.get('timeframe') or '5m'})"
    if a["metric"] == "price":
        return f"Price ({(a.get('currency') or 'usd').upper()})"
    return "Market cap"


def metric_value(a: dict, v: float | None) -> str:
    if a["metric"] == "price":
        return price(v, a.get("currency") or "usd")
    return usd(v)


def token_alert(a: dict, value: float, tok: dict | None) -> str:
    arrow = "≥" if a["direction"] == "above" else "≤"
    return (
        "<b>🎯 Special Alert Hit!</b>\n"
        f"{token_title((tok or {}).get('symbol') or a.get('symbol'), (tok or {}).get('name') or a.get('name'))}\n\n"
        f"Metric: <b>{metric_label(a)}</b>\n"
        f"Now: <b>{metric_value(a, value)}</b>  (target {arrow} {metric_value(a, a['threshold'])})\n\n"
        f"<code>{esc(a['ca'])}</code>\n"
        f"{links(a['ca'])}"
    )


def exit_alert(a: dict, res: dict, tok: dict | None, source_note: str = "",
               meteora_pool: str | None = None, reminder: bool = False) -> str:
    lines = [
        "🔁 <b>Exit Position Alert — reminder</b>" if reminder else "🚪 <b>Exit Position Alert</b>",
        token_title((tok or {}).get("symbol") or a.get("symbol"), (tok or {}).get("name") or a.get("name")),
        f"Timeframe: {a['timeframe']} (live candle) · Logic: {'ALL' if a['combine'] == 'all' else 'ANY'}",
        "",
    ]
    if a["rsi_enabled"]:
        ok = "✅" if res.get("rsi_ok") else "❌"
        lines.append(f"RSI({a['rsi_length']}): <b>{number(res.get('rsi'))}</b> (min {number(a['rsi_min'])}) {ok}")
    if a["bb_enabled"]:
        ok = "✅" if res.get("bb_ok") else "❌"
        band = "Middle" if a["bb_mode"] == "middle" else "Upper"
        bb = res.get("bb") or {}
        lines.append(
            f"BB({a['bb_length']}, {number(a['bb_std'])}): price <b>{pct(res.get('bb_pct'))}</b> vs {band} band "
            f"(min {number(a['bb_min_pct'])}%) {ok}"
        )
        lines.append(f"   Upper {price(bb.get('upper'))} · Middle {price(bb.get('middle'))} · Lower {price(bb.get('lower'))}")
    lines += [
        f"Price: {price(res.get('price'))}",
        f"Market cap: {usd((tok or {}).get('mcap'))}",
        f"5m volume: {usd(((tok or {}).get('volume') or {}).get('5m'))}",
        "",
        f"⏳ Monitoring ends in {duration((a.get('expires_at') or time.time()) - time.time())}",
    ]
    if source_note:
        lines.append(f"<i>{esc(source_note)}</i>")
    lines += [f"<code>{esc(a['ca'])}</code>", links(a["ca"], meteora_pool)]
    return "\n".join(lines)
