"""Database engine, session handling, and initialization."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Generator, TypeVar

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import Settings

Base = declarative_base()
T = TypeVar("T")


def create_db_engine(settings: Settings) -> Engine:
    """Create the SQLAlchemy engine."""
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        future=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )


def init_db(engine: Engine) -> None:
    """Create all database tables."""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_with_retry(operation: Callable[[], T], retries: int = 3, delay: float = 1.0) -> T:
    """Run a database operation with retries for transient errors."""
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            return operation()
        except OperationalError as exc:
            last_error = exc
            time.sleep(delay)
    if last_error:
        raise last_error
    raise RuntimeError("Database operation failed without exception.")

