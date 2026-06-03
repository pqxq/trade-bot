"""Parse trading signals from Telegram messages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from loguru import logger


@dataclass(frozen=True)
class SignalData:
    signal_type: str
    indicator_value: float
    telegram_price: float
    timestamp: datetime
    raw_message: str


_SIGNAL_REGEX = re.compile(
    r"₿\s*(?P<price>[\d,]+)\s*"
    r"Indicator:\s*(?P<indicator>[+-]?\d+(?:\.\d+)?)\s*"
    r"(?:↑|↗|↘)?\s*"
    r"(?P<signal>STRONG\s+BUY|BUY|weak\s+buy|STRONG\s+SELL|SELL|weak\s+sell)\b",
    re.IGNORECASE,
)

_SIGNAL_NORMALIZATION = {
    "strong buy": "STRONG BUY",
    "buy": "BUY",
    "weak buy": "weak buy",
    "strong sell": "STRONG SELL",
    "sell": "SELL",
    "weak sell": "weak sell",
}

# Signal → (side, risk_fraction)
SIGNAL_MAPPING: dict[str, tuple[str, float]] = {
    "STRONG BUY":  ("LONG",  1.00),
    "BUY":         ("LONG",  0.50),
    "weak buy":    ("LONG",  0.25),
    "STRONG SELL": ("SHORT", 1.00),
    "SELL":        ("SHORT", 0.50),
    "weak sell":   ("SHORT", 0.25),
}


def normalize_signal_type(signal_raw: str) -> Optional[str]:
    key = signal_raw.strip().lower()
    return _SIGNAL_NORMALIZATION.get(key)


def parse_signal(text: str, timestamp: datetime | None = None) -> SignalData | None:
    match = _SIGNAL_REGEX.search(text)
    if not match:
        return None

    signal_type = normalize_signal_type(match.group("signal"))
    if not signal_type:
        logger.warning("Unrecognized signal type in message: {}", text)
        return None

    price_raw = match.group("price").replace(",", "")
    indicator_raw = match.group("indicator")

    try:
        price = float(price_raw)
        indicator = float(indicator_raw)
    except ValueError:
        logger.warning("Failed to parse numeric values: {}", text)
        return None

    return SignalData(
        signal_type=signal_type,
        indicator_value=indicator,
        telegram_price=price,
        timestamp=timestamp or datetime.utcnow(),
        raw_message=text,
    )