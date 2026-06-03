"""Statistics and performance calculations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

import pandas as pd


@dataclass(frozen=True)
class TradeStats:
    """Aggregated trade statistics."""

    total_trades: int
    win_rate: float
    average_profit: float
    average_loss: float
    average_trade: float
    profit_factor: float
    maximum_drawdown: float
    largest_winner: float
    largest_loser: float
    current_equity: float
    roi_percent: float


def calculate_max_drawdown(equity_curve: Iterable[float]) -> float:
    """Calculate maximum drawdown as a percentage."""
    values = list(equity_curve)
    if not values:
        return 0.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak if peak else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown * 100


def calculate_trade_stats(
    trades: List[dict],
    equity_curve: Iterable[float],
    current_equity: float,
    initial_balance: float,
) -> TradeStats:
    """Calculate aggregated statistics from trade data."""
    if not trades:
        return TradeStats(
            total_trades=0,
            win_rate=0.0,
            average_profit=0.0,
            average_loss=0.0,
            average_trade=0.0,
            profit_factor=0.0,
            maximum_drawdown=calculate_max_drawdown(equity_curve),
            largest_winner=0.0,
            largest_loser=0.0,
            current_equity=current_equity,
            roi_percent=((current_equity - initial_balance) / initial_balance) * 100,
        )

    frame = pd.DataFrame(trades)
    profits = frame["profit_usdt"]
    wins = profits[profits > 0]
    losses = profits[profits < 0]

    win_rate = (len(wins) / len(profits)) * 100 if len(profits) else 0.0
    average_profit = wins.mean() if not wins.empty else 0.0
    average_loss = losses.mean() if not losses.empty else 0.0
    average_trade = profits.mean() if not profits.empty else 0.0
    profit_factor = wins.sum() / abs(losses.sum()) if not losses.empty else 0.0

    return TradeStats(
        total_trades=len(profits),
        win_rate=win_rate,
        average_profit=float(average_profit),
        average_loss=float(average_loss),
        average_trade=float(average_trade),
        profit_factor=float(profit_factor),
        maximum_drawdown=calculate_max_drawdown(equity_curve),
        largest_winner=float(profits.max()),
        largest_loser=float(profits.min()),
        current_equity=current_equity,
        roi_percent=((current_equity - initial_balance) / initial_balance) * 100,
    )

