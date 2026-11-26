"""
telegram_ui.py — the Telegram control panel (buttons + step-by-step questions).

How it is organised:
  * render_*()  functions build a screen (text + buttons) from the DATABASE,
                so Telegram always shows exactly what the dashboard shows.
  * on_button() handles every button press ("callback data" strings below).
  * The ConversationHandler asks for typed values ("Send me the new threshold…")
    and saves them through services.py (same rules as the dashboard).

Callback data cheat-sheet (max 64 bytes each):
  menu                         main menu
  A / A:tog / A:cfg            scanner screen / on-off / configure
  A:tf:<tf>                    scanner timeframe
  A:ask:<field>                ask for a number
  A:bl / A:bl:add|rm|set|clr   blacklist screen / actions
  B / B:mod / B:new            token alerts list / module on-off / new
  B:e:<id>                     edit card
  B:s:<id>:<field>:<value>     set a choice (metric, timeframe, currency, direction)
  B:ask:<id>:<field>           ask for a number
  B:st:<id>:<status>           activate / pause
  B:x:<id> / B:xx:<id>         cancel (confirm) / cancel (do it)
  C …                          same pattern for exit alerts, plus C:t:<id>:<field> toggles
"""
from __future__ import annotations

import logging
import time
import warnings
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from . import formatting as fmt
from . import services as svc
from .db import Database

log = logging.getLogger("zenobot.telegram")
warnings.filterwarnings("ignore", category=PTBUserWarning)

INPUT = 1  # the single "waiting for your typed answer" state

ON, OFF = "🟢 ON", "⚪ OFF"
STATUS_ICON = {"active": "🟢", "paused": "⏸", "draft": "📝", "expired": "⌛"}

Screen = tuple[str, InlineKeyboardMarkup]


def b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def tick(cond: bool, label: str) -> str:
    return f"✅ {label}" if cond else label


# ============================================================== SCREENS
async def render_main(db: Database, public_url: str | None = None) -> Screen:
    s = await db.scanner_settings()
    m = await db.module_settings()
    tok = await db.list("token_alerts", ("active",))
    ex = await db.list("exit_alerts", ("active",))
    text = (
        "🤖 <b>zenobot_alerts</b> — control panel\n\n"
        f"📡 <b>Volume Scanner:</b> {ON if s['enabled'] else OFF}  "
        f"({s['timeframe']} ≥ {fmt.usd(s['min_volume'], 0)})\n"
        f"🎯 <b>Token Alerts:</b> {ON if m['token_alerts_enabled'] else OFF} · {len(tok)} active\n"
        f"🚪 <b>Exit Alerts:</b> {ON if m['exit_alerts_enabled'] else OFF} · {len(ex)} active\n\n"
        "<i>Everything here is shared live with the web dashboard.</i>"
    )
    rows = [[b("📡 Volume Scanner", "A")], [b("🎯 Token Alerts", "B")], [b("🚪 Exit Alerts", "C")]]
    last = [b("🔄 Refresh", "menu")]
    if public_url:
        last.insert(0, InlineKeyboardButton("🌐 Dashboard", url=public_url))
    rows.append(last)
    return text, InlineKeyboardMarkup(rows)


def scanner_summary(s: dict) -> str:
    max_mc = fmt.usd(s["max_mcap"], 0) if s["max_mcap"] else "no max"
    fees = f"≥ {fmt.usd(s['min_fees'], 0)} (GMGN)" if s["min_fees"] else "off"
    return (
        f"Timeframe: <b>{s['timeframe']}</b>\n"
        f"Min volume: <b>{fmt.usd(s['min_volume'], 0)}</b>\n"
        f"Market cap: <b>{fmt.usd(s['min_mcap'], 0)} – {max_mc}</b>\n"
        f"Min total fees: <b>{fees}</b>\n"
        f"Cooldown: <b>{int(s['cooldown_minutes'])} min</b>"
    )


async def render_scanner(db: Database) -> Screen:
    s = await db.scanner_settings()
    bl = await db.blacklist()
    text = (f"📡 <b>Global Volume Scanner</b> — {ON if s['enabled'] else OFF}\n\n"
            f"{scanner_summary(s)}\nBlacklisted tokens: <b>{len(bl)}</b>")
    kb = [
        [b("⚪ Turn OFF" if s["enabled"] else "🟢 Turn ON", "A:tog")],
        [b("⚙️ Configure", "A:cfg"), b(f"🚫 Blacklist ({len(bl)})", "A:bl")],
        [b("⬅️ Back", "menu")],
    ]
    return text, InlineKeyboardMarkup(kb)


