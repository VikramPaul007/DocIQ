"""Record retrieval — the processed output of a job (OCR, extraction, rule
results, score/risk, summary). Review actions land here in a later milestone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser
from app.models import Record

router = APIRouter(tags=["records"])


class RecordOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    document_id: uuid.UUID
    template_version_id: uuid.UUID
    ocr: dict[str, Any] | None
    extraction: dict[str, Any] | None
    extraction_valid: bool | None
    validation_errors: list[Any] | None
    rule_results: list[Any] | None
    score: Decimal | None
    risk_level: str | None
    contributors: list[Any] | None
    summary: str | None
    evaluated_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/api/records/{record_id}", response_model=RecordOut)
def get_record(record_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db)) -> Record:
    record = db.get(Record, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return record
