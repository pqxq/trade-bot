"""Binance Futures Demo Trading exchange client via ccxt.binanceusdm."""
from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, Optional

import ccxt.async_support as ccxt
from loguru import logger

from app.config import Settings

# Binance Futures Demo Trading base URLs
# (replaces the deprecated testnet.binancefuture.com sandbox)
_DEMO_URLS = {
    "fapiPublic": "https://testnet.binancefuture.com/fapi/v1",
    "fapiPublicV2": "https://testnet.binancefuture.com/fapi/v2",
    "fapiPublicV3": "https://testnet.binancefuture.com/fapi/v3",
    "fapiPrivate": "https://testnet.binancefuture.com/fapi/v1",
    "fapiPrivateV2": "https://testnet.binancefuture.com/fapi/v2",
    "fapiPrivateV3": "https://testnet.binancefuture.com/fapi/v3",
    "fapiData": "https://testnet.binancefuture.com/futures/data",
}


class BinanceFuturesExchange:

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # Use binanceusdm — the dedicated USD-M futures subclass.
        # Do NOT use ccxt.binance with defaultType='future' anymore.
        self._exchange: ccxt.binanceusdm = ccxt.binanceusdm(
            {
                "apiKey": settings.binance_api_key,
                "secret": settings.binance_api_secret,
                "enableRateLimit": True,
                "urls": {"api": _DEMO_URLS},
            }
        )
        # set_sandbox_mode on binanceusdm switches URLs to demo endpoints
        self._exchange.set_sandbox_mode(True)
        self._markets: Dict[str, Any] | None = None

    async def initialize(self) -> None:
        await self._load_markets()
        await self._set_leverage()
        logger.info(
            "Exchange ready (Demo Trading) — symbol={} leverage={}",
            self._settings.trading_symbol,
            self._settings.leverage,
        )

    async def _load_markets(self) -> None:
        for attempt in range(1, 5):
            try:
                self._markets = await self._exchange.load_markets()
                logger.info("Markets loaded — {} symbols", len(self._markets))
                return
            except Exception as exc:
                logger.warning("load_markets attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 ** attempt, 10))
        raise RuntimeError("Failed to load Binance markets after retries.")

    async def _set_leverage(self) -> None:
        symbol = self._settings.trading_symbol
        leverage = self._settings.leverage
        # binanceusdm uses fapiPrivatePostLeverage directly
        raw_symbol = symbol.replace("/", "").replace(":USDT", "")
        for attempt in range(1, 4):
            try:
                await self._exchange.fapiPrivatePostLeverage(
                    {"symbol": raw_symbol, "leverage": leverage}
                )
                logger.info("Leverage set to {}x for {}", leverage, symbol)
                return
            except Exception as exc:
                logger.warning("set_leverage attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 6))
        logger.warning("Could not set leverage via API — proceeding anyway.")

    async def get_price(self, symbol: Optional[str] = None) -> float:
        sym = symbol or self._settings.trading_symbol
        last_error: Optional[Exception] = None
        for attempt in range(1, 5):
            try:
                ticker = await self._exchange.fetch_ticker(sym)
                price = (
                    ticker.get("last")
                    or ticker.get("close")
                    or ticker.get("bid")
                )
                if price is None:
                    raise ValueError("Ticker returned no usable price field.")
                return float(price)
            except Exception as exc:
                last_error = exc
                logger.warning("get_price attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 ** attempt, 10))
        raise RuntimeError(f"Failed to fetch price for {sym}") from last_error

    async def get_futures_balance(self) -> float:
        for attempt in range(1, 5):
            try:
                # binanceusdm fetches futures balance natively
                balance = await self._exchange.fetch_balance()
                usdt = balance.get("USDT", {})
                free = usdt.get("free") or usdt.get("total") or 0.0
                return float(free)
            except Exception as exc:
                logger.warning("get_futures_balance attempt {}: {}", attempt, exc)
                await asyncio.sleep(min(2 ** attempt, 10))
        raise RuntimeError("Failed to fetch futures balance after retries.")

    def round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity down to Binance symbol precision."""
        if self._markets:
            # binanceusdm uses BTC/USDT:USDT notation
            candidates = [
                symbol,
                symbol.replace("/", "/") + ":USDT",
                symbol.split("/")[0] + "/USDT:USDT",
            ]
            for key in candidates:
                if key in self._markets:
                    precision = self._markets[key].get("precision", {}).get("amount")
                    if precision is not None:
                        if isinstance(precision, int):
                            factor = 10 ** precision
                        else:
                            factor = 1.0 / float(precision)
                        return math.floor(quantity * factor) / factor
        # Safe fallback: 3 decimal places
        return math.floor(quantity * 1000) / 1000

    async def place_market_order(
        self,
        side: str,
        quantity: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        symbol = self._settings.trading_symbol
        params: Dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        for attempt in range(1, 5):
            try:
                order = await self._exchange.create_market_order(
                    symbol=symbol,
                    side=side,
                    amount=quantity,
                    params=params,
                )
                logger.info(
                    "Order placed — side={} qty={} reduceOnly={} id={}",
                    side,
                    quantity,
                    reduce_only,
                    order.get("id"),
                )
                return order
            except Exception as exc:
                logger.warning(
                    "place_market_order attempt {} (side={} qty={}): {}",
                    attempt, side, quantity, exc,
                )
                await asyncio.sleep(min(2 ** attempt, 10))
        raise RuntimeError(
            f"Failed to place market order side={side} qty={quantity} after retries."
        )

    async def close(self) -> None:
        await self._exchange.close()
        logger.info("Exchange connection closed.")