async def render_scanner_cfg(db: Database) -> Screen:
    s = await db.scanner_settings()
    text = f"⚙️ <b>Scanner settings</b>\n\n{scanner_summary(s)}\n\nTap what you want to change:"
    kb = [
        [b(tick(s["timeframe"] == tf, tf), f"A:tf:{tf}") for tf in svc.SCANNER_TFS],
        [b(f"Volume ≥ {fmt.usd(s['min_volume'], 0)}", "A:ask:min_volume")],
        [b(f"Min MC {fmt.usd(s['min_mcap'], 0)}", "A:ask:min_mcap"),
         b(f"Max MC {fmt.usd(s['max_mcap'], 0) if s['max_mcap'] else '∞'}", "A:ask:max_mcap")],
        [b(f"Min total fees {fmt.usd(s['min_fees'], 0) if s['min_fees'] else 'off'}", "A:ask:min_fees")],
        [b(f"Cooldown {int(s['cooldown_minutes'])} min", "A:ask:cooldown_minutes")],
        [b("⬅️ Back", "A")],
    ]
    return text, InlineKeyboardMarkup(kb)


async def render_blacklist(db: Database) -> Screen:
    bl = await db.blacklist()
    shown = "\n".join(f"• <code>{m}</code>" for m in bl[:30]) or "<i>(empty)</i>"
    more = f"\n…and {len(bl) - 30} more (see dashboard)" if len(bl) > 30 else ""
    text = f"🚫 <b>Scanner blacklist</b> ({len(bl)})\nThese tokens are ignored by the scanner.\n\n{shown}{more}"
    kb = [
        [b("➕ Add", "A:bl:add"), b("➖ Remove", "A:bl:rm")],
        [b("📝 Replace all", "A:bl:set"), b("🗑 Clear", "A:bl:clr")],
        [b("⬅️ Back", "A")],
    ]
    return text, InlineKeyboardMarkup(kb)


def token_line(a: dict) -> str:
    arrow = "≥" if a["direction"] == "above" else "≤"
    now = f" · now {fmt.metric_value(a, a['last_value'])}" if a.get("last_value") is not None else ""
    sym = fmt.esc(a.get("symbol") or fmt.short_ca(a["ca"]))
    return (f"{STATUS_ICON.get(a['status'], '')} #{a['id']} <b>${sym}</b> — {fmt.metric_label(a)} {arrow} "
            f"{fmt.metric_value(a, a['threshold'])}{now}")


async def render_token_list(db: Database) -> Screen:
    m = await db.module_settings()
    alerts = await db.list("token_alerts")
    lines = [token_line(a) for a in alerts] or ["<i>No alerts yet. Tap ➕ New alert.</i>"]
    text = (f"🎯 <b>Token Alerts</b> — {ON if m['token_alerts_enabled'] else OFF}\n\n" + "\n".join(lines))
    kb = [[b(f"✏️ Edit #{a['id']} ${(a.get('symbol') or fmt.short_ca(a['ca']))[:12]}", f"B:e:{a['id']}"),
           b("❌ Cancel", f"B:x:{a['id']}")] for a in alerts[:40]]
    kb.append([b("➕ New alert", "B:new")])
    kb.append([b("⚪ Module OFF" if m["token_alerts_enabled"] else "🟢 Module ON", "B:mod"), b("⬅️ Back", "menu")])
    return text, InlineKeyboardMarkup(kb)


