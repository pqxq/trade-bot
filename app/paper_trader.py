"""Paper trading engine."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import run_with_retry, session_scope
from app.exchange import BinanceExchange
from app.models import PortfolioHistory, Position, Signal, Trade
from app.signal_parser import SignalData


class PaperTrader:
    """Executes paper trades based on parsed signals."""

    def __init__(self, settings: Settings, exchange: BinanceExchange) -> None:
        """Initialize the paper trading engine."""
        self._settings = settings
        self._exchange = exchange
        self._lock = asyncio.Lock()
        self._session_factory = None

    def set_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        """Inject the session factory after DB initialization."""
        self._session_factory = session_factory

    async def handle_signal(self, signal: SignalData) -> None:
        """Handle a trading signal."""
        async with self._lock:
            await asyncio.to_thread(self._store_signal, signal)
            if signal.signal_type in {"STRONG BUY", "BUY"}:
                await self._open_position_if_needed()
            elif signal.signal_type in {"STRONG SELL", "SELL"}:
                await self._close_position_if_needed()
            else:
                logger.info("Ignored signal: {}", signal.signal_type)

    async def ensure_initial_history(self) -> None:
        """Ensure the initial balance is recorded."""
        await asyncio.to_thread(self._ensure_initial_history_sync)

    async def record_equity_snapshot(self) -> None:
        """Record the current equity to portfolio history."""
        equity = await self.get_current_equity()
        await asyncio.to_thread(self._record_portfolio_balance, equity)

    async def get_current_equity(self) -> float:
        """Get the current equity, including open positions."""
        open_position = await asyncio.to_thread(self._get_open_position)
        if open_position:
            price = await self._exchange.get_price("BTC/USDT")
            return open_position.quantity * price
        return await asyncio.to_thread(self._get_cash_balance)

    async def get_cash_balance(self) -> float:
        """Get current cash balance without unrealized PnL."""
        return await asyncio.to_thread(self._get_cash_balance)

    async def _open_position_if_needed(self) -> None:
        """Open a new long position if none exists."""
        open_position = await asyncio.to_thread(self._get_open_position)
        if open_position:
            logger.info("Open position exists; ignoring buy signal.")
            return

        price = await self._exchange.get_price("BTC/USDT")
        balance = await asyncio.to_thread(self._get_cash_balance)
        quantity = balance / price
        entry_time = datetime.now(timezone.utc)

        await asyncio.to_thread(
            self._create_position,
            entry_time,
            price,
            quantity,
        )
        logger.info("Opened position at {} qty {}", price, quantity)

    async def _close_position_if_needed(self) -> None:
        """Close the current position if one exists."""
        open_position = await asyncio.to_thread(self._get_open_position)
        if not open_position:
            logger.info("No open position; ignoring sell signal.")
            return

        exit_time = datetime.now(timezone.utc)
        exit_price = await self._exchange.get_price("BTC/USDT")
        profit_usdt = (exit_price - open_position.entry_price) * open_position.quantity
        profit_percent = (profit_usdt / (open_position.entry_price * open_position.quantity)) * 100

        await asyncio.to_thread(
            self._close_position,
            open_position.id,
            exit_time,
            exit_price,
            profit_usdt,
            profit_percent,
        )
        balance = await asyncio.to_thread(self._get_cash_balance)
        await asyncio.to_thread(self._record_portfolio_balance, balance)

        logger.info("Closed position at {} profit {}", exit_price, profit_usdt)

    def _store_signal(self, signal: SignalData) -> None:
        """Persist a signal to the database."""
        if not self._session_factory:
            raise RuntimeError("Session factory not set.")

        def operation() -> None:
            """Insert a signal row."""
            with session_scope(self._session_factory) as session:
                session.add(
                    Signal(
                        timestamp=signal.timestamp,
                        raw_message=signal.raw_message,
                        signal_type=signal.signal_type,
                        indicator=signal.indicator_value,
                        telegram_price=signal.telegram_price,
                    )
                )
            logger.info("Stored signal in database: {}", signal.signal_type)

        run_with_retry(operation)

    def _get_open_position(self) -> Optional[Position]:
        """Retrieve the current open position if any."""
        if not self._session_factory:
            raise RuntimeError("Session factory not set.")

        def operation() -> Optional[Position]:
            """Fetch the open position row."""
            with session_scope(self._session_factory) as session:
                return (
                    session.query(Position)
                    .filter(Position.status == "open")
                    .order_by(Position.entry_time.desc())
                    .first()
                )

        return run_with_retry(operation)

    def _create_position(self, entry_time: datetime, entry_price: float, quantity: float) -> None:
        """Create a new open position."""
        if not self._session_factory:
            raise RuntimeError("Session factory not set.")

        def operation() -> None:
            """Insert a new position row."""
            with session_scope(self._session_factory) as session:
                session.add(
                    Position(
                        entry_time=entry_time,
                        entry_price=entry_price,
                        quantity=quantity,
                        status="open",
                    )
                )
            logger.info("Stored new position in database at {}", entry_price)

        run_with_retry(operation)

    def _close_position(
        self,
        position_id: int,
        exit_time: datetime,
        exit_price: float,
        profit_usdt: float,
        profit_percent: float,
    ) -> None:
        """Close an open position and create a trade record."""
        if not self._session_factory:
            raise RuntimeError("Session factory not set.")

        def operation() -> None:
            """Close the position and create a trade row."""
            with session_scope(self._session_factory) as session:
                position = session.query(Position).filter(Position.id == position_id).one()
                position.status = "closed"
                duration = exit_time - position.entry_time
                duration_minutes = duration.total_seconds() / 60
                session.add(
                    Trade(
                        entry_time=position.entry_time,
                        exit_time=exit_time,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        profit_usdt=profit_usdt,
                        profit_percent=profit_percent,
                        duration_minutes=duration_minutes,
                    )
                )
            logger.info("Stored closed trade in database with profit {}", profit_usdt)

        run_with_retry(operation)

    def _get_cash_balance(self) -> float:
        """Calculate cash balance from initial balance and realized profits."""
        if not self._session_factory:
            raise RuntimeError("Session factory not set.")

        def operation() -> float:
            """Aggregate realized profits to compute cash balance."""
            with session_scope(self._session_factory) as session:
                total_profit = session.query(Trade).with_entities(Trade.profit_usdt).all()
                profit_sum = sum(item[0] for item in total_profit) if total_profit else 0.0
                return self._settings.initial_balance + profit_sum

        return run_with_retry(operation)

    def _ensure_initial_history_sync(self) -> None:
        """Create the initial portfolio history record."""
        if not self._session_factory:
            raise RuntimeError("Session factory not set.")

        def operation() -> None:
            """Insert initial portfolio history row."""
            with session_scope(self._session_factory) as session:
                exists = session.query(PortfolioHistory).first()
                if not exists:
                    session.add(
                        PortfolioHistory(
                            timestamp=datetime.now(timezone.utc),
                            balance=self._settings.initial_balance,
                        )
                    )

        run_with_retry(operation)

    def _record_portfolio_balance(self, balance: float) -> None:
        """Record a portfolio equity snapshot."""
        if not self._session_factory:
            raise RuntimeError("Session factory not set.")

        def operation() -> None:
            """Insert portfolio balance snapshot."""
            with session_scope(self._session_factory) as session:
                session.add(
                    PortfolioHistory(
                        timestamp=datetime.now(timezone.utc),
                        balance=balance,
                    )
                )
            logger.info("Recorded portfolio balance {}", balance)

        run_with_retry(operation)
