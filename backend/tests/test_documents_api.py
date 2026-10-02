from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.models import Workspace
from tests.conftest import minimal_blank_pdf, minimal_pdf_with_text

VALID_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"name": {"type": "string"}},
}


def _published_version(client: TestClient, csrf: str, workspace_id) -> str:
    template_id = client.post(
        f"/api/workspaces/{workspace_id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": f"t-{uuid.uuid4().hex[:6]}", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]
    version_id = client.get(f"/api/templates/{template_id}/versions").json()[0]["id"]
    client.put(
        f"/api/versions/{version_id}",
        headers={"X-CSRF-Token": csrf},
        json={"system_prompt": "Extract the name.", "json_schema": VALID_SCHEMA},
    )
    client.post(f"/api/versions/{version_id}/publish", headers={"X-CSRF-Token": csrf})
    return version_id


def _make_workspace_with_admin(db, make_user, make_membership):
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    admin = make_user()
    make_membership(workspace_id=ws.id, user_id=admin.id, role="admin")
    return ws, admin


def test_upload_rejects_non_pdf(client: TestClient, make_user, login_as, make_membership, db) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    version_id = _published_version(client, csrf, ws.id)

    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        headers={"X-CSRF-Token": csrf},
        params={"template_version_id": version_id},
        files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
    )
    assert resp.status_code == 422


def test_upload_rejects_oversized_file(client: TestClient, make_user, login_as, make_membership, db) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    version_id = _published_version(client, csrf, ws.id)

    oversized = b"%PDF-" + b"0" * (20 * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        headers={"X-CSRF-Token": csrf},
        params={"template_version_id": version_id},
        files={"file": ("x.pdf", oversized, "application/pdf")},
    )
    assert resp.status_code == 413


def test_upload_rejects_draft_version(client: TestClient, make_user, login_as, make_membership, db) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "t1", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]
    draft_version_id = client.get(f"/api/templates/{template_id}/versions").json()[0]["id"]

    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        headers={"X-CSRF-Token": csrf},
        params={"template_version_id": draft_version_id},
        files={"file": ("x.pdf", minimal_blank_pdf(), "application/pdf")},
    )
    assert resp.status_code == 422


def test_upload_succeeds_and_creates_queued_job(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    version_id = _published_version(client, csrf, ws.id)

    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        headers={"X-CSRF-Token": csrf},
        params={"template_version_id": version_id},
        files={"file": ("sample.pdf", minimal_pdf_with_text("Name: Alice Example"), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "QUEUED"
    assert job["kind"] == "runtime"

    doc = client.get(f"/api/documents/{body['document_id']}").json()
    assert doc["page_count"] == 1

    file_resp = client.get(f"/api/documents/{body['document_id']}/file")
    assert file_resp.status_code == 200
    assert file_resp.content.startswith(b"%PDF-")


def test_reviewer_cannot_upload(client: TestClient, make_user, login_as, make_membership, db) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    version_id = _published_version(client, csrf, ws.id)

    reviewer = make_user()
    make_membership(workspace_id=ws.id, user_id=reviewer.id, role="reviewer")
    reviewer_csrf = login_as(reviewer)

    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        headers={"X-CSRF-Token": reviewer_csrf},
        params={"template_version_id": version_id},
        files={"file": ("x.pdf", minimal_blank_pdf(), "application/pdf")},
    )
    assert resp.status_code == 403


def test_version_test_harness_accepts_draft(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "t1", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]
    draft_version_id = client.get(f"/api/templates/{template_id}/versions").json()[0]["id"]

    resp = client.post(
        f"/api/versions/{draft_version_id}/test",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("sample.pdf", minimal_blank_pdf(), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    job = client.get(f"/api/jobs/{resp.json()['job_id']}").json()
    assert job["kind"] == "test"