async def render_token_card(db: Database, aid: int, note: str = "") -> Screen:
    a = await db.get("token_alerts", aid)
    if not a:
        return await render_token_list(db)
    status = {"draft": "📝 Draft (not active yet)", "active": "🟢 Active", "paused": "⏸ Paused"}[a["status"]]
    text = (
        f"🎯 <b>Token alert #{a['id']}</b> — {status}\n"
        f"{fmt.token_title(a.get('symbol'), a.get('name'))}\n<code>{a['ca']}</code>\n\n"
        f"{token_line(a)}\n"
        + (f"⚠️ {fmt.esc(a['last_error'])}\n" if a.get("last_error") and a["status"] == "active" else "")
        + (f"\n{note}" if note else "")
        + "\nPick the metric, then set the threshold:"
    )
    i = a["id"]
    kb = [[b(tick(a["metric"] == k, lbl), f"B:s:{i}:metric:{k}")
           for k, lbl in (("volume", "Volume"), ("price", "Price"), ("mcap", "Market cap"))]]
    if a["metric"] == "volume":
        kb.append([b(tick(a["timeframe"] == tf, tf), f"B:s:{i}:timeframe:{tf}") for tf in svc.TOKEN_TFS])
    if a["metric"] == "price":
        kb.append([b(tick(a["currency"] == c, c.upper()), f"B:s:{i}:currency:{c}") for c in ("usd", "sol")])
    kb.append([b(tick(a["direction"] == d, lbl), f"B:s:{i}:direction:{d}")
               for d, lbl in (("above", "Above ⬆️"), ("below", "Below ⬇️"))])
    kb.append([b(f"✏️ Threshold: {fmt.metric_value(a, a['threshold']) if a['threshold'] else 'not set'}",
                 f"B:ask:{i}:threshold")])
    if a["status"] == "active":
        kb.append([b("⏸ Pause", f"B:st:{i}:paused")])
    else:
        kb.append([b("▶️ Activate" if a["status"] == "draft" else "▶️ Resume", f"B:st:{i}:active")])
    kb.append([b("❌ Cancel alert", f"B:x:{i}"), b("⬅️ Back", "B")])
    return text, InlineKeyboardMarkup(kb)


def exit_summary(a: dict) -> str:
    parts = []
    if a["rsi_enabled"]:
        parts.append(f"RSI({a['rsi_length']}) ≥ {fmt.number(a['rsi_min'])}")
    if a["bb_enabled"]:
        band = "Mid" if a["bb_mode"] == "middle" else "Upper"
        parts.append(f"BB({a['bb_length']},{fmt.number(a['bb_std'])}) ≥ {band}+{fmt.number(a['bb_min_pct'])}%")
    return (" AND " if a["combine"] == "all" else " OR ").join(parts) or "no indicator ON"


def exit_line(a: dict) -> str:
    sym = fmt.esc(a.get("symbol") or fmt.short_ca(a["ca"]))
    ends = ""
    if a["status"] == "active" and a.get("expires_at"):
        ends = f" · ends in {fmt.duration(a['expires_at'] - time.time())}"
    live = ""
    if a.get("last_triggered"):
        live += f" · last alert {fmt.duration(time.time() - a['last_triggered'])} ago"
    if a.get("last_rsi") is not None:
        live += f" · RSI {a['last_rsi']:.1f}"
    if a.get("last_bb_pct") is not None:
        live += f" · BB {fmt.pct(a['last_bb_pct'])}"
    return (f"{STATUS_ICON.get(a['status'], '')} #{a['id']} <b>${sym}</b> {a['timeframe']} — "
            f"{exit_summary(a)}{ends}{live}")


async def render_exit_list(db: Database) -> Screen:
    m = await db.module_settings()
    alerts = await db.list("exit_alerts")
    lines = [exit_line(a) for a in alerts] or ["<i>No exit alerts yet. Tap ➕ New exit alert.</i>"]
    text = f"🚪 <b>Exit Alerts</b> — {ON if m['exit_alerts_enabled'] else OFF}\n\n" + "\n".join(lines)
    kb = [[b(f"✏️ Edit #{a['id']} ${(a.get('symbol') or fmt.short_ca(a['ca']))[:12]}", f"C:e:{a['id']}"),
           b("❌ Cancel", f"C:x:{a['id']}")] for a in alerts[:40]]
    kb.append([b("➕ New exit alert", "C:new")])
    kb.append([b("⚪ Module OFF" if m["exit_alerts_enabled"] else "🟢 Module ON", "C:mod"), b("⬅️ Back", "menu")])
    return text, InlineKeyboardMarkup(kb)


