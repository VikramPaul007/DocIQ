"""Workspace CRUD and membership management.

Workspace creation is platform-admin only (see "Decisions I made" #3 in
docs/technical-implementation-plan.md). Within a workspace, the ``admin`` role
manages members and configuration; ``reviewer`` only reviews. List endpoints
rely on the RLS policy to filter to the caller's own workspaces — no manual
membership filter needed in the query itself.
"""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db
from app.deps import ANY_ROLE, CurrentUser, require_platform_admin, require_workspace_role
from app.models import Membership, MembershipRole, User, Workspace
from app.schemas import MemberAdd, MemberOut, Role, WorkspaceCreate, WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(user: CurrentUser, db: Session = Depends(get_db)) -> list[Workspace]:
    return db.query(Workspace).order_by(Workspace.name).all()


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: WorkspaceCreate,
    user: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> Workspace:
    if db.query(Workspace).filter(Workspace.name == payload.name).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A workspace with this name already exists")
    workspace = Workspace(name=payload.name, description=payload.description, created_by=user.id)
    db.add(workspace)
    db.flush()  # populate workspace.id for the audit event below
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=workspace.id,
        entity_type="workspace",
        entity_id=workspace.id,
        action="created",
        data={"name": workspace.name},
    )
    return workspace


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: uuid.UUID,
    user: User = Depends(require_workspace_role(*ANY_ROLE)),
    db: Session = Depends(get_db),
) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return workspace


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceUpdate,
    user: User = Depends(require_workspace_role(MembershipRole.admin)),
    db: Session = Depends(get_db),
) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if payload.name is not None:
        workspace.name = payload.name
    if payload.description is not None:
        workspace.description = payload.description
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=workspace.id,
        entity_type="workspace",
        entity_id=workspace.id,
        action="updated",
        data={},
    )
    return workspace


@router.get("/{workspace_id}/members", response_model=list[MemberOut])
def list_members(
    workspace_id: uuid.UUID,
    user: User = Depends(require_workspace_role(*ANY_ROLE)),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    rows = (
        db.query(Membership, User)
        .join(User, User.id == Membership.user_id)
        .filter(Membership.workspace_id == workspace_id)
        .order_by(User.email)
        .all()
    )
    return [
        MemberOut(
            user_id=m.user_id, email=u.email, role=cast(Role, m.role), created_at=m.created_at
        )
        for m, u in rows
    ]


@router.put("/{workspace_id}/members", response_model=MemberOut)
def add_or_update_member(
    workspace_id: uuid.UUID,
    payload: MemberAdd,
    actor: User = Depends(require_workspace_role(MembershipRole.admin)),
    db: Session = Depends(get_db),
) -> MemberOut:
    target_user = db.query(User).filter(User.email == payload.email).first()
    if target_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No user with this email exists")

    membership = (
        db.query(Membership)
        .filter(Membership.workspace_id == workspace_id, Membership.user_id == target_user.id)
        .first()
    )
    if membership is None:
        membership = Membership(
            workspace_id=workspace_id, user_id=target_user.id, role=payload.role
        )
        db.add(membership)
        action = "member_added"
    else:
        membership.role = payload.role
        action = "member_role_changed"

    record_audit_event(
        db,
        actor_id=actor.id,
        workspace_id=workspace_id,
        entity_type="membership",
        entity_id=target_user.id,
        action=action,
        data={"role": payload.role},
    )
    db.flush()
    return MemberOut(
        user_id=target_user.id,
        email=target_user.email,
        role=cast(Role, membership.role),
        created_at=membership.created_at,
    )


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    actor: User = Depends(require_workspace_role(MembershipRole.admin)),
    db: Session = Depends(get_db),
) -> None:
    membership = (
        db.query(Membership)
        .filter(Membership.workspace_id == workspace_id, Membership.user_id == user_id)
        .first()
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    if membership.role == MembershipRole.admin.value:
        other_admins = (
            db.query(Membership)
            .filter(
                Membership.workspace_id == workspace_id,
                Membership.role == MembershipRole.admin.value,
                Membership.user_id != user_id,
            )
            .count()
        )
        if other_admins == 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Cannot remove the last admin of a workspace"
            )

    db.delete(membership)
    record_audit_event(
        db,
        actor_id=actor.id,
        workspace_id=workspace_id,
        entity_type="membership",
        entity_id=user_id,
        action="member_removed",
        data={},
    )
