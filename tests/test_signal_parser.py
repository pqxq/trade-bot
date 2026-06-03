"""Unit tests for signal parsing."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.signal_parser import parse_signal


@pytest.mark.parametrize(
    "message,expected_type,expected_price,expected_indicator",
    [
        ("₿ 72,980 Indicator: +0.49 ↑ BUY 1min", "BUY", 72980.0, 0.49),
        ("₿ 72,751 Indicator: +0.69 ↑ STRONG BUY 1min", "STRONG BUY", 72751.0, 0.69),
        ("₿ 74,008 Indicator: -0.35 ↑ weak sell 1min", "weak sell", 74008.0, -0.35),
        ("₿ 74,008 Indicator: -1.05 ↑ STRONG SELL 1min", "STRONG SELL", 74008.0, -1.05),
    ],
)
def test_parse_signal_valid(message: str, expected_type: str, expected_price: float, expected_indicator: float) -> None:
    """Ensure valid messages are parsed correctly."""
    timestamp = datetime(2024, 1, 1, 0, 0, 0)
    result = parse_signal(message, timestamp=timestamp)
    assert result is not None
    assert result.signal_type == expected_type
    assert result.telegram_price == expected_price
    assert result.indicator_value == expected_indicator
    assert result.timestamp == timestamp


@pytest.mark.parametrize(
    "message",
    [
        "Random message without signal",
        "₿ 72,980 Indicator: BUY 1min",
        "₿ 72,980 Indicator: +0.49 ↑ HOLD 1min",
    ],
)
def test_parse_signal_invalid(message: str) -> None:
    """Ensure invalid messages are ignored."""
    result = parse_signal(message)
    assert result is None
