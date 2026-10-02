"""Login, logout, current-user, password change.

Login deliberately runs a password verify against a fixed dummy hash even when
the email doesn't match any account, so the response time doesn't leak account
existence. Rate limiting is per source IP (via Caddy's X-Forwarded-For),
across all accounts, backed by a plain Postgres counter table — see
``docs/technical-implementation-plan.md`` for why no Redis.
"""

from __future__ import annotations

import secrets
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db, set_rls_context
from app.deps import SESSION_COOKIE_NAME, CurrentUser
from app.models import LoginRateLimit, Membership, Session_, User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MembershipOut,
    MeResponse,
    Role,
    UserOut,
)
from app.security import (
    LOCKOUT_DURATION,
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
    LOGIN_RATE_LIMIT_WINDOW,
    MAX_FAILED_LOGINS,
    SESSION_ABSOLUTE_TIMEOUT,
    WeakPasswordError,
    generate_csrf_token,
    generate_session_token,
    hash_password,
    utcnow,
    validate_password_policy,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Computed once at import time; used to equalize login response time for
# unknown emails (argon2 verify is deliberately slow, so this matters).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _check_rate_limit(db: Session, ip: str | None) -> None:
    """Commits the counter itself immediately, independent of how the rest of
    the request turns out: ``get_db`` rolls back the whole transaction on any
    raised exception (including the 401/423 this same login request may raise
    later), which would otherwise silently erase this bookkeeping every time —
    exactly the failed/blocked attempts a rate limiter most needs to count.
    """
    if not ip:
        return
    now = utcnow()
    row = db.get(LoginRateLimit, ip)
    if row is None:
        row = LoginRateLimit(ip=ip, window_start=now, count=1)
        db.add(row)
    else:
        window_start = row.window_start
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=now.tzinfo)
        if now - window_start > LOGIN_RATE_LIMIT_WINDOW:
            row.window_start = now
            row.count = 1
        else:
            row.count += 1
    db.commit()
    if row.count > LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts, try again later"
        )


def _build_me_response(db: Session, user: User, csrf_token: str) -> MeResponse:
    memberships = db.query(Membership).filter(Membership.user_id == user.id).all()
    return MeResponse(
        user=UserOut.model_validate(user),
        csrf_token=csrf_token,
        memberships=[
            MembershipOut(workspace_id=m.workspace_id, role=cast(Role, m.role)) for m in memberships
        ],
    )


@router.post("/login", response_model=MeResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> MeResponse:
    client_ip = request.client.host if request.client else None
    _check_rate_limit(db, client_ip)

    user = db.query(User).filter(User.email == payload.email).first()
    now = utcnow()

    if user is not None and user.locked_until is not None:
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=now.tzinfo)
        if locked_until > now:
            raise HTTPException(
                status.HTTP_423_LOCKED, "Account temporarily locked, try again later"
            )

    password_hash = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(payload.password, password_hash)

    if not (password_ok and user is not None and user.is_active):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED_LOGINS:
                user.locked_until = now + LOCKOUT_DURATION
            # Known identity (login just failed, but we know *who* for):
            # audit_events' RLS SELECT policy needs app.user_id set for the
            # RETURNING clause on this INSERT to see its own just-written row.
            set_rls_context(db, user_id=user.id, is_platform_admin=user.is_platform_admin)
            record_audit_event(
                db,
                actor_id=user.id,
                workspace_id=None,
                entity_type="user",
                entity_id=user.id,
                action="login_failed",
                data={},
            )
            # Persist the failed-attempt/lockout bookkeeping before raising —
            # see _check_rate_limit's docstring for why it can't wait for
            # get_db's end-of-request commit.
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    user.failed_logins = 0
    user.locked_until = None

    # Known identity from here on: set the RLS context now so the membership
    # lookup below (and the audit insert) see this user's own rows.
    set_rls_context(db, user_id=user.id, is_platform_admin=user.is_platform_admin)

    raw_session_token, session_token_hash = generate_session_token()
    csrf_token = generate_csrf_token()
    db.add(
        Session_(
            user_id=user.id,
            token_hash=session_token_hash,
            csrf_token=csrf_token,
            expires_at=now + SESSION_ABSOLUTE_TIMEOUT,
            last_seen_at=now,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
        )
    )
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=None,
        entity_type="user",
        entity_id=user.id,
        action="login_succeeded",
        data={},
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_session_token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
        max_age=int(SESSION_ABSOLUTE_TIMEOUT.total_seconds()),
    )

    return _build_me_response(db, user, csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, user: CurrentUser, db: Session = Depends(get_db)
) -> None:
    session_row: Session_ = request.state.session
    db.delete(session_row)
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=None,
        entity_type="user",
        entity_id=user.id,
        action="logout",
        data={},
    )
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=MeResponse)
def me(request: Request, user: CurrentUser, db: Session = Depends(get_db)) -> MeResponse:
    session_row: Session_ = request.state.session
    return _build_me_response(db, user, session_row.csrf_token)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    try:
        validate_password_policy(payload.new_password)
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    user.password_hash = hash_password(payload.new_password)
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=None,
        entity_type="user",
        entity_id=user.id,
        action="password_changed",
        data={},
    )
