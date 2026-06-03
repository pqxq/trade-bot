"""Binance Futures live trading engine.

Every signal opens an independent trade that auto-closes after 60 seconds.
Multiple simultaneous positions are fully supported.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import run_with_retry, session_scope
from app.exchange import BinanceFuturesExchange
from app.models import Signal, Trade
from app.signal_parser import SIGNAL_MAPPING, SignalData

TRADE_DURATION_SECONDS: int = 60


class FuturesTrader:

    def __init__(self, settings: Settings, exchange: BinanceFuturesExchange) -> None:
        self._settings = settings
        self._exchange = exchange
        self._session_factory: sessionmaker[Session] | None = None

    def set_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # ── Public ─────────────────────────────────────────────────────────

    async def handle_signal(self, signal: SignalData) -> None:
        logger.info("Signal received: {} price={}", signal.signal_type, signal.telegram_price)
        await asyncio.to_thread(self._store_signal, signal)
        if signal.signal_type not in SIGNAL_MAPPING:
            logger.info("Signal '{}' not in mapping — skipping.", signal.signal_type)
            return
        await self._open_trade(signal)

    async def get_futures_balance(self) -> float:
        return await self._exchange.get_futures_balance()

    async def get_open_trades(self) -> List[Trade]:
        return await asyncio.to_thread(self._fetch_open_trades)

    async def get_all_trades(self) -> List[Trade]:
        return await asyncio.to_thread(self._fetch_all_trades)

    # ── Trade lifecycle ────────────────────────────────────────────────

    async def _open_trade(self, signal: SignalData) -> None:
        side_label, risk_fraction = SIGNAL_MAPPING[signal.signal_type]

        try:
            balance = await self._exchange.get_futures_balance()
            current_price = await self._exchange.get_price()
        except Exception as exc:
            logger.error("Pre-trade data fetch failed: {}", exc)
            return

        leverage = self._settings.leverage
        notional = balance * risk_fraction * leverage
        raw_qty = notional / current_price
        quantity = self._exchange.round_quantity(self._settings.trading_symbol, raw_qty)

        if quantity <= 0:
            logger.warning(
                "Quantity=0 (balance={} risk={} lev={} price={}) — skipping.",
                balance, risk_fraction, leverage, current_price,
            )
            return

        ccxt_side = "buy" if side_label == "LONG" else "sell"

        try:
            order = await self._exchange.place_market_order(side=ccxt_side, quantity=quantity)
        except Exception as exc:
            logger.error("Open order failed for {}: {}", signal.signal_type, exc)
            return

        entry_time = datetime.now(timezone.utc)
        filled_price = float(order.get("average") or order.get("price") or current_price)
        binance_order_id = str(order.get("id", ""))

        trade = await asyncio.to_thread(
            self._create_trade,
            signal, side_label, quantity, filled_price, entry_time, binance_order_id,
        )

        logger.info(
            "Trade #{} OPEN — {} {} qty={} entry={} order_id={}",
            trade.id, side_label, signal.signal_type, quantity, filled_price, binance_order_id,
        )

        asyncio.create_task(
            self._schedule_close(trade.id, entry_time),
            name=f"auto-close-{trade.id}",
        )

    async def _schedule_close(self, trade_id: int, entry_time: datetime) -> None:
        await asyncio.sleep(TRADE_DURATION_SECONDS)
        await self._close_trade(trade_id)

    async def _close_trade(self, trade_id: int) -> None:
        trade = await asyncio.to_thread(self._fetch_trade_by_id, trade_id)
        if trade is None or not trade.is_open:
            logger.debug("Trade #{} already closed.", trade_id)
            return

        ccxt_side = "sell" if trade.side == "LONG" else "buy"

        try:
            order = await self._exchange.place_market_order(
                side=ccxt_side, quantity=trade.quantity, reduce_only=True
            )
        except Exception as exc:
            logger.error("Close order failed for trade #{}: {}", trade_id, exc)
            return

        exit_time = datetime.now(timezone.utc)
        exit_price = float(order.get("average") or order.get("price") or 0.0)
        if exit_price == 0.0:
            try:
                exit_price = await self._exchange.get_price()
            except Exception:
                exit_price = trade.entry_price

        entry_tz = trade.entry_time.replace(tzinfo=timezone.utc) if trade.entry_time.tzinfo is None else trade.entry_time
        duration_seconds = (exit_time - entry_tz).total_seconds()
        notional_entry = trade.entry_price * trade.quantity

        if trade.side == "LONG":
            pnl_usdt = (exit_price - trade.entry_price) * trade.quantity * trade.leverage
        else:
            pnl_usdt = (trade.entry_price - exit_price) * trade.quantity * trade.leverage

        pnl_percent = (pnl_usdt / notional_entry * 100) if notional_entry else 0.0

        await asyncio.to_thread(
            self._update_trade_close,
            trade_id, exit_price, exit_time, duration_seconds, pnl_usdt, pnl_percent,
        )

        logger.info(
            "Trade #{} CLOSED — exit={} pnl={:.4f} USDT ({:.2f}%) duration={}s",
            trade_id, exit_price, pnl_usdt, pnl_percent, int(duration_seconds),
        )

    # ── DB helpers ─────────────────────────────────────────────────────

    def _store_signal(self, signal: SignalData) -> None:
        def op() -> None:
            with session_scope(self._session_factory) as s:
                s.add(Signal(
                    timestamp=signal.timestamp,
                    raw_message=signal.raw_message,
                    signal_type=signal.signal_type,
                    indicator=signal.indicator_value,
                    telegram_price=signal.telegram_price,
                ))
        run_with_retry(op)

    def _create_trade(
        self, signal: SignalData, side: str, quantity: float,
        entry_price: float, entry_time: datetime, binance_order_id: str,
    ) -> Trade:
        def op() -> Trade:
            with session_scope(self._session_factory) as s:
                trade = Trade(
                    signal_type=signal.signal_type,
                    indicator_value=signal.indicator_value,
                    side=side,
                    leverage=self._settings.leverage,
                    quantity=quantity,
                    entry_price=entry_price,
                    entry_time=entry_time,
                    binance_order_id=binance_order_id,
                    is_open=True,
                )
                s.add(trade)
                s.flush()
                s.expunge(trade)
                return trade
        return run_with_retry(op)

    def _update_trade_close(
        self, trade_id: int, exit_price: float, exit_time: datetime,
        duration_seconds: float, pnl_usdt: float, pnl_percent: float,
    ) -> None:
        def op() -> None:
            with session_scope(self._session_factory) as s:
                t = s.query(Trade).filter(Trade.id == trade_id).first()
                if t:
                    t.exit_price = exit_price
                    t.exit_time = exit_time
                    t.duration_seconds = duration_seconds
                    t.pnl_usdt = pnl_usdt
                    t.pnl_percent = pnl_percent
                    t.is_open = False
        run_with_retry(op)

    def _fetch_open_trades(self) -> List[Trade]:
        def op() -> List[Trade]:
            with session_scope(self._session_factory) as s:
                return s.query(Trade).filter(Trade.is_open == True).all()  # noqa: E712
        return run_with_retry(op)

    def _fetch_all_trades(self) -> List[Trade]:
        def op() -> List[Trade]:
            with session_scope(self._session_factory) as s:
                return s.query(Trade).order_by(Trade.entry_time.desc()).all()
        return run_with_retry(op)

    def _fetch_trade_by_id(self, trade_id: int) -> Trade | None:
        def op() -> Trade | None:
            with session_scope(self._session_factory) as s:
                return s.query(Trade).filter(Trade.id == trade_id).first()
        return run_with_retry(op)