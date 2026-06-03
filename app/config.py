"""Application configuration management."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    channel_name: str
    database_url: str
    port: int
    data_dir: Path
    log_path: Path
    session_path: Path
    binance_api_key: str
    binance_api_secret: str
    binance_testnet: bool
    trading_symbol: str
    leverage: int


def load_settings() -> Settings:
    load_dotenv()

    api_id_raw = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        raise ValueError("API_ID and API_HASH must be set in environment.")

    channel_name = os.getenv("CHANNEL_NAME", "intelligent_trading_signals").strip()
    if not channel_name.startswith("@"):
        channel_name = f"@{channel_name}"

    database_url = os.getenv("DATABASE_URL", "sqlite:///data/trades.db").strip()
    port = int(os.getenv("PORT", "8000").strip())

    data_dir = Path("data")
    log_path = Path("logs") / "bot.log"
    session_path = data_dir / "telegram.session"

    binance_api_key = os.getenv("BINANCE_API_KEY", "").strip()
    binance_api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    binance_testnet_raw = os.getenv("BINANCE_TESTNET", "true").strip().lower()
    binance_testnet = binance_testnet_raw == "true"
    trading_symbol = os.getenv("TRADING_SYMBOL", "BTC/USDT").strip()
    leverage = int(os.getenv("LEVERAGE", "10").strip())

    override_flag = os.getenv("ALLOW_PRODUCTION", "false").strip().lower() == "true"
    if not binance_testnet and not override_flag:
        raise RuntimeError(
            "BINANCE_TESTNET is set to false. "
            "The bot only runs on Testnet. "
            "Set ALLOW_PRODUCTION=true to override (DANGEROUS)."
        )

    return Settings(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        channel_name=channel_name,
        database_url=database_url,
        port=port,
        data_dir=data_dir,
        log_path=log_path,
        session_path=session_path,
        binance_api_key=binance_api_key,
        binance_api_secret=binance_api_secret,
        binance_testnet=binance_testnet,
        trading_symbol=trading_symbol,
        leverage=leverage,
    )