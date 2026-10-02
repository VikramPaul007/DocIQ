"""DB-level guarantees that don't depend on any API route existing yet:
published template versions are immutable except for archiving, and
audit_events/reviews are append-only."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from app.models import AuditEvent, Template, TemplateVersion, Workspace


def _make_published_version(db) -> TemplateVersion:
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    template = Template(workspace_id=ws.id, name="t1", purpose="p", document_type="doc")
    db.add(template)
    db.commit()
    version = TemplateVersion(
        workspace_id=ws.id,
        template_id=template.id,
        version_no=1,
        status="published",
        system_prompt="prompt",
        json_schema={"type": "object", "properties": {}},
        rules=[],
        scoring={},
        content_hash="abc123",
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def test_published_version_cannot_be_edited(db) -> None:
    version = _make_published_version(db)
    version.system_prompt = "tampered"
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_published_version_cannot_be_deleted(db) -> None:
    version = _make_published_version(db)
    db.delete(version)
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_published_version_can_be_archived(db) -> None:
    version = _make_published_version(db)
    version.status = "archived"
    db.commit()
    db.refresh(version)
    assert version.status == "archived"


def test_audit_events_cannot_be_updated(db) -> None:
    event = AuditEvent(entity_type="test", action="created", data={})
    db.add(event)
    db.commit()
    event.action = "tampered"
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_audit_events_cannot_be_deleted(db) -> None:
    event = AuditEvent(entity_type="test", action="created", data={})
    db.add(event)
    db.commit()
    db.delete(event)
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()
