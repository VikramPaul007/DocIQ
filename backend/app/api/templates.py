"""Template and template-version CRUD, plus the draft/publish lifecycle.

Routes scoped by ``template_id``/``version_id`` (not ``workspace_id`` in the
path) resolve the entity first — RLS already turns "wrong workspace" into "no
such row" — then call ``check_workspace_role`` for the admin/reviewer split,
rather than using ``require_workspace_role`` (which needs a path ``workspace_id``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db
from app.deps import ANY_ROLE, CurrentUser, check_workspace_role, require_workspace_role
from app.domain.versioning import PublishError, get_or_create_draft, publish, validate_version
from app.models import MembershipRole, Template, TemplateVersion, User, VersionStatus
from app.security import utcnow

router = APIRouter(tags=["templates"])


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1)
    document_type: str = Field(min_length=1, max_length=100)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    purpose: str | None = None
    document_type: str | None = Field(default=None, min_length=1, max_length=100)


class TemplateOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    purpose: str
    document_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TemplateVersionOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    version_no: int
    status: str
    system_prompt: str
    json_schema: dict[str, Any]
    rules: list[Any]
    scoring: dict[str, Any]
    agent_generated: bool
    human_reviewed_by: uuid.UUID | None
    human_reviewed_at: datetime | None
    content_hash: str | None
    published_by: uuid.UUID | None
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TemplateVersionUpdate(BaseModel):
    system_prompt: str | None = None
    json_schema: dict[str, Any] | None = None
    rules: list[Any] | None = None
    scoring: dict[str, Any] | None = None


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[str]


def _get_template_or_404(template_id: uuid.UUID, db: Session) -> Template:
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return template


def _get_version_or_404(version_id: uuid.UUID, db: Session) -> TemplateVersion:
    version = db.get(TemplateVersion, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return version


@router.post(
    "/api/workspaces/{workspace_id}/templates",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    workspace_id: uuid.UUID,
    payload: TemplateCreate,
    user: User = Depends(require_workspace_role(MembershipRole.admin)),
    db: Session = Depends(get_db),
) -> Template:
    if (
        db.query(Template)
        .filter(Template.workspace_id == workspace_id, Template.name == payload.name)
        .first()
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "A template with this name already exists")

    template = Template(
        workspace_id=workspace_id,
        name=payload.name,
        purpose=payload.purpose,
        document_type=payload.document_type,
        created_by=user.id,
    )
    db.add(template)
    db.flush()
    get_or_create_draft(db, template)  # every template starts with an editable v1 draft
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=workspace_id,
        entity_type="template",
        entity_id=template.id,
        action="created",
        data={"name": template.name},
    )
    return template


@router.get("/api/workspaces/{workspace_id}/templates", response_model=list[TemplateOut])
def list_templates(
    workspace_id: uuid.UUID,
    user: User = Depends(require_workspace_role(*ANY_ROLE)),
    db: Session = Depends(get_db),
) -> list[Template]:
    return (
        db.query(Template)
        .filter(Template.workspace_id == workspace_id)
        .order_by(Template.name)
        .all()
    )


@router.get("/api/templates/{template_id}", response_model=TemplateOut)
def get_template(
    template_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> Template:
    return _get_template_or_404(template_id, db)


@router.patch("/api/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: uuid.UUID,
    payload: TemplateUpdate,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Template:
    template = _get_template_or_404(template_id, db)
    check_workspace_role(db, user, template.workspace_id, (MembershipRole.admin,))
    if payload.name is not None:
        template.name = payload.name
    if payload.purpose is not None:
        template.purpose = payload.purpose
    if payload.document_type is not None:
        template.document_type = payload.document_type
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=template.workspace_id,
        entity_type="template",
        entity_id=template.id,
        action="updated",
        data={},
    )
    return template


@router.post("/api/templates/{template_id}/draft", response_model=TemplateVersionOut)
def open_draft(
    template_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> TemplateVersion:
    """Returns the template's current draft, creating one (cloned from the
    latest published version) if none exists — this is "Edit" on a published
    template: it never modifies the published row, only adds a new draft."""
    template = _get_template_or_404(template_id, db)
    check_workspace_role(db, user, template.workspace_id, (MembershipRole.admin,))
    draft = get_or_create_draft(db, template)
    return draft


@router.get("/api/templates/{template_id}/versions", response_model=list[TemplateVersionOut])
def list_versions(
    template_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> list[TemplateVersion]:
    _get_template_or_404(template_id, db)  # 404s if not a member of its workspace
    return (
        db.query(TemplateVersion)
        .filter(TemplateVersion.template_id == template_id)
        .order_by(TemplateVersion.version_no.desc())
        .all()
    )


@router.get("/api/versions/{version_id}", response_model=TemplateVersionOut)
def get_version(
    version_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> TemplateVersion:
    return _get_version_or_404(version_id, db)


@router.put("/api/versions/{version_id}", response_model=TemplateVersionOut)
def update_version(
    version_id: uuid.UUID,
    payload: TemplateVersionUpdate,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TemplateVersion:
    version = _get_version_or_404(version_id, db)
    check_workspace_role(db, user, version.workspace_id, (MembershipRole.admin,))
    if version.status != VersionStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft version can be edited")

    if payload.system_prompt is not None:
        version.system_prompt = payload.system_prompt
    if payload.json_schema is not None:
        version.json_schema = payload.json_schema
    if payload.rules is not None:
        version.rules = payload.rules
    if payload.scoring is not None:
        version.scoring = payload.scoring
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=version.workspace_id,
        entity_type="template_version",
        entity_id=version.id,
        action="updated",
        data={},
    )
    return version


@router.post("/api/versions/{version_id}/validate", response_model=ValidateResponse)
def validate_version_endpoint(
    version_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> ValidateResponse:
    version = _get_version_or_404(version_id, db)
    errors = validate_version(version)
    return ValidateResponse(valid=not errors, errors=errors)


@router.post("/api/versions/{version_id}/mark-reviewed", response_model=TemplateVersionOut)
def mark_reviewed(
    version_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> TemplateVersion:
    version = _get_version_or_404(version_id, db)
    check_workspace_role(db, user, version.workspace_id, (MembershipRole.admin,))
    if version.status != VersionStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft version can be marked reviewed")
    version.human_reviewed_by = user.id
    version.human_reviewed_at = utcnow()
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=version.workspace_id,
        entity_type="template_version",
        entity_id=version.id,
        action="marked_reviewed",
        data={},
    )
    return version


@router.post("/api/versions/{version_id}/publish", response_model=TemplateVersionOut)
def publish_version(
    version_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> TemplateVersion:
    version = _get_version_or_404(version_id, db)
    check_workspace_role(db, user, version.workspace_id, (MembershipRole.admin,))
    try:
        publish(db, version, published_by=user.id)
    except PublishError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(exc.errors)) from exc
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=version.workspace_id,
        entity_type="template_version",
        entity_id=version.id,
        action="published",
        data={"version_no": version.version_no, "content_hash": version.content_hash},
    )
    return version
