"""Database engine, sessions and connectivity check."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})


@lru_cache
def _factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any error."""
    s: Session = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def ping() -> tuple[bool, str]:
    """Return (reachable, detail). Never raises: health checks must not crash the API."""
    try:
        with get_engine().connect() as conn:
            version = conn.execute(text("select current_setting('server_version')")).scalar_one()
        return True, f"PostgreSQL {version}"
    except Exception as exc:  # noqa: BLE001
        return False, exc.__class__.__name__
