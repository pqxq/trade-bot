"""Binance Futures live trading engine.

Improvements over original pqxq/trade-bot:
  1. Configurable trade duration (was hardcoded 60s)
  2. Stop-loss (SL) and take-profit (TP) per trade
  3. Trailing stop-loss support
  4. Signal cooldown per side (prevents spam entries)
  5. Max concurrent positions guard
  6. Daily loss limit emergency stop
  7. Fee-aware PnL calculation (open + close taker fees)
  8. close_reason saved to DB (SL / TP / TIME)          ← fixed
  9. Orphaned trade recovery on startup                  ← new
 10. Shared price cache — one HTTP call/s max            ← new
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, date
from typing import Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import run_with_retry, session_scope
from app.exchange import BinanceFuturesExchange
from app.models import Signal, Trade
from app.signal_parser import SIGNAL_MAPPING, SignalData

# Shared price cache — updated once per second by a background task.
# All monitor coroutines read from here instead of each making their own request.
_price_cache: Dict[str, float] = {}
_price_cache_lock = asyncio.Lock()


class FuturesTrader:

    def __init__(self, settings: Settings, exchange: BinanceFuturesExchange) -> None:
        self._settings = settings
        self._exchange = exchange
        self._session_factory: Optional[sessionmaker[Session]] = None

        # In-memory guards
        self._last_signal_time: Dict[str, datetime] = {}
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: date = date.today()
        self._price_updater_task: Optional[asyncio.Task] = None

    def set_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # ── Startup / Shutdown ────────────────────────────────────────────

    async def start(self) -> None:
        """Start background tasks and recover any orphaned open trades."""
        self._price_updater_task = asyncio.create_task(
            self._price_updater_loop(), name="price-updater"
        )
        await self._restore_open_trades()

    async def stop(self) -> None:
        if self._price_updater_task:
            self._price_updater_task.cancel()
            try:
                await self._price_updater_task
            except asyncio.CancelledError:
                pass

    async def _price_updater_loop(self) -> None:
        """Refresh shared price cache once per second."""
        symbol = self._settings.trading_symbol
        while True:
            try:
                price = await self._exchange.get_price(symbol)
                async with _price_cache_lock:
                    _price_cache[symbol] = price
            except Exception as exc:
                logger.debug("Price updater error: {}", exc)
            await asyncio.sleep(1)

    async def _get_cached_price(self) -> float:
        """Return last cached price (or fetch directly if cache is empty)."""
        symbol = self._settings.trading_symbol
        async with _price_cache_lock:
            cached = _price_cache.get(symbol)
        if cached is not None:
            return cached
        return await self._exchange.get_price(symbol)

    async def _restore_open_trades(self) -> None:
        """On startup, re-attach monitor tasks for any trades still marked is_open=True.

        This handles the case where the bot crashed / restarted while a trade
        was open — without this the trade would never be closed.
        """
        open_trades = await self.get_open_trades()
        if not open_trades:
            return
        logger.warning(
            "Found {} orphaned open trade(s) in DB — restoring monitors.",
            len(open_trades),
        )
        now = datetime.now(timezone.utc)
        for trade in open_trades:
            entry_tz = (
                trade.entry_time.replace(tzinfo=timezone.utc)
                if trade.entry_time.tzinfo is None
                else trade.entry_time
            )
            elapsed = (now - entry_tz).total_seconds()
            remaining = max(0, self._settings.trade_duration_seconds - int(elapsed))

            # Rebuild SL/TP from stored values (or recalculate if missing)
            sl = trade.sl_price
            tp = trade.tp_price
            if sl is None or tp is None:
                sl, tp = self._compute_sl_tp(trade.entry_price, trade.side)

            logger.info(
                "Restoring monitor for trade #{} | {} | elapsed={}s remaining={}s SL={} TP={}",
                trade.id, trade.side, int(elapsed), remaining, sl, tp,
            )
            asyncio.create_task(
                self._monitor_trade(
                    trade.id, trade.entry_price, trade.side, sl, tp,
                    override_deadline=remaining,
                ),
                name=f"monitor-{trade.id}",
            )

    # ── Public API ────────────────────────────────────────────────────

    async def handle_signal(self, signal: SignalData) -> None:
        logger.info("Signal received: {} price={}", signal.signal_type, signal.telegram_price)
        await asyncio.to_thread(self._store_signal, signal)

        if signal.signal_type not in SIGNAL_MAPPING:
            logger.info("Signal '{}' not in SIGNAL_MAPPING — skipping.", signal.signal_type)
            return

        # Guard 1: daily loss emergency stop
        self._refresh_daily_pnl()
        if self._daily_pnl <= -abs(self._settings.daily_loss_limit_usdt):
            logger.warning(
                "EMERGENCY STOP — daily loss {:.2f} USDT reached limit {:.2f} USDT. "
                "No new trades until tomorrow.",
                self._daily_pnl, self._settings.daily_loss_limit_usdt,
            )
            return

        # Guard 2: concurrent position cap
        open_trades = await self.get_open_trades()
        if len(open_trades) >= self._settings.max_concurrent_trades:
            logger.info(
                "Max concurrent trades ({}) reached — skipping.",
                self._settings.max_concurrent_trades,
            )
            return

        # Guard 3: cooldown per side
        side_label, _ = SIGNAL_MAPPING[signal.signal_type]
        now = datetime.now(timezone.utc)
        last = self._last_signal_time.get(side_label)
        if last:
            elapsed = (now - last).total_seconds()
            if elapsed < self._settings.signal_cooldown_seconds:
                logger.info(
                    "Cooldown active for {} ({:.0f}s / {}s) — skipping.",
                    side_label, elapsed, self._settings.signal_cooldown_seconds,
                )
                return

        self._last_signal_time[side_label] = now
        await self._open_trade(signal)

    async def get_futures_balance(self) -> float:
        return await self._exchange.get_futures_balance()

    async def get_open_trades(self) -> List[Trade]:
        return await asyncio.to_thread(self._fetch_open_trades)

    async def get_all_trades(self) -> List[Trade]:
        return await asyncio.to_thread(self._fetch_all_trades)

    # ── Trade lifecycle ───────────────────────────────────────────────

    async def _open_trade(self, signal: SignalData) -> None:
        side_label, risk_fraction = SIGNAL_MAPPING[signal.signal_type]

        try:
            balance = await self._exchange.get_futures_balance()
            current_price = await self._get_cached_price()
        except Exception as exc:
            logger.error("Pre-trade data fetch failed: {}", exc)
            return

        leverage = self._settings.leverage
        notional = balance * risk_fraction * leverage
        raw_qty = notional / current_price
        quantity = self._exchange.round_quantity(self._settings.trading_symbol, raw_qty)

        if quantity <= 0:
            logger.warning(
                "Quantity=0 (balance={:.2f} risk={} lev={} price={}) — skipping.",
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

        sl_price, tp_price = self._compute_sl_tp(filled_price, side_label)

        trade = await asyncio.to_thread(
            self._create_trade,
            signal, side_label, quantity, filled_price,
            entry_time, binance_order_id, sl_price, tp_price,
        )

        logger.info(
            "Trade #{} OPEN | {} {} qty={} entry={} SL={} TP={} order_id={}",
            trade.id, side_label, signal.signal_type,
            quantity, filled_price, sl_price, tp_price, binance_order_id,
        )

        asyncio.create_task(
            self._monitor_trade(trade.id, filled_price, side_label, sl_price, tp_price),
            name=f"monitor-{trade.id}",
        )

    def _compute_sl_tp(self, entry_price: float, side: str) -> Tuple[float, float]:
        sl_pct = self._settings.stop_loss_pct
        tp_pct = self._settings.take_profit_pct
        if side == "LONG":
            sl = entry_price * (1.0 - sl_pct)
            tp = entry_price * (1.0 + tp_pct)
        else:
            sl = entry_price * (1.0 + sl_pct)
            tp = entry_price * (1.0 - tp_pct)
        return round(sl, 2), round(tp, 2)

    async def _monitor_trade(
        self,
        trade_id: int,
        entry_price: float,
        side: str,
        sl_price: float,
        tp_price: float,
        override_deadline: Optional[int] = None,
    ) -> None:
        """Watch price every second; close on SL / TP / trailing stop / time limit."""
        deadline = override_deadline if override_deadline is not None \
            else self._settings.trade_duration_seconds
        trailing_pct = self._settings.trailing_stop_pct

        best_price = entry_price
        elapsed = 0

        while elapsed < deadline:
            await asyncio.sleep(1)
            elapsed += 1

            trade = await asyncio.to_thread(self._fetch_trade_by_id, trade_id)
            if trade is None or not trade.is_open:
                return

            try:
                price = await self._get_cached_price()
            except Exception:
                continue

            # Update trailing stop
            if trailing_pct > 0:
                if side == "LONG" and price > best_price:
                    best_price = price
                    sl_price = round(best_price * (1.0 - trailing_pct), 2)
                    logger.debug("Trailing SL -> {} for trade #{}", sl_price, trade_id)
                elif side == "SHORT" and price < best_price:
                    best_price = price
                    sl_price = round(best_price * (1.0 + trailing_pct), 2)
                    logger.debug("Trailing SL -> {} for trade #{}", sl_price, trade_id)

            sl_hit = (side == "LONG" and price <= sl_price) or \
                     (side == "SHORT" and price >= sl_price)
            tp_hit = (side == "LONG" and price >= tp_price) or \
                     (side == "SHORT" and price <= tp_price)

            if sl_hit:
                logger.warning("Trade #{} SL HIT @ {} (SL={})", trade_id, price, sl_price)
                await self._close_trade(trade_id, reason="SL")
                return
            if tp_hit:
                logger.info("Trade #{} TP HIT @ {} (TP={})", trade_id, price, tp_price)
                await self._close_trade(trade_id, reason="TP")
                return

        await self._close_trade(trade_id, reason="TIME")

    async def _close_trade(self, trade_id: int, reason: str = "TIME") -> None:
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
                exit_price = await self._get_cached_price()
            except Exception:
                exit_price = trade.entry_price

        entry_tz = (
            trade.entry_time.replace(tzinfo=timezone.utc)
            if trade.entry_time.tzinfo is None
            else trade.entry_time
        )
        duration_seconds = (exit_time - entry_tz).total_seconds()
        notional_entry = trade.entry_price * trade.quantity

        if trade.side == "LONG":
            gross_pnl = (exit_price - trade.entry_price) * trade.quantity * trade.leverage
        else:
            gross_pnl = (trade.entry_price - exit_price) * trade.quantity * trade.leverage

        fee_pct = self._settings.taker_fee_pct
        fees = notional_entry * fee_pct * 2
        pnl_usdt = gross_pnl - fees
        pnl_percent = (pnl_usdt / notional_entry * 100.0) if notional_entry else 0.0

        self._daily_pnl += pnl_usdt

        await asyncio.to_thread(
            self._update_trade_close,
            trade_id, exit_price, exit_time, duration_seconds,
            pnl_usdt, pnl_percent, reason,
        )

        logger.info(
            "Trade #{} CLOSED ({}) | exit={} pnl={:.4f} USDT ({:.2f}%) dur={}s | daily_pnl={:.2f}",
            trade_id, reason, exit_price, pnl_usdt, pnl_percent,
            int(duration_seconds), self._daily_pnl,
        )

    # ── DB helpers ────────────────────────────────────────────────────

    def _refresh_daily_pnl(self) -> None:
        today = date.today()
        if today != self._daily_pnl_date:
            logger.info("New trading day — resetting daily PnL (was {:.2f} USDT).", self._daily_pnl)
            self._daily_pnl = 0.0
            self._daily_pnl_date = today

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
        self,
        signal: SignalData,
        side: str,
        quantity: float,
        entry_price: float,
        entry_time: datetime,
        binance_order_id: str,
        sl_price: float,
        tp_price: float,
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
                    sl_price=sl_price,
                    tp_price=tp_price,
                    is_open=True,
                )
                s.add(trade)
                s.flush()
                s.expunge(trade)
                return trade
        return run_with_retry(op)

    def _update_trade_close(
        self,
        trade_id: int,
        exit_price: float,
        exit_time: datetime,
        duration_seconds: float,
        pnl_usdt: float,
        pnl_percent: float,
        close_reason: str,
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
                    t.close_reason = close_reason
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
