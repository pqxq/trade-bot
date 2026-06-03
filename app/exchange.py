"""Exchange access via ccxt."""
from __future__ import annotations

import asyncio
from typing import Optional

import ccxt.async_support as ccxt
from loguru import logger


class BinanceExchange:
    """Fetch market prices from Binance using ccxt."""

    def __init__(self) -> None:
        """Initialize the Binance exchange client."""
        self._exchange = ccxt.binance({"enableRateLimit": True})

    async def get_price(self, symbol: str) -> float:
        """Fetch the latest price for a symbol."""
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                ticker = await self._exchange.fetch_ticker(symbol)
                price = ticker.get("last") or ticker.get("close") or ticker.get("bid")
                if price is None:
                    raise ValueError("Ticker did not include a usable price.")
                return float(price)
            except Exception as exc:
                last_error = exc
                logger.warning("Price fetch failed (attempt {}): {}", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 5))
        raise RuntimeError(f"Failed to fetch price for {symbol}") from last_error

    async def close(self) -> None:
        """Close the exchange connection."""
        await self._exchange.close()
