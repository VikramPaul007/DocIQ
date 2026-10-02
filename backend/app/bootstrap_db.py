"""Idempotent creation of the dociq_app / dociq_worker database roles.

Must run before ``alembic upgrade head``: migration 0001 GRANTs table
privileges to these roles, which fails if they don't exist yet. Safe to
re-run — an existing role just gets its password refreshed from env.

Postgres's ``ALTER/CREATE ROLE ... PASSWORD`` and role-name positions are
parsed as literal/identifier tokens, not general expressions, so they can't be
passed as bound query parameters (unlike ordinary ``SET`` values). We use
``psycopg.sql`` composition instead of f-string interpolation so the password
and role name are still safely quoted/escaped rather than pasted into SQL text.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from app.config import settings


def _upsert_role(conn: psycopg.Connection, name: str, password: str, *, bypass_rls: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
        verb = "ALTER" if cur.fetchone() is not None else "CREATE"
        bypass_clause = "BYPASSRLS" if bypass_rls else "NOBYPASSRLS"
        cur.execute(
            sql.SQL("{verb} ROLE {name} WITH LOGIN PASSWORD {password} {bypass}").format(
                verb=sql.SQL(verb),  # nosec: fixed literal, not user input
                name=sql.Identifier(name),
                password=sql.Literal(password),
                bypass=sql.SQL(bypass_clause),  # nosec: fixed literal, not user input
            )
        )
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {db} TO {name}").format(
                db=sql.Identifier(settings.postgres_db), name=sql.Identifier(name)
            )
        )
        cur.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {name}").format(name=sql.Identifier(name))
        )


def bootstrap_roles() -> None:
    with psycopg.connect(settings.owner_conninfo, autocommit=True) as conn:
        _upsert_role(
            conn, settings.postgres_app_user, settings.postgres_app_password, bypass_rls=False
        )
        _upsert_role(
            conn, settings.postgres_worker_user, settings.postgres_worker_password, bypass_rls=True
        )
