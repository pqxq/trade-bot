"""Background scheduler: 1s auto-close engine + runtime state."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

CLOSE_CHECK_INTERVAL: int = 1


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
        self._task = asyncio.create_task(self._run(), name="auto-close-check")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        from app.trader import TRADE_DURATION_SECONDS
        while True:
            try:
                open_trades = await self._trader.get_open_trades()
                now = datetime.now(timezone.utc)
                for trade in open_trades:
                    entry = trade.entry_time
                    if entry.tzinfo is None:
                        entry = entry.replace(tzinfo=timezone.utc)
                    elapsed = (now - entry).total_seconds()
                    if elapsed >= TRADE_DURATION_SECONDS:
                        logger.info(
                            "Scheduler closing trade #{} (elapsed={:.1f}s)",
                            trade.id, elapsed,
                        )
                        asyncio.create_task(
                            self._trader._close_trade(trade.id),
                            name=f"sched-close-{trade.id}",
                        )
            except Exception as exc:
                logger.exception("Auto-close check error: {}", exc)
            await asyncio.sleep(CLOSE_CHECK_INTERVAL)