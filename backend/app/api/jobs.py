"""Job status, listing (for the failure/retry view) and manual retry."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db
from app.deps import ANY_ROLE, CurrentUser, check_workspace_role, require_workspace_role
from app.models import Job, JobStatus, MembershipRole, Record

router = APIRouter(tags=["jobs"])


class JobOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    kind: str
    status: str
    stage: str | None
    attempts: int
    max_attempts: int
    last_error: str | None
    error_class: str | None
    created_at: datetime
    updated_at: datetime
    record_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


def _to_job_out(db: Session, job: Job) -> JobOut:
    out = JobOut.model_validate(job)
    record = db.query(Record).filter(Record.job_id == job.id).first()
    out.record_id = record.id if record else None
    return out


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)) -> JobOut:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return _to_job_out(db, job)


@router.get("/api/workspaces/{workspace_id}/jobs", response_model=list[JobOut])
def list_jobs(
    workspace_id: uuid.UUID,
    status_filter: str | None = None,
    user: object = Depends(require_workspace_role(*ANY_ROLE)),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    query = db.query(Job).filter(Job.workspace_id == workspace_id)
    if status_filter:
        query = query.filter(Job.status == status_filter)
    jobs = query.order_by(Job.created_at.desc()).all()
    return [_to_job_out(db, j) for j in jobs]


@router.post("/api/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)) -> JobOut:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    check_workspace_role(db, user, job.workspace_id, (MembershipRole.admin,))
    if job.status != JobStatus.failed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a failed job can be retried")

    job.status = JobStatus.queued
    job.attempts = 0
    job.last_error = None
    job.error_class = None
    job.locked_until = None
    job.locked_by = None
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=job.workspace_id,
        entity_type="job",
        entity_id=job.id,
        action="retried",
        data={},
    )
    return _to_job_out(db, job)
