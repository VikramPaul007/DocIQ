from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import DEFAULT_TEST_PASSWORD


def test_platform_admin_creates_workspace_and_adds_member(
    client: TestClient, make_user, login_as
) -> None:
    admin = make_user(is_platform_admin=True)
    member = make_user()

    csrf = login_as(admin)
    ws_name = f"HR-{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/workspaces", headers={"X-CSRF-Token": csrf}, json={"name": ws_name})
    assert resp.status_code == 201, resp.text
    workspace_id = resp.json()["id"]

    resp = client.put(
        f"/api/workspaces/{workspace_id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": member.email, "role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"

    resp = client.get(f"/api/workspaces/{workspace_id}/members", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200
    emails = {m["email"] for m in resp.json()}
    assert member.email in emails


def test_member_can_see_their_own_workspace_but_not_others(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    from app.models import Workspace

    ws_mine = Workspace(name=f"mine-{uuid.uuid4().hex[:8]}")
    ws_other = Workspace(name=f"other-{uuid.uuid4().hex[:8]}")
    db.add_all([ws_mine, ws_other])
    db.commit()

    user = make_user()
    make_membership(workspace_id=ws_mine.id, user_id=user.id, role="reviewer")

    login_as(user)
    resp = client.get("/api/workspaces")
    assert resp.status_code == 200
    ids = {w["id"] for w in resp.json()}
    assert str(ws_mine.id) in ids
    assert str(ws_other.id) not in ids


def test_cannot_remove_last_admin(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    from app.models import Workspace

    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()

    admin = make_user()
    make_membership(workspace_id=ws.id, user_id=admin.id, role="admin")

    csrf = login_as(admin)
    resp = client.delete(
        f"/api/workspaces/{ws.id}/members/{admin.id}", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 409


def test_platform_admin_creates_user(client: TestClient, make_user, login_as) -> None:
    admin = make_user(is_platform_admin=True)
    csrf = login_as(admin)
    new_email = f"new-{uuid.uuid4().hex[:8]}@dociq-fixtures.com"
    resp = client.post(
        "/api/users",
        headers={"X-CSRF-Token": csrf},
        json={"email": new_email, "password": DEFAULT_TEST_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == new_email

    # the new user can now log in
    login_resp = client.post(
        "/api/auth/login", json={"email": new_email, "password": DEFAULT_TEST_PASSWORD}
    )
    assert login_resp.status_code == 200
