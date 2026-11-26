"""
config.py — reads all settings that come from ENVIRONMENT VARIABLES.

On Railway you set these in the service's "Variables" tab. Nothing secret is
ever written in the code itself.

Required:
    TELEGRAM_BOT_TOKEN   token from @BotFather
    CHAT_ID              your Telegram numeric id (from @userinfobot)

Recommended:
    DASHBOARD_PASSWORD   password for the web dashboard. If you leave it empty
                         the bot invents one and sends it to you on Telegram.

Optional (sensible defaults are used when missing):
    GMGN_API_KEY         your GMGN OpenAPI key (https://gmgn.ai/ai). Strongly recommended:
                         it powers the volume scanner (incl. total fees) and real-time
                         candles for exit alerts. A read-only key (trading disabled) is enough.
    GMGN_RPS             GMGN rate-limit units per second (default 4; free plan allows 5)
    JUPITER_API_KEY      free key from https://portal.jup.ag (doubles Jupiter speed)
    GECKO_RPM            GeckoTerminal calls per minute (default 8, keyless limit is ~5-10)
    JUPITER_RPM          Jupiter calls per minute (default 24 keyless / 50 with key)
    MODULE_RPM           max external calls per minute PER alert module (default 15)
    DATA_DIR             folder for the database (auto-detected on Railway volumes)
    PORT                 web port (Railway sets this automatically)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    """Read an integer env var, falling back to the default if empty/invalid."""
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _pick_data_dir() -> tuple[Path, bool]:
    """
    Decide where the SQLite file lives.
    Returns (folder, is_persistent).
    Railway sets RAILWAY_VOLUME_MOUNT_PATH when a volume is attached; anything
    outside a volume is wiped on every redeploy.
    """
    explicit = os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if explicit:
        p = Path(explicit)
        p.mkdir(parents=True, exist_ok=True)
        return p, True
    if Path("/data").is_dir() and os.access("/data", os.W_OK):
        return Path("/data"), True
    p = Path(__file__).resolve().parent.parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p, False


@dataclass(frozen=True)
class Config:
    telegram_token: str
    chat_id: int
    dashboard_password: str | None
    jupiter_api_key: str | None
    gmgn_api_key: str | None
    data_dir: Path
    volume_persistent: bool
    port: int
    public_url: str | None
    gecko_rpm: int
    jupiter_rpm: int
    module_rpm: int
    meteora_rps: int
    gmgn_rps: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "zenobot.db"


def load_config(require_telegram: bool = True) -> Config:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_raw = os.getenv("CHAT_ID", "").strip()
    if require_telegram and (not token or not chat_raw):
        raise SystemExit(
            "\n\n*** Missing settings ***\n"
            "Add TELEGRAM_BOT_TOKEN and CHAT_ID in Railway -> your service -> Variables.\n"
        )
    try:
        chat_id = int(chat_raw) if chat_raw else 0
    except ValueError:
        raise SystemExit("CHAT_ID must be a number, e.g. 123456789 (get it from @userinfobot)")

    jup_key = os.getenv("JUPITER_API_KEY", "").strip() or None
    data_dir, persistent = _pick_data_dir()

    # Railway exposes the public domain of the service in this variable.
    # Fly.io gives every app the address <app-name>.fly.dev.
    fly_app = os.getenv("FLY_APP_NAME")
    domain = (os.getenv("PUBLIC_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN")
              or (f"{fly_app}.fly.dev" if fly_app else None))
    if domain and not domain.startswith("http"):
        domain = "https://" + domain

    return Config(
        telegram_token=token,
        chat_id=chat_id,
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", "").strip() or None,
        jupiter_api_key=jup_key,
        gmgn_api_key=os.getenv("GMGN_API_KEY", "").strip() or None,
        data_dir=data_dir,
        volume_persistent=persistent,
        port=_int("PORT", 8080),
        public_url=domain,
        gecko_rpm=_int("GECKO_RPM", 8),
        jupiter_rpm=_int("JUPITER_RPM", 50 if jup_key else 24),
        module_rpm=_int("MODULE_RPM", 15),
        meteora_rps=_int("METEORA_RPS", 5),
        gmgn_rps=_int("GMGN_RPS", 4),
    )