async def render_exit_card(db: Database, aid: int, note: str = "") -> Screen:
    a = await db.get("exit_alerts", aid)
    if not a:
        return await render_exit_list(db)
    status = {"draft": "📝 Draft (not active yet)", "active": "🟢 Active", "paused": "⏸ Paused",
              "expired": "⌛ Expired"}[a["status"]]
    extra = ""
    if a["status"] == "active" and a.get("expires_at"):
        extra += f"⏳ Ends in {fmt.duration(a['expires_at'] - time.time())}\n"
    if a.get("last_error") and a["status"] == "active":
        extra += f"⚠️ {fmt.esc(a['last_error'])}\n"
    text = (
        f"🚪 <b>Exit alert #{a['id']}</b> — {status}\n"
        f"{fmt.token_title(a.get('symbol'), a.get('name'))}\n<code>{a['ca']}</code>\n\n"
        f"Condition: <b>{exit_summary(a)}</b>\nTimeframe: <b>{a['timeframe']}</b> · "
        f"Duration: <b>{fmt.hours_label(a['duration_hours'])}</b>\n{extra}"
        + (f"\n{note}\n" if note else "")
    )
    i = a["id"]
    kb = [
        [b(tick(a["timeframe"] == tf, tf), f"C:s:{i}:timeframe:{tf}") for tf in svc.EXIT_TFS],
        [b(f"RSI: {'🟢 ON' if a['rsi_enabled'] else '⚪ OFF'}", f"C:t:{i}:rsi_enabled"),
         b(f"Length {a['rsi_length']}", f"C:ask:{i}:rsi_length"),
         b(f"Min {fmt.number(a['rsi_min'])}", f"C:ask:{i}:rsi_min")],
        [b(f"BB: {'🟢 ON' if a['bb_enabled'] else '⚪ OFF'}", f"C:t:{i}:bb_enabled"),
         b(f"Length {a['bb_length']}", f"C:ask:{i}:bb_length"),
         b(f"Width {fmt.number(a['bb_std'])}", f"C:ask:{i}:bb_std")],
        [b(tick(a["bb_mode"] == "middle", "Above Middle"), f"C:s:{i}:bb_mode:middle"),
         b(tick(a["bb_mode"] == "upper", "Above Upper"), f"C:s:{i}:bb_mode:upper"),
         b(f"Min {fmt.number(a['bb_min_pct'])}%", f"C:ask:{i}:bb_min_pct")],
        [b(f"Logic: {'ALL must hit' if a['combine'] == 'all' else 'ANY can hit'} 🔁", f"C:t:{i}:combine"),
         b(f"⏳ {fmt.hours_label(a['duration_hours'])}", f"C:ask:{i}:duration_hours")],
    ]
    if a["status"] == "active":
        kb.append([b("⏸ Pause", f"C:st:{i}:paused")])
    else:
        kb.append([b({"draft": "▶️ Activate", "expired": "🔁 Restart"}.get(a["status"], "▶️ Resume"),
                     f"C:st:{i}:active")])
    kb.append([b("❌ Cancel alert", f"C:x:{i}"), b("⬅️ Back", "C")])
    return text, InlineKeyboardMarkup(kb)


def confirm_screen(kind: str, aid: int) -> Screen:
    return (f"Cancel alert #{aid}? This deletes it.",
            InlineKeyboardMarkup([[b("🗑 Yes, cancel it", f"{kind}:xx:{aid}"), b("No", f"{kind}:e:{aid}")]]))


# ===================================================== access control
def allowed(update: Update, cfg: Any) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    return bool((chat and chat.id == cfg.chat_id) or (user and user.id == cfg.chat_id))


def deps(context: ContextTypes.DEFAULT_TYPE) -> tuple[Database, Any, Any]:
    bd = context.application.bot_data
    return bd["db"], bd["data"], bd["cfg"]


async def show(update: Update, screen: Screen, new_message: bool = False) -> None:
    """Edit the current panel (button press) or send a new one (commands / typed answers)."""
    text, kb = screen
    q = update.callback_query
    if q and not new_message:
        try:
            await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML,
                                      disable_web_page_preview=True)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            log.info("edit failed (%s), sending new message", e)
    await update.effective_chat.send_message(text, reply_markup=kb, parse_mode=ParseMode.HTML,
                                             disable_web_page_preview=True)


