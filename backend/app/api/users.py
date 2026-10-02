"""Platform-admin user management. Ongoing user creation after the very first
admin, which is bootstrapped via ``python -m app.cli create-admin`` (see
app/cli.py) rather than through this API, so day zero never needs a running
server with a privileged account already in it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db
from app.deps import require_platform_admin
from app.models import User
from app.schemas import UserCreate, UserOut
from app.security import WeakPasswordError, hash_password, validate_password_policy

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(
    actor: User = Depends(require_platform_admin), db: Session = Depends(get_db)
) -> list[User]:
    return db.query(User).order_by(User.email).all()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    actor: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> User:
    if db.query(User).filter(User.email == payload.email).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    try:
        validate_password_policy(payload.password)
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        is_platform_admin=payload.is_platform_admin,
    )
    db.add(user)
    db.flush()
    record_audit_event(
        db,
        actor_id=actor.id,
        workspace_id=None,
        entity_type="user",
        entity_id=user.id,
        action="created",
        data={"email": user.email, "is_platform_admin": user.is_platform_admin},
    )
    return user
