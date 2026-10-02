"""FastAPI dependencies: session auth, CSRF enforcement, workspace authorization.

``CurrentUser`` is the one type routes should use for "any logged-in user" —
it resolves the session cookie, sets the RLS context for the request's
transaction, and enforces CSRF on non-GET requests. ``require_workspace_role``
builds a per-route dependency that additionally requires workspace membership
with one of the given roles (platform admins always pass). Non-membership
returns 404 (don't reveal whether the workspace exists); wrong role in a real
membership returns 403.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db, set_rls_context
from app.models import Membership, MembershipRole, Session_, User
from app.security import (
    SESSION_IDLE_TIMEOUT,
    constant_time_eq,
    hash_token,
    utcnow,
)

SESSION_COOKIE_NAME = "__Host-session"


def _get_authenticated_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    token_hash = hash_token(raw_token)
    session_row = db.query(Session_).filter(Session_.token_hash == token_hash).first()
    if session_row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    now = utcnow()
    expires_at = session_row.expires_at
    last_seen_at = session_row.last_seen_at
    if expires_at.tzinfo is None:  # defensive: DB driver should always return aware datetimes
        expires_at = expires_at.replace(tzinfo=now.tzinfo)
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=now.tzinfo)

    if expires_at <= now or (now - last_seen_at) > SESSION_IDLE_TIMEOUT:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    user = db.get(User, session_row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    session_row.last_seen_at = now
    request.state.session = session_row
    request.state.user = user

    set_rls_context(db, user_id=user.id, is_platform_admin=user.is_platform_admin)
    return user


def _require_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    session_row: Session_ | None = getattr(request.state, "session", None)
    if session_row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    header_token = request.headers.get("X-CSRF-Token")
    if not header_token or not constant_time_eq(header_token, session_row.csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing CSRF token")


def _authed_user(
    user: Annotated[User, Depends(_get_authenticated_user)],
    _csrf: Annotated[None, Depends(_require_csrf)],
) -> User:
    return user


CurrentUser = Annotated[User, Depends(_authed_user)]


def require_platform_admin(user: CurrentUser) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform admin required")
    return user


def check_workspace_role(
    db: Session, user: User, workspace_id: uuid.UUID, allowed_roles: tuple[MembershipRole, ...]
) -> None:
    """The shared check behind both ``require_workspace_role`` (path has a
    ``workspace_id``) and entity-scoped routes like templates/versions, which
    resolve their own workspace_id from the entity (already RLS-filtered to
    "not found" for non-members) and then call this for the role check."""
    if user.is_platform_admin:
        return
    allowed = {r.value for r in allowed_roles}
    membership = (
        db.query(Membership)
        .filter(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
        .first()
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if membership.role not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role for this workspace")


def require_workspace_role(*allowed_roles: MembershipRole) -> Callable[..., User]:
    def _dep(
        workspace_id: uuid.UUID,
        user: CurrentUser,
        db: Session = Depends(get_db),
    ) -> User:
        check_workspace_role(db, user, workspace_id, allowed_roles)
        return user

    return _dep


# Convenience for routes any workspace member may use (e.g. viewing), regardless
# of their specific role: require_workspace_role(*ANY_ROLE)
ANY_ROLE = (MembershipRole.admin, MembershipRole.reviewer)
