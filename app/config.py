"""Application configuration management — improved version."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    # Telegram
    api_id: int
    api_hash: str
    channel_name: str

    # Binance
    binance_api_key: str
    binance_api_secret: str
    binance_testnet: bool
    trading_symbol: str
    leverage: int

    # Risk management (NEW vs original)
    trade_duration_seconds: int      # was hardcoded 60
    stop_loss_pct: float             # e.g. 0.005 = 0.5% SL
    take_profit_pct: float           # e.g. 0.010 = 1.0% TP
    trailing_stop_pct: float         # e.g. 0.003, 0 = disabled
    max_concurrent_trades: int       # max open positions
    signal_cooldown_seconds: int     # min seconds between same-side signals
    daily_loss_limit_usdt: float     # emergency stop threshold
    maker_fee_pct: float             # default 0.0002
    taker_fee_pct: float             # default 0.0004

    # Infra
    database_url: str
    port: int
    data_dir: Path
    log_path: Path
    session_path: Path


def load_settings() -> Settings:
    load_dotenv()

    api_id_raw = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        raise ValueError("API_ID and API_HASH must be set in environment.")

    channel_name = os.getenv("CHANNEL_NAME", "intelligent_trading_signals").strip()
    if not channel_name.startswith("@"):
        channel_name = f"@{channel_name}"

    testnet_raw = os.getenv("BINANCE_TESTNET", "true").strip().lower()
    binance_testnet = testnet_raw == "true"
    override_flag = os.getenv("ALLOW_PRODUCTION", "false").strip().lower() == "true"
    if not binance_testnet and not override_flag:
        raise RuntimeError(
            "BINANCE_TESTNET=false but ALLOW_PRODUCTION is not set. "
            "Set ALLOW_PRODUCTION=true to enable live trading (DANGEROUS)."
        )

    return Settings(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        channel_name=channel_name,
        binance_api_key=os.getenv("BINANCE_API_KEY", "").strip(),
        binance_api_secret=os.getenv("BINANCE_API_SECRET", "").strip(),
        binance_testnet=binance_testnet,
        trading_symbol=os.getenv("TRADING_SYMBOL", "BTC/USDT").strip(),
        leverage=int(os.getenv("LEVERAGE", "10").strip()),
        # Risk management
        trade_duration_seconds=int(os.getenv("TRADE_DURATION_SECONDS", "60").strip()),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.005").strip()),
        take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.010").strip()),
        trailing_stop_pct=float(os.getenv("TRAILING_STOP_PCT", "0.003").strip()),
        max_concurrent_trades=int(os.getenv("MAX_CONCURRENT_TRADES", "3").strip()),
        signal_cooldown_seconds=int(os.getenv("SIGNAL_COOLDOWN_SECONDS", "30").strip()),
        daily_loss_limit_usdt=float(os.getenv("DAILY_LOSS_LIMIT_USDT", "50.0").strip()),
        maker_fee_pct=float(os.getenv("MAKER_FEE_PCT", "0.0002").strip()),
        taker_fee_pct=float(os.getenv("TAKER_FEE_PCT", "0.0004").strip()),
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/trades.db").strip(),
        port=int(os.getenv("PORT", "8000").strip()),
        data_dir=Path("data"),
        log_path=Path("logs") / "bot.log",
        session_path=Path("data") / "telegram.session",
    )
