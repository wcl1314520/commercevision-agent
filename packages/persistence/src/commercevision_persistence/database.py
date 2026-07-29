"""SQLAlchemy engine and session lifecycle."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from commercevision_contracts import Settings
from commercevision_contracts.config import WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

_unit_of_work_depth: ContextVar[int] = ContextVar("commercevision_uow_depth", default=0)


def sync_mysql_url(dsn: str) -> URL:
    url = make_url(dsn)
    if url.drivername in {"mysql", "mysql+aiomysql"}:
        return url.set(drivername="mysql+pymysql")
    if url.drivername != "mysql+pymysql":
        raise ValueError(f"unsupported MySQL driver for persistence: {url.drivername}")
    return url


@dataclass(frozen=True, slots=True)
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    def dispose(self) -> None:
        self.engine.dispose()


def create_database(settings: Settings) -> Database:
    engine = create_engine(
        sync_mysql_url(settings.mysql_dsn),
        pool_pre_ping=True,
        pool_recycle=settings.mysql_pool_recycle_seconds,
        pool_size=settings.mysql_pool_size,
        max_overflow=settings.mysql_max_overflow,
        isolation_level="READ COMMITTED",
        connect_args={"connect_timeout": settings.mysql_connect_timeout_seconds},
    )
    return Database(
        engine=engine,
        session_factory=sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
            class_=Session,
        ),
    )


def create_readiness_database(settings: Settings) -> Database:
    """Create the short-lived, socket-bounded Worker readiness database."""

    engine = create_engine(
        sync_mysql_url(settings.mysql_dsn),
        pool_pre_ping=False,
        pool_recycle=settings.mysql_pool_recycle_seconds,
        pool_size=1,
        max_overflow=0,
        isolation_level="AUTOCOMMIT",
        skip_autocommit_rollback=True,
        connect_args={
            "connect_timeout": settings.mysql_connect_timeout_seconds,
            "read_timeout": WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS,
            "write_timeout": WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS,
        },
    )
    return Database(
        engine=engine,
        session_factory=sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
            class_=Session,
        ),
    )


def is_unit_of_work_active() -> bool:
    return _unit_of_work_depth.get() > 0


def enter_unit_of_work() -> object:
    return _unit_of_work_depth.set(_unit_of_work_depth.get() + 1)


def exit_unit_of_work(token: object) -> None:
    _unit_of_work_depth.reset(token)
