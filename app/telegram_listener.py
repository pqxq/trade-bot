"""Telegram listener implementation using Telethon."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from telethon import TelegramClient, events, errors

from app.config import Settings
from app.trader import FuturesTrader
from app.scheduler import RuntimeState
from app.signal_parser import parse_signal


class TelegramListener:
    """Listen to Telegram channel messages and process trading signals."""

    def __init__(self, settings: Settings, trader: FuturesTrader, state: RuntimeState) -> None:
        self._settings = settings
        self._trader = trader
        self._state = state
        self._client = TelegramClient(
            str(settings.session_path),
            settings.api_id,
            settings.api_hash,
        )
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Start the listener in the background."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="telegram-listener")

    async def stop(self) -> None:
        """Stop the listener gracefully."""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            await self._client.disconnect()
        except Exception:
            pass

    async def _run(self) -> None:
        backoff = 1
        while not self._stop_event.is_set():
            try:
                self._state.bot_status = "CONNECTING"
                await self._client.connect()
                if not await self._client.is_user_authorized():
                    self._state.bot_status = "AUTH_REQUIRED"
                    logger.error(
                        "Telegram authorization required. "
                        "Please create session file manually first."
                    )
                    await asyncio.sleep(10)
                    continue
                self._state.bot_status = "RUNNING"
                logger.info(
                    "Telegram listener connected to {}",
                    self._settings.channel_name,
                )
                self._client.remove_event_handler(self._on_message)
                self._client.add_event_handler(
                    self._on_message,
                    events.NewMessage(chats=self._settings.channel_name),
                )
                await self._client.run_until_disconnected()
                backoff = 1
            except errors.FloodWaitError as exc:
                self._state.bot_status = f"FLOOD_WAIT_{exc.seconds}s"
                logger.warning("Telegram flood wait: sleeping {}s", exc.seconds)
                await asyncio.sleep(exc.seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._state.bot_status = "ERROR"
                logger.exception("Telegram listener error: {}", exc)
                await asyncio.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)
            finally:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                if not self._stop_event.is_set():
                    self._state.bot_status = "RECONNECTING"
                    logger.info("Telegram listener reconnecting in {}s...", backoff)

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        """Handle incoming Telegram messages."""
        text: str = event.raw_text or ""

        # FIX: datetime.utcnow() is deprecated and returns a naive datetime.
        # event.date from Telethon is already timezone-aware (UTC).
        # Fallback uses datetime.now(timezone.utc) instead.
        timestamp: datetime = event.date or datetime.now(timezone.utc)

        signal = parse_signal(text, timestamp=timestamp)
        if not signal:
            return

        self._state.last_signal_time = signal.timestamp
        self._state.last_signal_type = signal.signal_type
        self._state.last_signal_raw = signal.raw_message

        logger.info(
            "Signal from Telegram: {} @ {}",
            signal.signal_type,
            signal.telegram_price,
        )
        await self._trader.handle_signal(signal)
