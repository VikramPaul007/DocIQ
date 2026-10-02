"""Reviewer queue, review actions, and the per-record audit timeline.

Every processed record lands in the queue (nothing is auto-approved, see
"Decisions I made" #1 in docs/technical-implementation-plan.md); any review
action closes it (job -> COMPLETED) and removes it from the queue.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db
from app.deps import ANY_ROLE, CurrentUser, check_workspace_role, require_workspace_role
from app.models import (
    AuditEvent,
    Document,
    Job,
    JobKind,
    JobStatus,
    MembershipRole,
    Record,
    Review,
    TemplateVersion,
    User,
)

router = APIRouter(tags=["reviews"])


class ReviewQueueEntry(BaseModel):
    job_id: uuid.UUID
    document_id: uuid.UUID
    record_id: uuid.UUID
    template_version_id: uuid.UUID
    original_filename: str
    risk_level: str | None
    score: Decimal | None
    summary: str | None
    created_at: datetime


@router.get("/api/workspaces/{workspace_id}/review-queue", response_model=list[ReviewQueueEntry])
def get_review_queue(
    workspace_id: uuid.UUID,
    template_id: uuid.UUID | None = None,
    risk: str | None = None,
    user: User = Depends(require_workspace_role(*ANY_ROLE)),
    db: Session = Depends(get_db),
) -> list[ReviewQueueEntry]:
    query = (
        db.query(Job, Record, Document)
        .join(Record, Record.job_id == Job.id)
        .join(Document, Document.id == Job.document_id)
        .filter(
            Job.workspace_id == workspace_id,
            Job.status == JobStatus.review_required,
            Job.kind == JobKind.runtime,
        )
    )
    if template_id is not None:
        version_ids = db.query(TemplateVersion.id).filter(
            TemplateVersion.template_id == template_id
        )
        query = query.filter(Record.template_version_id.in_(version_ids))
    if risk is not None:
        query = query.filter(Record.risk_level == risk)

    risk_order = case(
        (Record.risk_level == "high", 0),
        (Record.risk_level == "medium", 1),
        (Record.risk_level == "low", 2),
        else_=3,
    )
    rows = query.order_by(risk_order, Job.created_at).all()
    return [
        ReviewQueueEntry(
            job_id=job.id,
            document_id=document.id,
            record_id=record.id,
            template_version_id=record.template_version_id,
            original_filename=document.original_filename,
            risk_level=record.risk_level,
            score=record.score,
            summary=record.summary,
            created_at=job.created_at,
        )
        for job, record, document in rows
    ]


class ReviewCreate(BaseModel):
    action: Literal["approve", "reject", "request_correction"]
    comment: str | None = Field(default=None, max_length=4000)


class ReviewOut(BaseModel):
    id: uuid.UUID
    record_id: uuid.UUID
    reviewer_id: uuid.UUID
    action: str
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post(
    "/api/records/{record_id}/reviews",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    record_id: uuid.UUID,
    payload: ReviewCreate,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Review:
    record = db.get(Record, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    check_workspace_role(
        db, user, record.workspace_id, (MembershipRole.admin, MembershipRole.reviewer)
    )

    job = db.get(Job, record.job_id)
    if job is None or job.status != JobStatus.review_required:
        raise HTTPException(status.HTTP_409_CONFLICT, "This record is not awaiting review")

    if payload.action in ("reject", "request_correction") and not (payload.comment or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A comment is required for this action"
        )

    review = Review(
        workspace_id=record.workspace_id,
        record_id=record.id,
        reviewer_id=user.id,
        action=payload.action,
        comment=payload.comment,
    )
    db.add(review)
    job.status = JobStatus.completed
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=record.workspace_id,
        entity_type="job",
        entity_id=job.id,
        action="reviewed",
        data={"review_action": payload.action},
    )
    db.flush()
    return review


class TimelineEntry(BaseModel):
    at: datetime
    actor_email: str | None
    action: str
    details: dict[str, Any] | None = None


@router.get("/api/records/{record_id}/audit", response_model=list[TimelineEntry])
def get_record_audit(
    record_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> list[TimelineEntry]:
    record = db.get(Record, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    events = (
        db.query(AuditEvent)
        .filter(
            or_(
                and_(AuditEvent.entity_type == "job", AuditEvent.entity_id == record.job_id),
                and_(
                    AuditEvent.entity_type == "document", AuditEvent.entity_id == record.document_id
                ),
                and_(
                    AuditEvent.entity_type == "template_version",
                    AuditEvent.entity_id == record.template_version_id,
                ),
            )
        )
        .all()
    )
    reviews = db.query(Review).filter(Review.record_id == record_id).all()

    actor_ids = {e.actor_id for e in events if e.actor_id} | {r.reviewer_id for r in reviews}
    emails = (
        {u.id: u.email for u in db.query(User).filter(User.id.in_(actor_ids))} if actor_ids else {}
    )

    entries = [
        TimelineEntry(
            at=e.at,
            actor_email=emails.get(e.actor_id) if e.actor_id else None,
            action=f"{e.entity_type}.{e.action}",
            details=e.data or None,
        )
        for e in events
    ]
    entries += [
        TimelineEntry(
            at=r.created_at,
            actor_email=emails.get(r.reviewer_id),
            action=f"review.{r.action}",
            details={"comment": r.comment} if r.comment else None,
        )
        for r in reviews
    ]
    entries.sort(key=lambda e: e.at)
    return entries
