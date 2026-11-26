"""
db.py — the shared memory of the bot (a single SQLite file).

Both the Telegram bot and the web dashboard read and write THIS database, which
is how they stay in sync: change something in one place and the other sees it
on its next read (Telegram re-reads on every button press, the dashboard polls
every few seconds).

A counter called "rev" (revision) goes up on every change so the dashboard
knows when it needs to refresh.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

# --------------------------------------------------------------------------
# Default settings. Edit these numbers if you want different starting values.
# --------------------------------------------------------------------------
DEFAULT_SCANNER: dict[str, Any] = {
    "enabled": False,          # Module A starts switched OFF
    "timeframe": "5m",         # 5m | 1h | 24h
    "min_volume": 50_000,      # USD volume in the selected timeframe
    "min_mcap": 100_000,       # USD, 0 = no minimum
    "max_mcap": 50_000_000,    # USD, 0 = no maximum
    "min_fees": 0,             # USD total fees paid by traders (GMGN), 0 = don't filter
    "cooldown_minutes": 60,    # don't alert the same token again for this long
}

DEFAULT_MODULES: dict[str, Any] = {
    "token_alerts_enabled": True,   # Module B master switch
    "exit_alerts_enabled": True,    # Module C master switch
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Module B: token-specific alerts
CREATE TABLE IF NOT EXISTS token_alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ca             TEXT NOT NULL,
    symbol         TEXT,
    name           TEXT,
    metric         TEXT NOT NULL,             -- volume | price | mcap
    timeframe      TEXT,                      -- for volume: 1m | 5m | 1h | 24h
    currency       TEXT,                      -- for price: usd | sol
    direction      TEXT NOT NULL DEFAULT 'above',  -- above | below
    threshold      REAL NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'active', -- draft | active | paused
    armed          INTEGER NOT NULL DEFAULT 1,     -- 1 = ready to fire
    last_value     REAL,
    last_checked   REAL,
    last_triggered REAL,
    last_error     TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);

-- Module C: exit alerts (RSI / Bollinger)
CREATE TABLE IF NOT EXISTS exit_alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ca             TEXT NOT NULL,
    symbol         TEXT,
    name           TEXT,
    timeframe      TEXT NOT NULL DEFAULT '5m',  -- 1m | 5m | 15m | 1h | 4h
    rsi_enabled    INTEGER NOT NULL DEFAULT 1,
    rsi_length     INTEGER NOT NULL DEFAULT 14,
    rsi_min        REAL NOT NULL DEFAULT 70,
    bb_enabled     INTEGER NOT NULL DEFAULT 0,
    bb_length      INTEGER NOT NULL DEFAULT 20,
    bb_std         REAL NOT NULL DEFAULT 2,
    bb_mode        TEXT NOT NULL DEFAULT 'upper',  -- middle | upper
    bb_min_pct     REAL NOT NULL DEFAULT 0,
    combine        TEXT NOT NULL DEFAULT 'all',    -- all = every ON indicator must hit; any = one is enough
    duration_hours REAL NOT NULL DEFAULT 24,
    expires_at     REAL,
    status         TEXT NOT NULL DEFAULT 'active', -- draft | active | paused | expired
    armed          INTEGER NOT NULL DEFAULT 1,
    pending_since  REAL,                           -- first time the condition was seen (confirmation)
    pending_count  INTEGER NOT NULL DEFAULT 0,
    last_rsi       REAL,
    last_bb_pct    REAL,
    last_price     REAL,
    last_checked   REAL,
    last_triggered REAL,
    last_error     TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);

-- Module A: remembers when each token was last alerted (cooldown)
CREATE TABLE IF NOT EXISTS scanner_seen (
    ca           TEXT PRIMARY KEY,
    last_alerted REAL NOT NULL
);

-- Activity log shown on the dashboard
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    module TEXT NOT NULL,
    text   TEXT NOT NULL
);
"""

