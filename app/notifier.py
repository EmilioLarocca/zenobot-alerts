"""
notifier.py — sends alert messages to your Telegram chat.

Handles Telegram's own limits (flood control) and never lets a failed message
crash the alert loops.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

log = logging.getLogger("zenobot.notify")


class Notifier:
    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self._lock = asyncio.Lock()  # one message at a time keeps us under Telegram limits

    async def send(self, text: str, photo: str | None = None, buttons: Any = None) -> bool:
        """Send an alert. `buttons` = optional inline keyboard (e.g. "Cancel this alert")."""
        async with self._lock:
            for attempt in range(3):
                try:
                    if photo and len(text) <= 1024:  # captions are limited to 1024 chars
                        try:
                            await self.bot.send_photo(self.chat_id, photo=photo, caption=text,
                                                      parse_mode=ParseMode.HTML, reply_markup=buttons)
                            return True
                        except RetryAfter:
                            raise
                        except TelegramError as e:
                            # bad/unsupported image (e.g. svg) -> send as text instead
                            log.info("photo failed (%s), sending text only", e)
                    await self.bot.send_message(self.chat_id, text, parse_mode=ParseMode.HTML,
                                                disable_web_page_preview=True, reply_markup=buttons)
                    return True
                except RetryAfter as e:
                    ra = getattr(e, "retry_after", 5)
                    wait = ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra or 5)
                    await asyncio.sleep(wait + 1)
                except TelegramError as e:
                    log.error("Telegram send failed (attempt %d): %s", attempt + 1, e)
                    await asyncio.sleep(2 * (attempt + 1))
            return False
