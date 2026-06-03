"""Background scheduler for periodic tasks and runtime state."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from loguru import logger


@dataclass
class RuntimeState:
    """Tracks runtime status for dashboard display."""

    bot_status: str = "STARTING"
    last_signal_time: Optional[datetime] = None
    last_signal_type: Optional[str] = None
    last_signal_raw: Optional[str] = None


class Scheduler:
    """Periodic background tasks for the application."""

    def __init__(self, paper_trader: "PaperTrader", interval_seconds: int = 60) -> None:
        """Create a scheduler for periodic background tasks."""
        self._paper_trader = paper_trader
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start periodic tasks."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="equity-snapshot")

    async def stop(self) -> None:
        """Stop periodic tasks."""
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        """Run periodic snapshots."""
        while True:
            try:
                await self._paper_trader.record_equity_snapshot()
            except Exception as exc:
                logger.exception("Equity snapshot failed: {}", exc)
            await asyncio.sleep(self._interval)