# Columns that may be written from outside (protects against typos/injection)
TOKEN_ALERT_COLS = {
    "ca", "symbol", "name", "metric", "timeframe", "currency", "direction", "threshold",
    "status", "armed", "last_value", "last_checked", "last_triggered", "last_error",
}
EXIT_ALERT_COLS = {
    "ca", "symbol", "name", "timeframe", "rsi_enabled", "rsi_length", "rsi_min",
    "bb_enabled", "bb_length", "bb_std", "bb_mode", "bb_min_pct", "combine",
    "duration_hours", "expires_at", "status", "armed", "pending_since", "pending_count",
    "last_rsi", "last_bb_pct", "last_price", "last_checked", "last_triggered", "last_error",
}
TABLE_COLS = {"token_alerts": TOKEN_ALERT_COLS, "exit_alerts": EXIT_ALERT_COLS}


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    # ------------------------------------------------------------------ setup
    @classmethod
    async def open(cls, path: str | Path) -> "Database":
        conn = await aiosqlite.connect(str(path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self.conn.close()

    # ------------------------------------------------------- revision counter
    async def rev(self) -> int:
        cur = await self.conn.execute("SELECT value FROM kv WHERE key='rev'")
        row = await cur.fetchone()
        return int(row["value"]) if row else 0

    async def _bump(self) -> None:
        await self.conn.execute(
            "INSERT INTO kv(key,value) VALUES('rev','1') "
            "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"
        )

    # -------------------------------------------------------- key/value store
    async def get_json(self, key: str, default: Any = None) -> Any:
        cur = await self.conn.execute("SELECT value FROM kv WHERE key=?", (key,))
        row = await cur.fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    async def set_json(self, key: str, value: Any, bump: bool = True) -> None:
        await self.conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        if bump:
            await self._bump()
        await self.conn.commit()

    # Convenience getters that always return complete settings dicts
    async def scanner_settings(self) -> dict[str, Any]:
        stored = await self.get_json("scanner", {}) or {}
        merged = {**DEFAULT_SCANNER, **stored}
        merged.pop("fees_source", None)        # removed setting (older versions)
        if merged["timeframe"] not in ("5m", "1h", "24h"):
            merged["timeframe"] = "5m"         # 1m was removed
        return merged

    async def module_settings(self) -> dict[str, Any]:
        stored = await self.get_json("modules", {}) or {}
        return {**DEFAULT_MODULES, **stored}

    async def blacklist(self) -> list[str]:
        return list(await self.get_json("blacklist", []) or [])

    # ------------------------------------------------- generic alert helpers
    async def insert(self, table: str, data: dict[str, Any]) -> int:
        cols = [c for c in data if c in TABLE_COLS[table]]
        now = time.time()
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}, created_at, updated_at) "
            f"VALUES ({', '.join('?' for _ in cols)}, ?, ?)"
        )
        cur = await self.conn.execute(sql, [data[c] for c in cols] + [now, now])
        await self._bump()
        await self.conn.commit()
        return int(cur.lastrowid)

    async def update(self, table: str, row_id: int, fields: dict[str, Any], bump: bool = True) -> None:
        cols = [c for c in fields if c in TABLE_COLS[table]]
        if not cols:
            return
        sql = f"UPDATE {table} SET {', '.join(f'{c}=?' for c in cols)}, updated_at=? WHERE id=?"
        await self.conn.execute(sql, [fields[c] for c in cols] + [time.time(), row_id])
        if bump:
            await self._bump()
        await self.conn.commit()

    async def get(self, table: str, row_id: int) -> dict[str, Any] | None:
        assert table in TABLE_COLS
        cur = await self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list(self, table: str, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        assert table in TABLE_COLS
        if statuses:
            q = f"SELECT * FROM {table} WHERE status IN ({','.join('?' for _ in statuses)}) ORDER BY id"
            cur = await self.conn.execute(q, statuses)
        else:
            cur = await self.conn.execute(f"SELECT * FROM {table} ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]

    async def delete(self, table: str, row_id: int) -> None:
        assert table in TABLE_COLS
        await self.conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
        await self._bump()
        await self.conn.commit()

    async def delete_old_drafts(self, max_age_s: float = 86_400) -> None:
        """Drafts are half-finished alerts started in Telegram; remove stale ones."""
        cutoff = time.time() - max_age_s
        for t in TABLE_COLS:
            await self.conn.execute(f"DELETE FROM {t} WHERE status='draft' AND created_at<?", (cutoff,))
        await self.conn.commit()

    # ------------------------------------------------------ scanner cooldown
    async def scanner_last_alerted(self, ca: str) -> float | None:
        cur = await self.conn.execute("SELECT last_alerted FROM scanner_seen WHERE ca=?", (ca,))
        row = await cur.fetchone()
        return float(row["last_alerted"]) if row else None

    async def scanner_all_last_alerted(self) -> dict[str, float]:
        cur = await self.conn.execute("SELECT ca, last_alerted FROM scanner_seen")
        return {r["ca"]: float(r["last_alerted"]) for r in await cur.fetchall()}

    async def scanner_mark(self, ca: str, ts: float | None = None) -> None:
        await self.conn.execute(
            "INSERT INTO scanner_seen(ca,last_alerted) VALUES(?,?) "
            "ON CONFLICT(ca) DO UPDATE SET last_alerted=excluded.last_alerted",
            (ca, ts or time.time()),
        )
        await self.conn.commit()

    async def scanner_prune(self, older_than_s: float = 7 * 86_400) -> None:
        await self.conn.execute("DELETE FROM scanner_seen WHERE last_alerted<?", (time.time() - older_than_s,))
        await self.conn.commit()

    # ------------------------------------------------------------ event log
    async def add_event(self, module: str, text: str) -> None:
        await self.conn.execute("INSERT INTO events(ts,module,text) VALUES(?,?,?)", (time.time(), module, text))
        # keep only the latest 300 entries
        await self.conn.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 300)"
        )
        await self._bump()
        await self.conn.commit()

    async def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        cur = await self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]
