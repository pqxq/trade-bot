"""Application entrypoint for FastAPI and background services."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app import dashboard
from app.config import Settings, load_settings
from app.database import create_db_engine, create_session_factory, init_db
from app.exchange import BinanceExchange
from app.paper_trader import PaperTrader
from app.scheduler import RuntimeState, Scheduler
from app.telegram_listener import TelegramListener


@dataclass
class AppContext:
    """Container for application services."""

    settings: Settings
    exchange: BinanceExchange
    trader: PaperTrader
    listener: TelegramListener
    scheduler: Scheduler
    state: RuntimeState


def configure_logging(settings: Settings) -> None:
    """Configure loguru logging."""
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    logger.add(
        settings.log_path,
        rotation="10 MB",
        retention="14 days",
        level="INFO",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = load_settings()
    configure_logging(settings)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    init_db(engine)

    exchange = BinanceExchange()
    trader = PaperTrader(settings, exchange)
    trader.set_session_factory(session_factory)

    state = RuntimeState()
    listener = TelegramListener(settings, trader, state)
    scheduler = Scheduler(trader)

    app = FastAPI()
    app.state.context = AppContext(
        settings=settings,
        exchange=exchange,
        trader=trader,
        listener=listener,
        scheduler=scheduler,
        state=state,
    )

    dashboard.router.session_factory = session_factory
    app.include_router(dashboard.router)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.on_event("startup")
    async def on_startup() -> None:
        """Start background services on app startup."""
        logger.info("Starting trading platform")
        await trader.ensure_initial_history()
        await listener.start()
        await scheduler.start()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        """Shutdown background services on app stop."""
        logger.info("Shutting down trading platform")
        await scheduler.stop()
        await listener.stop()
        await exchange.close()

    return app


app = create_app()
