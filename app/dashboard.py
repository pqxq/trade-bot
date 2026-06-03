"""FastAPI routes for the web dashboard."""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
import aiofiles
from sqlalchemy.orm import Session, sessionmaker

from app.database import run_with_retry, session_scope
from app.models import PortfolioHistory, Position, Signal, Trade
from app.statistics import calculate_trade_stats

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_context(request: Request) -> Any:
    """Get application context from the request."""
    return request.app.state.context


def _fetch_trades(sort_column: str, sort_order: str, offset: int, limit: int) -> Tuple[List[Trade], int]:
    """Fetch trades with pagination and sorting."""
    def operation() -> Tuple[List[Trade], int]:
        """Run the trades query with sorting and pagination."""
        with session_scope(_get_session_factory()) as session:
            query = session.query(Trade)
            total = query.count()
            column = getattr(Trade, sort_column)
            if sort_order == "desc":
                query = query.order_by(column.desc())
            else:
                query = query.order_by(column.asc())
            items = query.offset(offset).limit(limit).all()
            return items, total

    return run_with_retry(operation)


def _fetch_all_trades() -> List[Trade]:
    """Fetch all trades."""
    def operation() -> List[Trade]:
        """Run the trades query."""
        with session_scope(_get_session_factory()) as session:
            return session.query(Trade).order_by(Trade.entry_time.asc()).all()

    return run_with_retry(operation)


def _fetch_open_position() -> Position | None:
    """Fetch the current open position."""
    def operation() -> Position | None:
        """Run the open position query."""
        with session_scope(_get_session_factory()) as session:
            return session.query(Position).filter(Position.status == "open").first()

    return run_with_retry(operation)


def _fetch_last_signal() -> Signal | None:
    """Fetch the most recent signal."""
    def operation() -> Signal | None:
        """Run the last signal query."""
        with session_scope(_get_session_factory()) as session:
            return session.query(Signal).order_by(Signal.timestamp.desc()).first()

    return run_with_retry(operation)


def _fetch_equity_curve(limit: int | None = None) -> List[PortfolioHistory]:
    """Fetch the portfolio equity curve history."""
    def operation() -> List[PortfolioHistory]:
        """Run the portfolio history query."""
        with session_scope(_get_session_factory()) as session:
            query = session.query(PortfolioHistory).order_by(PortfolioHistory.timestamp.asc())
            if limit:
                query = query.limit(limit)
            return query.all()

    return run_with_retry(operation)


def _get_session_factory() -> sessionmaker[Session]:
    """Get the configured SQLAlchemy session factory."""
    return router.session_factory


def _trade_to_dict(trade: Trade) -> Dict[str, Any]:
    """Convert a Trade model to a template-ready dictionary."""
    return {
        "entry_time": trade.entry_time,
        "exit_time": trade.exit_time,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "quantity": trade.quantity,
        "profit_usdt": trade.profit_usdt,
        "profit_percent": trade.profit_percent,
        "duration_minutes": trade.duration_minutes,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the dashboard home page."""
    context = _get_context(request)
    state = context.state

    open_position = await asyncio.to_thread(_fetch_open_position)
    last_signal = await asyncio.to_thread(_fetch_last_signal)
    trades = await asyncio.to_thread(_fetch_all_trades)
    trades_dicts = [_trade_to_dict(trade) for trade in trades]

    total_profit = sum(t.profit_usdt for t in trades)
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.profit_usdt > 0)
    losing_trades = sum(1 for t in trades if t.profit_usdt < 0)
    win_rate = (winning_trades / total_trades) * 100 if total_trades else 0.0
    current_balance = await context.trader.get_current_equity()
    total_profit_percent = (
        (total_profit / context.settings.initial_balance) * 100
        if context.settings.initial_balance
        else 0.0
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "current_balance": current_balance,
            "total_profit": total_profit,
            "total_profit_percent": total_profit_percent,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "open_position": open_position,
            "last_signal": last_signal,
            "bot_status": state.bot_status,
            "trades": trades_dicts,
        },
    )


@router.get("/trades", response_class=HTMLResponse)
async def trades_page(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    sort: str = Query("entry_time"),
    order: str = Query("desc"),
) -> HTMLResponse:
    """Render the trade history page."""
    allowed_sort = {"entry_time", "exit_time", "profit_usdt", "profit_percent"}
    sort_column = sort if sort in allowed_sort else "entry_time"
    sort_order = "desc" if order == "desc" else "asc"
    offset = (page - 1) * per_page

    trades, total = await asyncio.to_thread(
        _fetch_trades,
        sort_column,
        sort_order,
        offset,
        per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(
        request=request,
        name="trades.html",
        context={
            "trades": [_trade_to_dict(t) for t in trades],
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "sort": sort_column,
            "order": sort_order,
        },
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request) -> HTMLResponse:
    """Render the statistics page."""
    context = _get_context(request)
    trades = await asyncio.to_thread(_fetch_all_trades)
    trades_dicts = [_trade_to_dict(trade) for trade in trades]
    equity_curve = await asyncio.to_thread(_fetch_equity_curve)
    equity_values = [item.balance for item in equity_curve]
    current_equity = await context.trader.get_current_equity()

    stats = calculate_trade_stats(
        trades=trades_dicts,
        equity_curve=equity_values,
        current_equity=current_equity,
        initial_balance=context.settings.initial_balance,
    )

    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={
            "stats": stats,
        },
    )


@router.get("/api/equity")
async def equity_curve() -> JSONResponse:
    """Return equity curve data for chart updates."""
    history = await asyncio.to_thread(_fetch_equity_curve)
    payload = [
        {"timestamp": item.timestamp.isoformat(), "balance": item.balance}
        for item in history
    ]
    return JSONResponse(payload)


@router.get("/export")
async def export_trades(request: Request) -> FileResponse:
    """Export all trades to a CSV file."""
    context = _get_context(request)
    trades = await asyncio.to_thread(_fetch_all_trades)
    exports_dir: Path = context.settings.exports_dir
    exports_dir.mkdir(parents=True, exist_ok=True)

    filename = f"trades_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    file_path = exports_dir / filename

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "quantity",
            "profit_usdt",
            "profit_percent",
            "duration_minutes",
        ]
    )
    for trade in trades:
        writer.writerow(
            [
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat(),
                trade.entry_price,
                trade.exit_price,
                trade.quantity,
                trade.profit_usdt,
                trade.profit_percent,
                trade.duration_minutes,
            ]
        )

    async with aiofiles.open(file_path, "w", encoding="utf-8") as handle:
        await handle.write(buffer.getvalue())
    logger.info("Exported trades to {}", file_path)

    return FileResponse(
        path=file_path,
        media_type="text/csv",
        filename=filename,
    )


@router.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse({"status": "ok"})
