"""context.py — a small box holding the shared objects every module needs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config
from .datasources import DataHub
from .db import Database


@dataclass
class Ctx:
    cfg: Config
    db: Database
    data: DataHub
    notifier: Any  # notifier.Notifier (or a fake one in tests)
