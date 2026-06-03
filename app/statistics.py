"""Trade statistics computation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TradeStats:
    total_trades: int = 0
    long_trades: int = 0
    short_trades: int = 0
    strong_trades: int = 0
    normal_trades: int = 0
    weak_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    average_trade: float = 0.0
    average_long: float = 0.0
    average_short: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    total_pnl: float = 0.0
    roi_percent: float = 0.0
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)


def calculate_stats(trades: List[Any], initial_balance: float) -> TradeStats:
    closed = [t for t in trades if not t.is_open and t.pnl_usdt is not None]
    stats = TradeStats()
    stats.total_trades = len(closed)
    if not closed:
        return stats

    pnls = [t.pnl_usdt for t in closed]
    stats.total_pnl = sum(pnls)
    stats.winning_trades = sum(1 for p in pnls if p > 0)
    stats.losing_trades = sum(1 for p in pnls if p <= 0)
    stats.win_rate = (stats.winning_trades / stats.total_trades) * 100
    stats.average_trade = stats.total_pnl / stats.total_trades
    stats.best_trade = max(pnls)
    stats.worst_trade = min(pnls)
    stats.roi_percent = (stats.total_pnl / initial_balance) * 100 if initial_balance else 0.0

    long_pnls = [t.pnl_usdt for t in closed if t.side == "LONG"]
    short_pnls = [t.pnl_usdt for t in closed if t.side == "SHORT"]
    stats.long_trades = len(long_pnls)
    stats.short_trades = len(short_pnls)
    stats.average_long = sum(long_pnls) / len(long_pnls) if long_pnls else 0.0
    stats.average_short = sum(short_pnls) / len(short_pnls) if short_pnls else 0.0

    stats.strong_trades = sum(1 for t in closed if "STRONG" in t.signal_type)
    stats.weak_trades = sum(1 for t in closed if "weak" in t.signal_type)
    stats.normal_trades = stats.total_trades - stats.strong_trades - stats.weak_trades

    equity = initial_balance
    curve: List[Dict[str, Any]] = []
    for t in sorted(closed, key=lambda x: x.entry_time):
        equity += t.pnl_usdt
        curve.append({
            "time": t.exit_time.isoformat() if t.exit_time else "",
            "equity": round(equity, 4),
        })
    stats.equity_curve = curve
    return stats