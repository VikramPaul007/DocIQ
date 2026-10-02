"""POST /api/templates/{id}/generate, with the LLM provider swapped out via
FastAPI dependency override — no network calls in tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api.templates import get_llm_provider
from app.main import app
from app.models import Workspace
from app.providers.llm import FakeLlmProvider
from tests.test_agent import VALID_RESPONSE


def _make_workspace_with_admin(db, make_user, make_membership):
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    admin = make_user()
    make_membership(workspace_id=ws.id, user_id=admin.id, role="admin")
    return ws, admin


def test_generate_fills_draft_and_requires_review_before_publish(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws, admin = _make_workspace_with_admin(db, make_user, make_membership)
    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "extract name", "document_type": "pdf"},
    ).json()["id"]

    app.dependency_overrides[get_llm_provider] = lambda: FakeLlmProvider([VALID_RESPONSE])
    try:
        resp = client.post(f"/api/templates/{template_id}/generate", headers={"X-CSRF-Token": csrf})
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_generated"] is True
    assert body["system_prompt"] == "Extract the name."
    assert body["json_schema"]["additionalProperties"] is False

    # Agent-generated content can't be published until reviewed.
    resp2 = client.post(f"/api/versions/{body['id']}/publish", headers={"X-CSRF-Token": csrf})
    assert resp2.status_code == 422

    client.post(f"/api/versions/{body['id']}/mark-reviewed", headers={"X-CSRF-Token": csrf})
    resp3 = client.post(f"/api/versions/{body['id']}/publish", headers={"X-CSRF-Token": csrf})
    assert resp3.status_code == 200, resp3.text


def test_reviewer_cannot_trigger_generate(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    reviewer = make_user()
    make_membership(workspace_id=ws.id, user_id=reviewer.id, role="reviewer")
    admin = make_user()
    make_membership(workspace_id=ws.id, user_id=admin.id, role="admin")

    csrf = login_as(admin)
    template_id = client.post(
        f"/api/workspaces/{ws.id}/templates",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Onboarding", "purpose": "extract name", "document_type": "pdf"},
    ).json()["id"]

    reviewer_csrf = login_as(reviewer)
    resp = client.post(
        f"/api/templates/{template_id}/generate", headers={"X-CSRF-Token": reviewer_csrf}
    )
    assert resp.status_code == 403
