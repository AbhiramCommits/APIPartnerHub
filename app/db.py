"""SQLAlchemy engine, session factory and FastAPI session dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

settings = get_settings()

_connect_args: dict[str, object] = {}
_engine_kwargs: dict[str, object] = {"pool_pre_ping": True, "future": True}

if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    if ":memory:" in settings.DATABASE_URL:
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.DATABASE_URL, connect_args=_connect_args, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a scoped SQLAlchemy session."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create all tables. Safe to call repeatedly."""

    from app import models  # noqa: F401  (register mappers before create_all)

    Base.metadata.create_all(bind=engine)
