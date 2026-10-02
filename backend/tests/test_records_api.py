from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.models import (
    Document,
    Job,
    JobStatus,
    Record,
    Template,
    TemplateVersion,
    VersionStatus,
    Workspace,
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"name": {"type": "string"}},
}


def _make_record(db, *, member_user_id=None) -> tuple[uuid.UUID, uuid.UUID]:
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    template = Template(workspace_id=ws.id, name="t1", purpose="p", document_type="pdf")
    db.add(template)
    db.commit()
    version = TemplateVersion(
        workspace_id=ws.id,
        template_id=template.id,
        version_no=1,
        status=VersionStatus.published,
        system_prompt="p",
        json_schema=SCHEMA,
        rules=[],
        scoring={},
        content_hash="abc",
    )
    db.add(version)
    db.commit()
    document = Document(
        workspace_id=ws.id,
        template_version_id=version.id,
        storage_key=uuid.uuid4(),
        sha256="x",
        size=1,
        page_count=1,
        original_filename="a.pdf",
    )
    db.add(document)
    db.commit()
    job = Job(
        workspace_id=ws.id,
        document_id=document.id,
        kind="runtime",
        status=JobStatus.review_required,
    )
    db.add(job)
    db.commit()
    record = Record(
        workspace_id=ws.id,
        job_id=job.id,
        document_id=document.id,
        template_version_id=version.id,
        extraction={"name": "Alice"},
        summary="A summary.",
    )
    db.add(record)
    db.commit()
    return ws.id, record.id


def test_get_record_requires_membership(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, record_id = _make_record(db)
    outsider = make_user()
    login_as(outsider)
    resp = client.get(f"/api/records/{record_id}")
    assert resp.status_code == 404


def test_get_record_returns_full_payload_for_member(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, record_id = _make_record(db)
    reviewer = make_user()
    make_membership(workspace_id=ws_id, user_id=reviewer.id, role="reviewer")
    login_as(reviewer)

    resp = client.get(f"/api/records/{record_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction"] == {"name": "Alice"}
    assert body["summary"] == "A summary."