# ============================================================ COMMANDS
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, cfg = deps(context)
    if not allowed(update, cfg):
        await update.effective_chat.send_message("⛔ This bot is private.")
        return
    await show(update, await render_main(db, context.application.bot_data.get("public_url")), new_message=True)


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, _, cfg = deps(context)
    if not allowed(update, cfg):
        return
    bd = context.application.bot_data
    url = bd.get("public_url") or "(no public URL yet — Railway → Settings → Networking → Generate Domain)"
    await update.effective_chat.send_message(
        f"🌐 Dashboard: {url}\nUsername: <code>admin</code>\nPassword: <tg-spoiler>{fmt.esc(bd.get('password'))}</tg-spoiler>",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, data, cfg = deps(context)
    if not allowed(update, cfg):
        return
    lines = ["🩺 <b>Data sources</b>"]
    for host, st in data.status().items():
        icon = {True: "🟢", False: "🔴", None: "⚪"}[st["healthy"]]
        err = f" — last error: {fmt.esc(st['error'])}" if st["error"] else ""
        lines.append(f"{icon} {host}{err}")
    lines.append(f"\nGMGN API key: {'yes ✅' if cfg.gmgn_api_key else 'no ⚠️ (scanner fees + fast candles off)'}")
    lines.append(f"Jupiter API key: {'yes' if cfg.jupiter_api_key else 'no (keyless, slower)'}")
    lines.append(f"Database on persistent volume: {'yes ✅' if cfg.volume_persistent else 'NO ⚠️'}")
    await update.effective_chat.send_message("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, _, cfg = deps(context)
    if not allowed(update, cfg):
        return
    await update.effective_chat.send_message(
        "/settings — control panel\n/dashboard — web dashboard link + password\n"
        "/status — data source health\n/cancel — stop typing a value\n\n"
        "Numbers accept shortcuts: 50k = 50,000 · 1.5m = 1,500,000.\n"
        "Durations: 12h · 3d · 90m.")


# ============================================================= BUTTONS
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Every button that does NOT need typing ends up here."""
    q = update.callback_query
    db, data, cfg = deps(context)
    if not allowed(update, cfg):
        await q.answer("⛔ Private bot", show_alert=True)
        return ConversationHandler.END
    context.user_data.pop("pending", None)  # pressing any button cancels a pending question
    parts = (q.data or "menu").split(":")
    try:
        screen = await route(parts, db, data, context)
        await q.answer()
    except svc.ValidationError as e:
        await q.answer(str(e)[:200], show_alert=True)
        return ConversationHandler.END
    await show(update, screen)
    return ConversationHandler.END


async def route(p: list[str], db: Database, data: Any, context: ContextTypes.DEFAULT_TYPE) -> Screen:
    head = p[0]
    if head == "menu" or head == "noop":
        return await render_main(db, context.application.bot_data.get("public_url"))

    # ---------------- Module A
    if head == "A":
        if len(p) == 1:
            return await render_scanner(db)
        act = p[1]
        if act == "tog":
            s = await db.scanner_settings()
            await svc.update_scanner(db, {"enabled": not s["enabled"]})
            return await render_scanner(db)
        if act == "cfg":
            return await render_scanner_cfg(db)
        if act == "tf":
            await svc.update_scanner(db, {"timeframe": p[2]})
            return await render_scanner_cfg(db)
        if act == "bl":
            if len(p) == 3 and p[2] == "clr":
                return ("Remove ALL tokens from the blacklist?",
                        InlineKeyboardMarkup([[b("🗑 Yes, clear", "A:bl:clr2"), b("No", "A:bl")]]))
            if len(p) == 3 and p[2] == "clr2":
                await svc.set_blacklist(db, [])
            return await render_blacklist(db)

    # ---------------- Module B
    if head == "B":
        if len(p) == 1:
            return await render_token_list(db)
        act = p[1]
        if act == "mod":
            m = await db.module_settings()
            await svc.update_modules(db, {"token_alerts_enabled": not m["token_alerts_enabled"]})
            return await render_token_list(db)
        aid = int(p[2])
        if act == "e":
            return await render_token_card(db, aid)
        if act == "s":
            await svc.update_token_alert(db, aid, {p[3]: p[4]}, data)
            return await render_token_card(db, aid)
        if act == "st":
            await svc.set_token_alert_status(db, aid, p[3])
            return await render_token_card(db, aid, "✅ Saved.")
        if act == "x":
            return confirm_screen("B", aid)
        if act == "xx":
            await svc.delete_alert(db, "token_alerts", aid)
            return await render_token_list(db)

    # ---------------- Module C
    if head == "C":
        if len(p) == 1:
            return await render_exit_list(db)
        act = p[1]
        if act == "mod":
            m = await db.module_settings()
            await svc.update_modules(db, {"exit_alerts_enabled": not m["exit_alerts_enabled"]})
            return await render_exit_list(db)
        aid = int(p[2])
        if act == "e":
            return await render_exit_card(db, aid)
        if act == "s":
            await svc.update_exit_alert(db, aid, {p[3]: p[4]}, data)
            return await render_exit_card(db, aid)
        if act == "t":
            a = await db.get("exit_alerts", aid)
            if not a:
                raise svc.ValidationError("Alert not found")
            field = p[3]
            new = ("any" if a["combine"] == "all" else "all") if field == "combine" else (0 if a[field] else 1)
            await svc.update_exit_alert(db, aid, {field: new}, data)
            return await render_exit_card(db, aid)
        if act == "st":
            await svc.set_exit_alert_status(db, aid, p[3])
            return await render_exit_card(db, aid, "✅ Saved.")
        if act == "x":
            return confirm_screen("C", aid)
        if act == "xx":
            await svc.delete_alert(db, "exit_alerts", aid)
            return await render_exit_list(db)

    return await render_main(db, context.application.bot_data.get("public_url"))


# ======================================================= TYPED ANSWERS
SCANNER_PROMPTS = {
    "min_volume": "Send the minimum <b>volume in USD</b> for the selected timeframe.\nExamples: <code>50000</code> · <code>50k</code> · <code>1.2m</code>",
    "min_mcap": "Send the <b>minimum market cap</b> in USD (0 = no minimum).\nExamples: <code>100k</code> · <code>1m</code>",
    "max_mcap": "Send the <b>maximum market cap</b> in USD (0 = no maximum).\nExamples: <code>10m</code> · <code>500k</code>",
    "min_fees": "Send the <b>minimum total fees</b> in USD, as shown on GMGN (0 = don't filter).",
    "cooldown_minutes": "Send the <b>cooldown in minutes</b> (how long before the same token can alert again).\nExample: <code>60</code>",
}
EXIT_PROMPTS = {
    "rsi_length": "Send the <b>RSI length</b> (2–100). Default is 14.",
    "rsi_min": "Send the <b>RSI threshold</b> (0–100). The alert fires when RSI is at or above it. Default 70.",
    "bb_length": "Send the <b>Bollinger length</b> (2–200). Default is 20.",
    "bb_std": "Send the <b>Bollinger width</b> (number of standard deviations). Default is 2.",
    "bb_min_pct": "Send the <b>minimum % above the band</b>. Example: <code>2</code> = price at least 2% above. "
                  "<code>0</code> = touching or above.",
    "duration_hours": "Send <b>how long</b> this alert should run.\nExamples: <code>12h</code> · <code>3d</code> · <code>90m</code>",
}
CANCEL_KB = InlineKeyboardMarkup([[b("✖️ Cancel", "menu")]])


async def ask_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A button that needs a typed value was pressed: ask the question."""
    q = update.callback_query
    db, _, cfg = deps(context)
    if not allowed(update, cfg):
        await q.answer("⛔ Private bot", show_alert=True)
        return ConversationHandler.END
    await q.answer()
    p = q.data.split(":")
    prompt = ""
    if p[0] == "A" and p[1] == "ask":
        context.user_data["pending"] = {"kind": "scanner", "field": p[2]}
        prompt = SCANNER_PROMPTS[p[2]]
    elif p[0] == "A" and p[1] == "bl":
        context.user_data["pending"] = {"kind": f"bl_{p[2]}"}
        prompt = {"add": "Paste the contract address(es) to <b>add</b> (one per line or comma-separated).",
                  "rm": "Paste the contract address(es) to <b>remove</b>.",
                  "set": "Paste the <b>full new blacklist</b> (replaces the current one)."}[p[2]]
    elif p[1] == "new":
        context.user_data["pending"] = {"kind": "token_new" if p[0] == "B" else "exit_new"}
        prompt = "Send me the token <b>contract address (CA)</b>."
    elif p[0] == "B" and p[1] == "ask":
        a = await db.get("token_alerts", int(p[2]))
        context.user_data["pending"] = {"kind": "token_field", "id": int(p[2]), "field": p[3]}
        unit = {"volume": f"USD volume ({(a or {}).get('timeframe') or '5m'})",
                "price": f"price in {((a or {}).get('currency') or 'usd').upper()}",
                "mcap": "market cap in USD"}.get((a or {}).get("metric"), "value")
        prompt = (f"Send the <b>threshold</b> ({unit}).\n"
                  "Examples: <code>250k</code> · <code>0.00042</code> · <code>5m</code>")
    elif p[0] == "C" and p[1] == "ask":
        context.user_data["pending"] = {"kind": "exit_field", "id": int(p[2]), "field": p[3]}
        prompt = EXIT_PROMPTS[p[3]]
    await show(update, (f"✍️ {prompt}\n\n<i>Type your answer below, or /cancel.</i>", CANCEL_KB))
    return INPUT


async def receive_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db, data, cfg = deps(context)
    if not allowed(update, cfg):
        return ConversationHandler.END
    pend = context.user_data.get("pending")
    if not pend:
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    kind = pend["kind"]
    try:
        if kind == "scanner":
            await svc.update_scanner(db, {pend["field"]: text})
            screen = await render_scanner_cfg(db)
        elif kind == "bl_add":
            _, bad = await svc.blacklist_add(db, text)
            screen = await render_blacklist(db)
            if bad:
                await update.message.reply_text(f"Ignored {len(bad)} invalid entr{'y' if len(bad) == 1 else 'ies'}.")
        elif kind == "bl_rm":
            await svc.blacklist_remove(db, text)
            screen = await render_blacklist(db)
        elif kind == "bl_set":
            _, bad = await svc.set_blacklist(db, text)
            screen = await render_blacklist(db)
            if bad:
                await update.message.reply_text(f"Ignored {len(bad)} invalid entr{'y' if len(bad) == 1 else 'ies'}.")
        elif kind == "token_new":
            ca = svc.check_mint(text)
            await update.message.reply_text("🔎 Looking up the token…")
            aid = await svc.create_token_alert(db, data, {"ca": ca, "threshold": 0}, status="draft")
            screen = await render_token_card(db, aid, "Step 2: choose the metric, set the threshold, then ▶️ Activate.")
        elif kind == "exit_new":
            ca = svc.check_mint(text)
            await update.message.reply_text("🔎 Looking up the token…")
            aid = await svc.create_exit_alert(db, data, {"ca": ca}, status="draft")
            screen = await render_exit_card(db, aid, "Step 2: adjust the indicators (defaults shown), then ▶️ Activate.")
        elif kind == "token_field":
            await svc.update_token_alert(db, pend["id"], {pend["field"]: text}, data)
            a = await db.get("token_alerts", pend["id"])
            hint = "Now tap ▶️ Activate." if a and a["status"] == "draft" else "✅ Saved."
            screen = await render_token_card(db, pend["id"], hint)
        elif kind == "exit_field":
            await svc.update_exit_alert(db, pend["id"], {pend["field"]: text}, data)
            screen = await render_exit_card(db, pend["id"], "✅ Saved.")
        else:
            return ConversationHandler.END
    except svc.ValidationError as e:
        await update.message.reply_text(f"⚠️ {e}\n\nPlease try again, or /cancel.")
        return INPUT  # stay in the same question
    context.user_data.pop("pending", None)
    await show(update, screen, new_message=True)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("pending", None)
    db, _, cfg = deps(context)
    if allowed(update, cfg):
        await show(update, await render_main(db, context.application.bot_data.get("public_url")), new_message=True)
    return ConversationHandler.END


ASK_PATTERN = r"^(A:ask:.+|A:bl:(add|rm|set)|B:new|C:new|B:ask:.+|C:ask:.+)$"


def build_application(cfg: Any, db: Database, data: Any, public_url: str | None, password: str) -> Application:
    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data.update(db=db, data=data, cfg=cfg, public_url=public_url, password=password)

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_entry, pattern=ASK_PATTERN)],
        states={INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_input)]},
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(ask_entry, pattern=ASK_PATTERN),  # pressing another "ask" button
            CallbackQueryHandler(on_button),                       # any other button ends the question
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler(["start", "settings"], cmd_start))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_button))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Telegram handler error: %s", context.error, exc_info=context.error)

    app.add_error_handler(on_error)
    return app


async def set_commands(app: Application) -> None:
    try:
        await app.bot.set_my_commands([
            BotCommand("settings", "Open the control panel"),
            BotCommand("dashboard", "Web dashboard link + password"),
            BotCommand("status", "Data source health"),
            BotCommand("help", "Help"),
            BotCommand("cancel", "Cancel the current question"),
        ])
    except Exception as e:  # noqa: BLE001
        log.warning("could not set bot commands: %s", e)
