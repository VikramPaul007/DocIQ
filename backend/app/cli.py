"""Operator CLI.

Run inside the api container, e.g.:
    docker compose exec api python -m app.cli migrate
    docker compose exec -it api python -m app.cli create-admin
"""

from __future__ import annotations

import argparse
import getpass
import sys

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.bootstrap_db import bootstrap_roles
from app.config import settings
from app.models import User
from app.security import WeakPasswordError, hash_password, validate_password_policy


def cmd_migrate(_args: argparse.Namespace) -> None:
    from alembic.config import Config

    from alembic import command

    bootstrap_roles()
    command.upgrade(Config("alembic.ini"), "head")
    print("Migration complete.")


def cmd_create_admin(_args: argparse.Namespace) -> None:
    email = input("Admin email: ").strip()
    try:
        # Same validator the API's EmailStr fields use (email-validator), so
        # an admin created here is never rejected the first time they try to
        # log in through the API.
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        print(f"Invalid email: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)
    try:
        validate_password_policy(password)
    except WeakPasswordError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    # Owner role: bypasses RLS as table owner, no app.user_id context needed.
    engine = create_engine(settings.owner_dsn)
    with Session(engine) as db:
        if db.query(User).filter(User.email == email).first() is not None:
            print(f"A user with email {email} already exists.", file=sys.stderr)
            raise SystemExit(1)
        db.add(User(email=email, password_hash=hash_password(password), is_platform_admin=True))
        db.commit()
    print(f"Platform admin {email} created.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "migrate", help="Bootstrap DB roles and run Alembic migrations to head"
    ).set_defaults(func=cmd_migrate)
    sub.add_parser(
        "create-admin", help="Interactively create the first platform admin"
    ).set_defaults(func=cmd_create_admin)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
