"""
services.py — the ONE place where settings and alerts are validated and saved.

Both the Telegram bot and the web dashboard call these functions, so the rules
are identical everywhere (this is what guarantees "Web & Telegram parity").
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .db import Database

log = logging.getLogger("zenobot.services")

MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # Solana base58 address
SCANNER_TFS = ("5m", "1h", "24h")
TOKEN_TFS = ("1m", "5m", "1h", "24h")
EXIT_TFS = ("1m", "5m", "15m", "1h", "4h")
MAX_DURATION_H = 24 * 90


class ValidationError(ValueError):
    """A friendly error message to show the user."""


# ------------------------------------------------- ON/OFF notifications
# main.py plugs the Telegram sender in here. Every time something is switched
# on or off (from Telegram OR the dashboard) a short message is sent to you.
_notify: Any = None


def set_notifier(send: Any) -> None:
    global _notify
    _notify = send


def _announce(text: str) -> None:
    """Send in the background so the button/dashboard never waits for Telegram."""
    if _notify is None:
        return
    try:
        asyncio.get_running_loop().create_task(_notify(text))
    except RuntimeError:
        pass


def _name(a: dict) -> str:
    import html
    return html.escape(a.get("symbol") or (a["ca"][:4] + "…" + a["ca"][-4:]))


# ================================================================ parsers
def parse_number(v: Any, field: str = "value") -> float:
    """Accepts 50000, '50k', '1.5m', '$2,000', '2b', '3%'."""
    if isinstance(v, bool):
        raise ValidationError(f"{field}: expected a number")
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().lower().replace(",", "").replace("$", "").replace("_", "").replace(" ", "")
    s = s.rstrip("%")
    mult = 1.0
    if s and s[-1] in "kmb":
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        raise ValidationError(f"{field}: '{v}' is not a number (examples: 50000, 50k, 1.5m)")


def parse_int(v: Any, field: str, lo: int, hi: int) -> int:
    n = parse_number(v, field)
    if n != int(n) or not lo <= n <= hi:
        raise ValidationError(f"{field} must be a whole number between {lo} and {hi}")
    return int(n)


def parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "on", "yes", "y")


def parse_duration_hours(v: Any) -> float:
    """'24' or '24h' -> 24, '3d' -> 72, '90m' -> 1.5, '1d 12h' -> 36."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        hours = float(v)
    else:
        s = str(v or "").strip().lower().replace(" ", "")
        if re.fullmatch(r"\d+(\.\d+)?", s):
            hours = float(s)
        else:
            parts = re.findall(r"(\d+(?:\.\d+)?)(d|h|m)", s)
            if not parts or "".join(a + b for a, b in parts) != s:
                raise ValidationError("Duration examples: 12h, 3d, 90m, 1d12h (a plain number means hours)")
            hours = sum(float(n) * {"d": 24, "h": 1, "m": 1 / 60}[u] for n, u in parts)
    if not 0 < hours <= MAX_DURATION_H:
        raise ValidationError(f"Duration must be between 1 minute and {MAX_DURATION_H // 24} days")
    return round(hours, 4)


def parse_mints(text: str | list) -> tuple[list[str], list[str]]:
    """Split pasted text (one per line, comma or space separated). Returns (valid, invalid)."""
    items = text if isinstance(text, list) else re.split(r"[\s,;]+", str(text or ""))
    valid, invalid = [], []
    for raw in items:
        s = str(raw).strip()
        if not s:
            continue
        (valid if MINT_RE.match(s) else invalid).append(s)
    return list(dict.fromkeys(valid)), invalid


def check_mint(ca: Any) -> str:
    s = str(ca or "").strip()
    if not MINT_RE.match(s):
        raise ValidationError("That doesn't look like a Solana contract address (32–44 base58 characters).")
    return s


def _choice(v: Any, options: tuple[str, ...], field: str) -> str:
    s = str(v or "").strip().lower()
    if s not in options:
        raise ValidationError(f"{field} must be one of: {', '.join(options)}")
    return s


