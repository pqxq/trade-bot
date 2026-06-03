"""Binance Futures Testnet exchange client via ccxt."""
from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, Optional

import ccxt.async_support as ccxt
from loguru import logger

from app.config import Settings


class BinanceFuturesExchange:

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._exchange: ccxt.binance = ccxt.binance(
            {
                "apiKey": settings.binance_api_key,
                "secret": settings.binance_api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )
        self._exchange.set_sandbox_mode(True)
        self._markets: Dict[str, Any] | None = None

    async def initialize(self) -> None:
        await self._load_markets()
        await self._set_leverage()
        logger.info(
            "Exchange ready — symbol={} leverage={}",
            self._settings.trading_symbol,
            self._settings.leverage,
        )

    async def _load_markets(self) -> None:
        for attempt in range(1, 4):
            try:
                self._markets = await self._exchange.load_markets()
                return
            except Exception as exc:
                logger.warning("load_markets attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 5))
        raise RuntimeError("Failed to load Binance markets after retries.")

    async def _set_leverage(self) -> None:
        symbol = self._settings.trading_symbol
        leverage = self._settings.leverage
        for attempt in range(1, 4):
            try:
                await self._exchange.fapiPrivatePostLeverage(
                    {"symbol": symbol.replace("/", ""), "leverage": leverage}
                )
                logger.info("Leverage set to {} for {}", leverage, symbol)
                return
            except Exception as exc:
                logger.warning("set_leverage attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 5))
        logger.warning("Could not set leverage via API; continuing anyway.")

    async def get_price(self, symbol: Optional[str] = None) -> float:
        sym = symbol or self._settings.trading_symbol
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                ticker = await self._exchange.fetch_ticker(sym)
                price = ticker.get("last") or ticker.get("close") or ticker.get("bid")
                if price is None:
                    raise ValueError("Ticker returned no usable price.")
                return float(price)
            except Exception as exc:
                last_error = exc
                logger.warning("get_price attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 5))
        raise RuntimeError(f"Failed to fetch price for {sym}") from last_error

    async def get_futures_balance(self) -> float:
        for attempt in range(1, 4):
            try:
                balance = await self._exchange.fetch_balance({"type": "future"})
                usdt = balance.get("USDT", {})
                free = usdt.get("free") or usdt.get("total") or 0.0
                return float(free)
            except Exception as exc:
                logger.warning("get_futures_balance attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 5))
        raise RuntimeError("Failed to fetch futures balance.")

    def round_quantity(self, symbol: str, quantity: float) -> float:
        if self._markets and symbol in self._markets:
            market = self._markets[symbol]
            precision = market.get("precision", {}).get("amount")
            if precision is not None:
                factor = 10 ** precision if isinstance(precision, int) else 1 / float(precision)
                return math.floor(quantity * factor) / factor
        return math.floor(quantity * 1000) / 1000

    async def place_market_order(
        self, side: str, quantity: float, reduce_only: bool = False
    ) -> Dict[str, Any]:
        symbol = self._settings.trading_symbol
        params: Dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        for attempt in range(1, 4):
            try:
                order = await self._exchange.create_market_order(
                    symbol=symbol, side=side, amount=quantity, params=params
                )
                logger.info(
                    "Order placed — side={} qty={} reduce_only={} id={}",
                    side, quantity, reduce_only, order.get("id"),
                )
                return order
            except Exception as exc:
                logger.warning("place_market_order attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 5))
        raise RuntimeError(f"Failed to place market order side={side} qty={quantity}.")

    async def close(self) -> None:
        await self._exchange.close()