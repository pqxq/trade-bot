"""Application entrypoint."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app import dashboard
from app.config import Settings, load_settings
from app.database import create_db_engine, create_session_factory, init_db
from app.exchange import BinanceFuturesExchange
from app.trader import FuturesTrader
from app.scheduler import RuntimeState, Scheduler
from app.telegram_listener import TelegramListener


@dataclass
class AppContext:
    settings: Settings
    exchange: BinanceFuturesExchange
    trader: FuturesTrader
    listener: TelegramListener
    scheduler: Scheduler
    state: RuntimeState


def configure_logging(settings: Settings) -> None:
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stdout, level="INFO", enqueue=True, backtrace=True, diagnose=False,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")
    logger.add(settings.log_path, rotation="10 MB", retention="14 days",
        level="DEBUG", enqueue=True, backtrace=True, diagnose=False)


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(settings)
    logger.info("Starting Binance Futures Testnet Trading Platform")
    logger.info("Symbol={} Leverage={} Testnet={}", settings.trading_symbol, settings.leverage, settings.binance_testnet)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    init_db(engine)

    exchange = BinanceFuturesExchange(settings)
    trader = FuturesTrader(settings, exchange)
    trader.set_session_factory(session_factory)

    state = RuntimeState()
    listener = TelegramListener(settings, trader, state)
    scheduler = Scheduler(trader)

    app = FastAPI(title="Binance Futures Testnet Trader")
    app.state.context = AppContext(
        settings=settings, exchange=exchange, trader=trader,
        listener=listener, scheduler=scheduler, state=state,
    )

    dashboard.router.session_factory = session_factory
    app.include_router(dashboard.router)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info("Initializing exchange connection…")
        await exchange.initialize()
        await listener.start()
        await scheduler.start()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        logger.info("Graceful shutdown…")
        await scheduler.stop()
        await listener.stop()
        await exchange.close()

    return app


app = create_app()