# ======================================================= Module A: scanner
async def update_scanner(db: Database, changes: dict[str, Any]) -> dict[str, Any]:
    cur = await db.scanner_settings()
    new = dict(cur)
    for k, v in changes.items():
        if k == "enabled":
            new[k] = parse_bool(v)
        elif k == "timeframe":
            new[k] = _choice(v, SCANNER_TFS, "Timeframe")
        elif k in ("min_volume", "min_mcap", "max_mcap", "min_fees"):
            n = parse_number(v, k.replace("_", " "))
            if n < 0:
                raise ValidationError("Values can't be negative")
            new[k] = n
        elif k == "cooldown_minutes":
            new[k] = parse_int(v, "Cooldown (minutes)", 0, 60 * 24 * 30)
        elif k == "fees_source":
            continue  # setting removed; ignore values sent by old dashboards
        else:
            raise ValidationError(f"Unknown scanner setting: {k}")
    if new["max_mcap"] and new["min_mcap"] > new["max_mcap"]:
        raise ValidationError("Min market cap is higher than max market cap")
    new.pop("fees_source", None)
    await db.set_json("scanner", new)
    if new["enabled"] != cur["enabled"]:
        _announce(f"{'🟢' if new['enabled'] else '⚪'} <b>Volume Scanner</b> turned "
                  f"{'ON' if new['enabled'] else 'OFF'}")
    return new


async def set_blacklist(db: Database, text_or_list: str | list) -> tuple[list[str], list[str]]:
    valid, invalid = parse_mints(text_or_list)
    await db.set_json("blacklist", valid)
    return valid, invalid


async def blacklist_add(db: Database, text: str) -> tuple[list[str], list[str]]:
    add, invalid = parse_mints(text)
    current = await db.blacklist()
    merged = list(dict.fromkeys(current + add))
    await db.set_json("blacklist", merged)
    return merged, invalid


async def blacklist_remove(db: Database, text: str) -> list[str]:
    rm, _ = parse_mints(text)
    merged = [m for m in await db.blacklist() if m not in set(rm)]
    await db.set_json("blacklist", merged)
    return merged


MODULE_NAMES = {"token_alerts_enabled": "Token Alerts", "exit_alerts_enabled": "Exit Alerts"}


async def update_modules(db: Database, changes: dict[str, Any]) -> dict[str, Any]:
    cur = await db.module_settings()
    before = dict(cur)
    for k, v in changes.items():
        if k not in cur:
            raise ValidationError(f"Unknown module setting: {k}")
        cur[k] = parse_bool(v)
    await db.set_json("modules", cur)
    for k, label in MODULE_NAMES.items():
        if cur[k] != before[k]:
            _announce(f"{'🟢' if cur[k] else '⚪'} <b>{label}</b> module turned {'ON' if cur[k] else 'OFF'}")
    return cur


# ================================================== token name lookup
async def fetch_meta(data_hub: Any, ca: str) -> dict[str, str | None]:
    """Best-effort symbol/name lookup so lists look nice. Never raises."""
    if data_hub is None:
        return {"symbol": None, "name": None}
    try:
        tok = await asyncio.wait_for(data_hub.token_info(ca, "system"), timeout=10)
        if tok:
            return {"symbol": tok.get("symbol"), "name": tok.get("name")}
    except Exception as e:  # noqa: BLE001 - lookups must never break saving
        log.info("meta lookup failed for %s: %s", ca, e)
    return {"symbol": None, "name": None}


