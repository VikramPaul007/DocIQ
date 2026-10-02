"""Single helper for writing audit trail rows. Every state-changing action in
the API and worker should call this in the same DB transaction as the change
it's recording."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record_audit_event(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID | None,
    action: str,
    data: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            data=data or {},
        )
    )
