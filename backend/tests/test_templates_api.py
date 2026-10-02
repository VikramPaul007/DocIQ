from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.models import Workspace

VALID_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}


def _make_workspace_with_admin(db, make_user, make_membership):
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    admin = make_user()
    make_membership(workspace_id=ws.id, user_id=admin.id, role="admin")
    return ws, admin


def test_create_template_makes_an_initial_draft_version(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)

    resp = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "extract stuff", "document_type": "pdf"},
    )
    assert resp.status_code == 201, resp.text
    template_id = resp.json()["id"]

    versions = client.get(f"/api/templates/{template_id}/versions").json()
    assert len(versions) == 1
    assert versions[0]["version_no"] == 1
    assert versions[0]["status"] == "draft"


def test_reviewer_cannot_create_template(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    reviewer = make_user()
    make_membership(workspace_id=ws.id, user_id=reviewer.id, role="reviewer")
    csrf = login_as(reviewer)

    resp = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "X", "purpose": "p", "document_type": "pdf"},
    )
    assert resp.status_code == 403


def test_edit_validate_and_publish_draft(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)

    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]
    version_id = client.get(f"/api/templates/{template_id}/versions").json()[0]["id"]

    # An empty draft schema fails validation.
    resp = client.post(f"/api/versions/{version_id}/validate", headers={"X-CSRF-Token": csrf})
    assert resp.json()["valid"] is False

    resp = client.put(
        f"/api/versions/{version_id}",
        headers={"X-CSRF-Token": csrf},
        json={"system_prompt": "Extract the name.", "json_schema": VALID_SCHEMA},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(f"/api/versions/{version_id}/validate", headers={"X-CSRF-Token": csrf})
    assert resp.json() == {"valid": True, "errors": []}

    resp = client.post(f"/api/versions/{version_id}/publish", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "published"
    assert body["content_hash"]

    # Now immutable.
    resp = client.put(
        f"/api/versions/{version_id}",
        headers={"X-CSRF-Token": csrf},
        json={"system_prompt": "tampered"},
    )
    assert resp.status_code == 409


def test_publish_rejects_invalid_schema(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]
    version_id = client.get(f"/api/templates/{template_id}/versions").json()[0]["id"]

    resp = client.post(f"/api/versions/{version_id}/publish", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 422


def test_editing_published_template_opens_a_new_draft(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]
    v1_id = client.get(f"/api/templates/{template_id}/versions").json()[0]["id"]
    client.put(
        f"/api/versions/{v1_id}",
        headers={"X-CSRF-Token": csrf},
        json={"system_prompt": "p1", "json_schema": VALID_SCHEMA},
    )
    client.post(f"/api/versions/{v1_id}/publish", headers={"X-CSRF-Token": csrf})

    resp = client.post(f"/api/templates/{template_id}/draft", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert draft["version_no"] == 2
    assert draft["status"] == "draft"
    assert draft["system_prompt"] == "p1"  # cloned from latest published

    versions = client.get(f"/api/templates/{template_id}/versions").json()
    assert len(versions) == 2

    # Calling it again returns the same draft, doesn't create a v3.
    resp2 = client.post(f"/api/templates/{template_id}/draft", headers={"X-CSRF-Token": csrf})
    assert resp2.json()["id"] == draft["id"]


def test_agent_generated_version_requires_review_before_publish(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    from app.models import TemplateVersion

    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]
    version_id = client.get(f"/api/templates/{template_id}/versions").json()[0]["id"]
    client.put(
        f"/api/versions/{version_id}",
        headers={"X-CSRF-Token": csrf},
        json={"system_prompt": "p1", "json_schema": VALID_SCHEMA},
    )
    version = db.get(TemplateVersion, uuid.UUID(version_id))
    version.agent_generated = True
    db.commit()

    resp = client.post(f"/api/versions/{version_id}/publish", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 422
    assert "reviewed" in resp.json()["detail"]

    client.post(f"/api/versions/{version_id}/mark-reviewed", headers={"X-CSRF-Token": csrf})
    resp = client.post(f"/api/versions/{version_id}/publish", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200, resp.text


def test_non_member_gets_404_on_template(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "p", "document_type": "pdf"},
    ).json()["id"]

    outsider = make_user()
    login_as(outsider)
    resp = client.get(f"/api/templates/{template_id}")
    assert resp.status_code == 404
