"""Application configuration management."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    api_id: int
    api_hash: str
    channel_name: str
    initial_balance: float
    database_url: str
    port: int
    data_dir: Path
    exports_dir: Path
    log_path: Path
    session_path: Path


def load_settings() -> Settings:
    """Load configuration from environment variables and .env file."""
    load_dotenv()

    api_id_raw = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        raise ValueError("API_ID and API_HASH must be set in environment.")

    channel_name = os.getenv("CHANNEL_NAME", "intelligent_trading_signals").strip()
    if not channel_name.startswith("@"):
        channel_name = f"@{channel_name}"

    initial_balance = float(os.getenv("INITIAL_BALANCE", "100").strip())
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/trades.db").strip()
    port = int(os.getenv("PORT", "8000").strip())

    data_dir = Path("data")
    exports_dir = Path("exports")
    log_path = Path("logs") / "bot.log"
    session_path = data_dir / "telegram.session"

    return Settings(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        channel_name=channel_name,
        initial_balance=initial_balance,
        database_url=database_url,
        port=port,
        data_dir=data_dir,
        exports_dir=exports_dir,
        log_path=log_path,
        session_path=session_path,
    )

