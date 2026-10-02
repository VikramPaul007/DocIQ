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


def _make_review_required_record(
    db, *, risk_level: str = "high"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
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
        risk_level=risk_level,
        summary="A summary.",
    )
    db.add(record)
    db.commit()
    return ws.id, job.id, record.id


def _add_reviewer(db, make_user, make_membership, ws_id, role="reviewer"):
    user = make_user()
    make_membership(workspace_id=ws_id, user_id=user.id, role=role)
    return user


def test_queue_lists_review_required_jobs_sorted_by_risk(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, _, _ = _make_review_required_record(db, risk_level="high")
    # A second, low-risk record in the *same* workspace (reusing its template/version).
    template = db.query(Template).filter(Template.workspace_id == ws_id).first()
    version = db.query(TemplateVersion).filter(TemplateVersion.template_id == template.id).first()
    document2 = Document(
        workspace_id=ws_id,
        template_version_id=version.id,
        storage_key=uuid.uuid4(),
        sha256="y",
        size=1,
        page_count=1,
        original_filename="b.pdf",
    )
    db.add(document2)
    db.commit()
    job2 = Job(
        workspace_id=ws_id,
        document_id=document2.id,
        kind="runtime",
        status=JobStatus.review_required,
    )
    db.add(job2)
    db.commit()
    low_record2 = Record(
        workspace_id=ws_id,
        job_id=job2.id,
        document_id=document2.id,
        template_version_id=version.id,
        extraction={},
        risk_level="low",
    )
    db.add(low_record2)
    db.commit()

    reviewer = _add_reviewer(db, make_user, make_membership, ws_id)
    login_as(reviewer)

    resp = client.get(f"/api/workspaces/{ws_id}/review-queue")
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    assert len(entries) == 2
    assert entries[0]["risk_level"] == "high"  # high risk sorts first
    assert entries[1]["risk_level"] == "low"


def test_review_queue_filters_by_risk(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, _, _ = _make_review_required_record(db, risk_level="high")
    reviewer = _add_reviewer(db, make_user, make_membership, ws_id)
    login_as(reviewer)

    resp = client.get(f"/api/workspaces/{ws_id}/review-queue", params={"risk": "low"})
    assert resp.json() == []
    resp = client.get(f"/api/workspaces/{ws_id}/review-queue", params={"risk": "high"})
    assert len(resp.json()) == 1


def test_reviewer_can_approve(client: TestClient, make_user, login_as, make_membership, db) -> None:
    ws_id, job_id, record_id = _make_review_required_record(db)
    reviewer = _add_reviewer(db, make_user, make_membership, ws_id)
    csrf = login_as(reviewer)

    resp = client.post(
        f"/api/records/{record_id}/reviews",
        headers={"X-CSRF-Token": csrf},
        json={"action": "approve"},
    )
    assert resp.status_code == 201, resp.text

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"

    # No longer in the queue.
    queue = client.get(f"/api/workspaces/{ws_id}/review-queue").json()
    assert queue == []


def test_reject_requires_a_comment(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, job_id, record_id = _make_review_required_record(db)
    reviewer = _add_reviewer(db, make_user, make_membership, ws_id)
    csrf = login_as(reviewer)

    resp = client.post(
        f"/api/records/{record_id}/reviews",
        headers={"X-CSRF-Token": csrf},
        json={"action": "reject"},
    )
    assert resp.status_code == 422

    resp = client.post(
        f"/api/records/{record_id}/reviews",
        headers={"X-CSRF-Token": csrf},
        json={"action": "reject", "comment": "Missing required document"},
    )
    assert resp.status_code == 201, resp.text


def test_cannot_review_same_record_twice(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, job_id, record_id = _make_review_required_record(db)
    reviewer = _add_reviewer(db, make_user, make_membership, ws_id)
    csrf = login_as(reviewer)

    resp1 = client.post(
        f"/api/records/{record_id}/reviews",
        headers={"X-CSRF-Token": csrf},
        json={"action": "approve"},
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        f"/api/records/{record_id}/reviews",
        headers={"X-CSRF-Token": csrf},
        json={"action": "approve"},
    )
    assert resp2.status_code == 409


def test_audit_timeline_includes_review_action(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, job_id, record_id = _make_review_required_record(db)
    reviewer = _add_reviewer(db, make_user, make_membership, ws_id)
    csrf = login_as(reviewer)
    client.post(
        f"/api/records/{record_id}/reviews",
        headers={"X-CSRF-Token": csrf},
        json={"action": "request_correction", "comment": "Please re-upload a clearer scan"},
    )

    resp = client.get(f"/api/records/{record_id}/audit")
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e["action"] == "review.request_correction" for e in entries)
    assert any(e["actor_email"] == reviewer.email for e in entries)


def test_non_member_gets_404_on_queue_and_audit(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws_id, job_id, record_id = _make_review_required_record(db)
    outsider = make_user()
    login_as(outsider)

    assert client.get(f"/api/workspaces/{ws_id}/review-queue").status_code == 404
    assert client.get(f"/api/records/{record_id}/audit").status_code == 404
