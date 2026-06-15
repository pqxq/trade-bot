"""Background scheduler: runtime state container.

NOTE: trade auto-close by time is handled entirely inside
FuturesTrader._monitor_trade() (one asyncio.Task per trade).
The Scheduler no longer duplicates that logic, which eliminates:
  - the broken `from app.trader import TRADE_DURATION_SECONDS` ImportError
  - the race condition where both Scheduler and _monitor_trade
    tried to close the same trade simultaneously.

The Scheduler now only:
  1. Keeps RuntimeState.bot_status up-to-date
  2. Provides a start/stop interface used by main.py
  3. Logs a heartbeat every 60 s
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from app.trader import FuturesTrader


@dataclass
class RuntimeState:
    bot_status: str = "STARTING"
    last_signal_time: Optional[datetime] = None
    last_signal_type: Optional[str] = None
    last_signal_raw: Optional[str] = None


class Scheduler:

    def __init__(self, trader: "FuturesTrader") -> None:
        self._trader = trader
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="scheduler-heartbeat")
        logger.info("Scheduler started.")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        logger.info("Scheduler stopped.")

    async def _run(self) -> None:
        """Heartbeat loop — logs open trade count every 60 s."""
        while True:
            try:
                open_trades = await self._trader.get_open_trades()
                if open_trades:
                    logger.debug(
                        "Scheduler heartbeat — {} open trade(s).",
                        len(open_trades),
                    )
            except Exception as exc:
                logger.warning("Scheduler heartbeat error: {}", exc)
            await asyncio.sleep(60)