# ================================================= Module B: token alerts
def normalize_token_alert(data: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate + fill defaults. `base` is the existing alert when editing."""
    a = {**(base or {}), **{k: v for k, v in data.items() if v is not None}}
    out: dict[str, Any] = {"ca": check_mint(a.get("ca"))}
    out["metric"] = _choice(a.get("metric") or "volume", ("volume", "price", "mcap"), "Metric")
    out["direction"] = _choice(a.get("direction") or "above", ("above", "below"), "Direction")
    out["timeframe"] = _choice(a.get("timeframe") or "5m", TOKEN_TFS, "Timeframe") if out["metric"] == "volume" else None
    out["currency"] = _choice(a.get("currency") or "usd", ("usd", "sol"), "Currency") if out["metric"] == "price" else None
    out["threshold"] = parse_number(a.get("threshold", 0), "Threshold")
    if out["threshold"] < 0:
        raise ValidationError("Threshold can't be negative")
    return out


async def create_token_alert(db: Database, data_hub: Any, data: dict[str, Any], status: str = "active") -> int:
    a = normalize_token_alert(data)
    if status == "active" and a["threshold"] <= 0:
        raise ValidationError("Set a threshold greater than 0")
    a.update(await fetch_meta(data_hub, a["ca"]))
    a.update(status=status, armed=1)
    aid = await db.insert("token_alerts", a)
    if status == "active":
        _announce(f"🟢 Token alert #{aid} <b>${_name(a)}</b> turned ON")
    return aid


async def update_token_alert(db: Database, alert_id: int, changes: dict[str, Any],
                             data_hub: Any = None) -> dict[str, Any]:
    cur = await db.get("token_alerts", alert_id)
    if not cur:
        raise ValidationError("Alert not found (maybe it was cancelled)")
    new = normalize_token_alert(changes, cur)
    if cur["status"] == "active" and new["threshold"] <= 0:
        raise ValidationError("Set a threshold greater than 0")
    if new["ca"] != cur["ca"]:
        new.update(await fetch_meta(data_hub, new["ca"]))
    new.update(armed=1, last_value=None, last_error=None)  # re-arm after any edit
    await db.update("token_alerts", alert_id, new)
    return await db.get("token_alerts", alert_id)


async def set_token_alert_status(db: Database, alert_id: int, status: str) -> dict[str, Any]:
    cur = await db.get("token_alerts", alert_id)
    if not cur:
        raise ValidationError("Alert not found")
    status = _choice(status, ("active", "paused"), "Status")
    if status == "active" and (cur["threshold"] or 0) <= 0:
        raise ValidationError("Set a threshold greater than 0 before activating")
    await db.update("token_alerts", alert_id, {"status": status, "armed": 1})
    if status != cur["status"]:
        _announce(f"{'🟢' if status == 'active' else '⏸'} Token alert #{alert_id} <b>${_name(cur)}</b> turned "
                  f"{'ON' if status == 'active' else 'OFF (paused)'}")
    return await db.get("token_alerts", alert_id)


# ================================================== Module C: exit alerts
EXIT_DEFAULTS = {
    "timeframe": "5m", "rsi_enabled": 1, "rsi_length": 14, "rsi_min": 70,
    "bb_enabled": 0, "bb_length": 20, "bb_std": 2, "bb_mode": "upper", "bb_min_pct": 0,
    "combine": "all", "duration_hours": 24,
}


def normalize_exit_alert(data: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    a = {**EXIT_DEFAULTS, **(base or {}), **{k: v for k, v in data.items() if v is not None}}

    # The dashboard sends two boxes: "min above middle %" and "min above upper %".
    # Exactly one of them should be filled; whichever is filled decides the mode.
    mid = data.get("bb_above_middle")
    up = data.get("bb_above_upper")
    mid_f = mid not in (None, "")
    up_f = up not in (None, "")
    if mid_f and up_f:
        raise ValidationError("Fill only ONE of 'Min above Middle Band' or 'Min above Upper Band'")
    if mid_f:
        a["bb_mode"], a["bb_min_pct"] = "middle", mid
    elif up_f:
        a["bb_mode"], a["bb_min_pct"] = "upper", up

    out: dict[str, Any] = {"ca": check_mint(a.get("ca"))}
    out["timeframe"] = _choice(a["timeframe"], EXIT_TFS, "Timeframe")
    out["rsi_enabled"] = int(parse_bool(a["rsi_enabled"]))
    out["rsi_length"] = parse_int(a["rsi_length"], "RSI length", 2, 100)
    out["rsi_min"] = parse_number(a["rsi_min"], "RSI threshold")
    if not 0 <= out["rsi_min"] <= 100:
        raise ValidationError("RSI threshold must be between 0 and 100")
    out["bb_enabled"] = int(parse_bool(a["bb_enabled"]))
    out["bb_length"] = parse_int(a["bb_length"], "BB length", 2, 200)
    out["bb_std"] = parse_number(a["bb_std"], "BB width")
    if not 0 < out["bb_std"] <= 10:
        raise ValidationError("BB width (standard deviations) must be between 0 and 10")
    out["bb_mode"] = _choice(a["bb_mode"], ("middle", "upper"), "BB mode")
    out["bb_min_pct"] = parse_number(a["bb_min_pct"], "BB minimum %")
    if not -100 < out["bb_min_pct"] <= 10_000:
        raise ValidationError("BB minimum % is out of range")
    out["combine"] = _choice(a["combine"], ("all", "any"), "Logic")
    out["duration_hours"] = parse_duration_hours(a["duration_hours"])
    return out


def check_exit_activatable(a: dict[str, Any]) -> None:
    if not int(a.get("rsi_enabled") or 0) and not int(a.get("bb_enabled") or 0):
        raise ValidationError(
            "⚠️ Turn ON at least one indicator (RSI or Bollinger Bands). The alert was NOT switched on."
        )


async def create_exit_alert(db: Database, data_hub: Any, data: dict[str, Any], status: str = "active") -> int:
    a = normalize_exit_alert(data)
    if status == "active":
        check_exit_activatable(a)
        a["expires_at"] = time.time() + a["duration_hours"] * 3600
    a.update(await fetch_meta(data_hub, a["ca"]))
    a.update(status=status, armed=1, pending_count=0)
    aid = await db.insert("exit_alerts", a)
    if status == "active":
        _announce(f"🟢 Exit alert #{aid} <b>${_name(a)}</b> ({a['timeframe']}) turned ON")
    return aid


async def update_exit_alert(db: Database, alert_id: int, changes: dict[str, Any],
                            data_hub: Any = None) -> dict[str, Any]:
    cur = await db.get("exit_alerts", alert_id)
    if not cur:
        raise ValidationError("Alert not found (maybe it was cancelled)")
    new = normalize_exit_alert(changes, cur)
    if cur["status"] == "active":
        check_exit_activatable(new)
        if "duration_hours" in changes:  # new duration counts from now
            new["expires_at"] = time.time() + new["duration_hours"] * 3600
    if new["ca"] != cur["ca"]:
        new.update(await fetch_meta(data_hub, new["ca"]))
    new.update(armed=1, pending_count=0, pending_since=None, last_error=None)
    await db.update("exit_alerts", alert_id, new)
    return await db.get("exit_alerts", alert_id)


async def set_exit_alert_status(db: Database, alert_id: int, status: str) -> dict[str, Any]:
    cur = await db.get("exit_alerts", alert_id)
    if not cur:
        raise ValidationError("Alert not found")
    status = _choice(status, ("active", "paused"), "Status")
    fields: dict[str, Any] = {"status": status, "armed": 1, "pending_count": 0, "pending_since": None}
    if status == "active":
        check_exit_activatable(cur)
        # starting (or restarting an expired one) -> the clock starts now;
        # resuming a paused alert keeps its original end time if still in the future
        if cur["status"] in ("draft", "expired") or not cur.get("expires_at") or cur["expires_at"] <= time.time():
            fields["expires_at"] = time.time() + float(cur["duration_hours"]) * 3600
    await db.update("exit_alerts", alert_id, fields)
    if status != cur["status"]:
        _announce(f"{'🟢' if status == 'active' else '⏸'} Exit alert #{alert_id} <b>${_name(cur)}</b> "
                  f"({cur['timeframe']}) turned {'ON' if status == 'active' else 'OFF (paused)'}")
    return await db.get("exit_alerts", alert_id)


async def delete_alert(db: Database, table: str, alert_id: int) -> None:
    if table not in ("token_alerts", "exit_alerts"):
        raise ValidationError("Unknown alert type")
    cur = await db.get(table, alert_id)
    await db.delete(table, alert_id)
    if cur and cur["status"] in ("active", "paused"):
        kind = "Token" if table == "token_alerts" else "Exit"
        _announce(f"🗑 {kind} alert #{alert_id} <b>${_name(cur)}</b> cancelled (OFF)")
