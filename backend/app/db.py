"""Database engines and the FastAPI session dependency.

Two roles connect to the same database: the API uses ``dociq_app`` (subject to
row-level security), the worker uses ``dociq_worker`` (BYPASSRLS, see
alembic/versions for the role grants). Each gets its own engine/session
factory; migrations run separately as the owner role via Alembic.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def make_session_factory(dsn: str) -> tuple[sessionmaker[Session], Engine]:
    engine = create_engine(dsn, pool_pre_ping=True, pool_size=5, max_overflow=5)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False), engine


SessionLocal, engine = make_session_factory(settings.app_dsn)


def get_db() -> Generator[Session, None, None]:
    """One DB session per request, committed on success and rolled back on error.

    RLS context (set via ``set_rls_context``) is scoped to this transaction, so it
    must be applied *after* this session starts and *before* any workspace-scoped
    query runs.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_rls_context(db: Session, *, user_id: uuid.UUID | None, is_platform_admin: bool) -> None:
    """Scope row-level security to the current transaction.

    Uses ``set_config(..., true)`` (the "is_local" form) rather than string-built
    ``SET LOCAL`` so the values are bound parameters, not interpolated SQL.
    """
    db.execute(
        text(
            "SELECT set_config('app.user_id', :uid, true), "
            "set_config('app.is_platform_admin', :flag, true)"
        ),
        {
            "uid": str(user_id) if user_id else "",
            "flag": "on" if is_platform_admin else "off",
        },
    )
