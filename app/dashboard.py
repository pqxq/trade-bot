"""FastAPI routes for the web dashboard.

Fixes vs previous version:
  1. _trade_dict: remaining_seconds now uses settings.trade_duration_seconds
     (was hardcoded to 60.0)
  2. /api/live-trades: uses shared price cache via trader._get_cached_price()
     instead of exchange.get_price() per trade
  3. /stats and /api/stats: pass current_price to calculate_stats()
     so unrealized_pnl is populated
  4. _trade_dict: exposes sl_price, tp_price and close_reason fields
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from app.database import run_with_retry, session_scope
from app.models import Signal, Trade
from app.statistics import calculate_stats

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sf() -> sessionmaker[Session]:
    return router.session_factory  # type: ignore[attr-defined]


def _fetch_open_trades() -> List[Trade]:
    def op() -> List[Trade]:
        with session_scope(_sf()) as s:
            return (
                s.query(Trade)
                .filter(Trade.is_open == True)  # noqa: E712
                .order_by(Trade.entry_time.desc())
                .all()
            )
    return run_with_retry(op)


def _fetch_all_trades() -> List[Trade]:
    def op() -> List[Trade]:
        with session_scope(_sf()) as s:
            return s.query(Trade).order_by(Trade.entry_time.desc()).all()
    return run_with_retry(op)


def _fetch_closed_trades(
    offset: int, limit: int, sort_col: str, sort_order: str
) -> tuple[List[Trade], int]:
    def op() -> tuple[List[Trade], int]:
        with session_scope(_sf()) as s:
            q = s.query(Trade).filter(Trade.is_open == False)  # noqa: E712
            total = q.count()
            col = getattr(Trade, sort_col, Trade.entry_time)
            q = q.order_by(col.desc() if sort_order == "desc" else col.asc())
            return q.offset(offset).limit(limit).all(), total
    return run_with_retry(op)


def _fetch_last_signal() -> Signal | None:
    def op() -> Signal | None:
        with session_scope(_sf()) as s:
            return s.query(Signal).order_by(Signal.timestamp.desc()).first()
    return run_with_retry(op)


def _trade_dict(trade: Trade, duration_seconds: int = 60) -> Dict[str, Any]:
    """Serialise a Trade to a plain dict.

    Args:
        trade: ORM object.
        duration_seconds: configured TRADE_DURATION_SECONDS — used to compute
            the countdown timer correctly (fix for hardcoded 60s bug).
    """
    now = datetime.now(timezone.utc)
    entry = (
        trade.entry_time.replace(tzinfo=timezone.utc)
        if trade.entry_time.tzinfo is None
        else trade.entry_time
    )
    elapsed = (now - entry).total_seconds()
    # BUG FIX #1: was hardcoded 60.0 — now uses the configured duration
    remaining = max(0.0, duration_seconds - elapsed)
    return {
        "id": trade.id,
        "signal_type": trade.signal_type,
        "side": trade.side,
        "quantity": trade.quantity,
        "leverage": trade.leverage,
        "entry_price": trade.entry_price,
        "sl_price": trade.sl_price,
        "tp_price": trade.tp_price,
        "entry_time": entry.isoformat(),
        "exit_price": trade.exit_price,
        "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
        "duration_seconds": trade.duration_seconds,
        "pnl_usdt": trade.pnl_usdt,
        "pnl_percent": trade.pnl_percent,
        "close_reason": getattr(trade, "close_reason", None),
        "binance_order_id": trade.binance_order_id,
        "is_open": trade.is_open,
        "remaining_seconds": round(remaining, 1),
    }


# ── Pages ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    ctx = request.app.state.context
    duration = ctx.settings.trade_duration_seconds
    all_trades = await asyncio.to_thread(_fetch_all_trades)
    open_trades = [t for t in all_trades if t.is_open]
    closed = [t for t in all_trades if not t.is_open and t.pnl_usdt is not None]
    total_pnl = sum(t.pnl_usdt for t in closed)
    total_trades = len(closed)
    winning = sum(1 for t in closed if t.pnl_usdt > 0)
    losing = sum(1 for t in closed if t.pnl_usdt <= 0)
    win_rate = (winning / total_trades * 100) if total_trades else 0.0
    avg_trade = (total_pnl / total_trades) if total_trades else 0.0
    last_signal = await asyncio.to_thread(_fetch_last_signal)
    try:
        balance = await ctx.trader.get_futures_balance()
    except Exception:
        balance = 0.0
    roi = (total_pnl / balance * 100) if balance else 0.0
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "balance": balance,
            "total_pnl": total_pnl,
            "roi": roi,
            "total_trades": total_trades,
            "winning_trades": winning,
            "losing_trades": losing,
            "win_rate": win_rate,
            "avg_trade": avg_trade,
            "open_trades_count": len(open_trades),
            "last_signal": last_signal,
            "bot_status": ctx.state.bot_status,
            "open_trades": [_trade_dict(t, duration) for t in open_trades],
        },
    )


@router.get("/live", response_class=HTMLResponse)
async def live_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="live.html", context={})


@router.get("/trades", response_class=HTMLResponse)
async def trades_page(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    sort: str = Query("entry_time"),
    order: str = Query("desc"),
) -> HTMLResponse:
    ctx = request.app.state.context
    duration = ctx.settings.trade_duration_seconds
    allowed = {"entry_time", "exit_time", "pnl_usdt", "pnl_percent", "side", "signal_type"}
    sort_col = sort if sort in allowed else "entry_time"
    sort_order = "desc" if order == "desc" else "asc"
    offset = (page - 1) * per_page
    trades, total = await asyncio.to_thread(
        _fetch_closed_trades, offset, per_page, sort_col, sort_order
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        request=request,
        name="trades.html",
        context={
            "trades": [_trade_dict(t, duration) for t in trades],
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "sort": sort_col,
            "order": sort_order,
        },
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request) -> HTMLResponse:
    ctx = request.app.state.context
    all_trades = await asyncio.to_thread(_fetch_all_trades)
    try:
        initial = await ctx.trader.get_futures_balance()
    except Exception:
        initial = 100.0
    # BUG FIX #3: pass current_price so unrealized_pnl is computed
    try:
        current_price = await ctx.trader._get_cached_price()
    except Exception:
        current_price = 0.0
    stats = calculate_stats(all_trades, initial_balance=initial, current_price=current_price)
    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={"stats": stats},
    )


# ── API endpoints ────────────────────────────────────────────────────────────

@router.get("/api/live-trades")
async def api_live_trades(request: Request) -> JSONResponse:
    ctx = request.app.state.context
    duration = ctx.settings.trade_duration_seconds
    open_trades = await asyncio.to_thread(_fetch_open_trades)
    result: List[Dict[str, Any]] = []

    # BUG FIX #2: fetch price ONCE from shared cache, not once per trade
    try:
        price = await ctx.trader._get_cached_price()
    except Exception:
        price = None

    for t in open_trades:
        d = _trade_dict(t, duration)
        if price is not None:
            pnl = (
                (price - t.entry_price)
                if t.side == "LONG"
                else (t.entry_price - price)
            ) * t.quantity * t.leverage
            d["current_price"] = price
            d["current_pnl"] = round(pnl, 4)
        else:
            d["current_price"] = None
            d["current_pnl"] = None
        result.append(d)
    return JSONResponse(result)


@router.get("/api/stats")
async def api_stats(request: Request) -> JSONResponse:
    ctx = request.app.state.context
    all_trades = await asyncio.to_thread(_fetch_all_trades)
    try:
        initial = await ctx.trader.get_futures_balance()
    except Exception:
        initial = 100.0
    # BUG FIX #3: pass current_price so unrealized_pnl is populated
    try:
        current_price = await ctx.trader._get_cached_price()
    except Exception:
        current_price = 0.0
    stats = calculate_stats(all_trades, initial, current_price=current_price)
    return JSONResponse(stats.__dict__)


@router.get("/api/balance")
async def api_balance(request: Request) -> JSONResponse:
    ctx = request.app.state.context
    try:
        bal = await ctx.trader.get_futures_balance()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"balance": bal})
