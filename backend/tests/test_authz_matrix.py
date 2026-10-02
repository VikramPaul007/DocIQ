"""One parametrized test over every route the app exposes: anonymous must
never get past the auth dependency. This fails automatically the moment a new
route is added without going through CurrentUser / require_workspace_role /
require_platform_admin, rather than relying on someone remembering to test it.

Role-specific checks (404 for non-members, 403 for wrong role) are covered
separately below for the routes that currently exist with workspace scoping.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.models import Workspace

PUBLIC_ROUTES = {("POST", "/api/auth/login"), ("GET", "/api/health")}


def _iter_api_routes():
    """FastAPI wraps each app.include_router(...) call in an opaque
    `_IncludedRouter` rather than flattening its routes into `app.routes`
    directly — the real APIRoute objects live on `.original_router.routes`.
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from (r for r in route.original_router.routes if isinstance(r, APIRoute))


def _all_protected_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for route in _iter_api_routes():
        for method in route.methods - {"HEAD", "OPTIONS"}:
            key = (method, route.path)
            if key in PUBLIC_ROUTES or key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _fill_path(path_template: str) -> str:
    return path_template.format(
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        record_id=uuid.uuid4(),
    )


@pytest.mark.parametrize("method,path_template", _all_protected_routes())
def test_anonymous_request_is_rejected(client: TestClient, method: str, path_template: str) -> None:
    path = _fill_path(path_template)
    resp = client.request(method, path, json={})
    assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"


def test_non_member_gets_404_on_workspace_scoped_route(
    client: TestClient, make_user, login_as, db
) -> None:
    outsider = make_user()
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()

    login_as(outsider)
    resp = client.get(f"/api/workspaces/{ws.id}")
    assert resp.status_code == 404


def test_reviewer_gets_403_on_admin_only_route(
    client: TestClient, make_user, login_as, make_membership, db
) -> None:
    reviewer = make_user()
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    make_membership(workspace_id=ws.id, user_id=reviewer.id, role="reviewer")

    csrf = login_as(reviewer)
    resp = client.patch(
        f"/api/workspaces/{ws.id}", headers={"X-CSRF-Token": csrf}, json={"name": "new-name"}
    )
    assert resp.status_code == 403


def test_non_platform_admin_gets_403_on_platform_admin_route(
    client: TestClient, make_user, login_as
) -> None:
    regular_user = make_user()
    csrf = login_as(regular_user)
    resp = client.post(
        "/api/workspaces",
        headers={"X-CSRF-Token": csrf},
        json={"name": f"ws-{uuid.uuid4().hex[:8]}"},
    )
    assert resp.status_code == 403
