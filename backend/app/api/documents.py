"""Document ingestion (runtime and test-harness) and file retrieval.

Validation (magic bytes, size, page count, corruption/encryption) happens
synchronously here, before storage or a job is ever created — the whole
point is a fast rejection with a clear reason, rather than an async job that
fails for a preventable reason minutes later.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db
from app.deps import CurrentUser, check_workspace_role
from app.models import (
    Document,
    Job,
    JobKind,
    JobStatus,
    MembershipRole,
    TemplateVersion,
    VersionStatus,
)
from app.pipeline import pdf_utils
from app.providers.storage import get_document, put_document

router = APIRouter(tags=["documents"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 50
PDF_MAGIC = b"%PDF-"


class DocumentOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    template_version_id: uuid.UUID
    sha256: str
    size: int
    page_count: int | None
    original_filename: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UploadResult(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID


def _sanitize_filename(name: str | None) -> str:
    base = os.path.basename(name or "document.pdf")
    return base[:255] or "document.pdf"


async def _read_and_validate_pdf(file: UploadFile) -> tuple[bytes, int]:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File exceeds the 20MB limit")
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is not a PDF")

    try:
        doc = pdf_utils.open_pdf(data)
    except pdf_utils.CorruptPdfError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "PDF is corrupt, encrypted, or unreadable"
        ) from None
    try:
        page_count = len(doc)
    finally:
        doc.close()

    if page_count > MAX_PAGES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"PDF has {page_count} pages (max {MAX_PAGES})"
        )
    return data, page_count


def _create_document_and_job(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    version_id: uuid.UUID,
    data: bytes,
    page_count: int,
    filename: str,
    uploaded_by: uuid.UUID,
    kind: str,
    config_snapshot: dict[str, Any] | None,
) -> tuple[Document, Job]:
    document = Document(
        workspace_id=workspace_id,
        template_version_id=version_id,
        storage_key=uuid.uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        page_count=page_count,
        original_filename=_sanitize_filename(filename),
        uploaded_by=uploaded_by,
    )
    db.add(document)
    db.flush()

    put_document(document.storage_key, data)

    job = Job(
        workspace_id=workspace_id,
        document_id=document.id,
        kind=kind,
        config_snapshot=config_snapshot,
        status=JobStatus.queued,
    )
    db.add(job)
    db.flush()
    return document, job


@router.post(
    "/api/workspaces/{workspace_id}/documents",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    workspace_id: uuid.UUID,
    file: UploadFile,
    template_version_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> UploadResult:
    check_workspace_role(db, user, workspace_id, (MembershipRole.admin,))

    version = db.get(TemplateVersion, template_version_id)
    if version is None or version.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template version not found")
    if version.status != VersionStatus.published:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Documents can only be uploaded against a published version",
        )

    data, page_count = await _read_and_validate_pdf(file)
    document, job = _create_document_and_job(
        db,
        workspace_id=workspace_id,
        version_id=version.id,
        data=data,
        page_count=page_count,
        filename=file.filename or "document.pdf",
        uploaded_by=user.id,
        kind=JobKind.runtime,
        config_snapshot=None,
    )
    record_audit_event(
        db,
        actor_id=user.id,
        workspace_id=workspace_id,
        entity_type="document",
        entity_id=document.id,
        action="uploaded",
        data={"template_version_id": str(version.id), "job_id": str(job.id)},
    )
    return UploadResult(document_id=document.id, job_id=job.id)


@router.post(
    "/api/versions/{version_id}/test",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
)
async def test_version(
    version_id: uuid.UUID,
    file: UploadFile,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> UploadResult:
    """Runs a sample document through a draft (or any) version without
    affecting the reviewer queue — the job carries a frozen snapshot of the
    version's config, so a later edit to the draft can't change what this
    test run is judged against."""
    version = db.get(TemplateVersion, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    check_workspace_role(db, user, version.workspace_id, (MembershipRole.admin,))

    data, page_count = await _read_and_validate_pdf(file)
    config_snapshot = {
        "template_version_id": str(version.id),
        "system_prompt": version.system_prompt,
        "json_schema": version.json_schema,
        "rules": version.rules,
        "scoring": version.scoring,
    }
    document, job = _create_document_and_job(
        db,
        workspace_id=version.workspace_id,
        version_id=version.id,
        data=data,
        page_count=page_count,
        filename=file.filename or "document.pdf",
        uploaded_by=user.id,
        kind=JobKind.test,
        config_snapshot=config_snapshot,
    )
    return UploadResult(document_id=document.id, job_id=job.id)


def _get_document_or_404(document_id: uuid.UUID, db: Session) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return document


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
def get_document_metadata(
    document_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> Document:
    return _get_document_or_404(document_id, db)


@router.get("/api/documents/{document_id}/file")
def get_document_file(
    document_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)
) -> Response:
    document = _get_document_or_404(document_id, db)
    data = get_document(document.storage_key)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
