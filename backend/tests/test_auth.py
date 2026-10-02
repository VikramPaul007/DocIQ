from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import User
from app.security import MAX_FAILED_LOGINS
from tests.conftest import DEFAULT_TEST_PASSWORD


def test_login_success_sets_cookie_and_returns_csrf(client: TestClient, make_user) -> None:
    user: User = make_user()
    resp = client.post(
        "/api/auth/login", json={"email": user.email, "password": DEFAULT_TEST_PASSWORD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == user.email
    assert body["csrf_token"]
    assert "__Host-session" in resp.cookies


def test_login_wrong_password(client: TestClient, make_user) -> None:
    user: User = make_user()
    resp = client.post(
        "/api/auth/login", json={"email": user.email, "password": "wrong-password-x"}
    )
    assert resp.status_code == 401


def test_login_unknown_email_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": "nobody-xyz@dociq-fixtures.com", "password": "whatever12345"},
    )
    assert resp.status_code == 401


def test_account_locks_after_max_failed_logins(client: TestClient, make_user) -> None:
    user: User = make_user()
    for _ in range(MAX_FAILED_LOGINS):
        resp = client.post("/api/auth/login", json={"email": user.email, "password": "wrong"})
        assert resp.status_code == 401
    # Correct password no longer matters once locked out.
    resp = client.post(
        "/api/auth/login", json={"email": user.email, "password": DEFAULT_TEST_PASSWORD}
    )
    assert resp.status_code == 423


def test_me_requires_authentication(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_after_login(client: TestClient, make_user, login_as) -> None:
    user: User = make_user()
    login_as(user)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == user.email


def test_logout_without_csrf_header_is_rejected(client: TestClient, make_user, login_as) -> None:
    user: User = make_user()
    login_as(user)
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 403


def test_logout_with_wrong_csrf_token_is_rejected(client: TestClient, make_user, login_as) -> None:
    user: User = make_user()
    login_as(user)
    resp = client.post("/api/auth/logout", headers={"X-CSRF-Token": "not-the-real-token"})
    assert resp.status_code == 403


def test_logout_invalidates_the_session(client: TestClient, make_user, login_as) -> None:
    user: User = make_user()
    csrf = login_as(user)
    resp = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 204
    resp2 = client.get("/api/auth/me")
    assert resp2.status_code == 401


def test_change_password_requires_correct_current_password(
    client: TestClient, make_user, login_as
) -> None:
    user: User = make_user()
    csrf = login_as(user)
    resp = client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": "wrong", "new_password": "a-new-strong-password-123"},
    )
    assert resp.status_code == 401


def test_change_password_then_login_with_new_password(
    client: TestClient, make_user, login_as
) -> None:
    user: User = make_user()
    csrf = login_as(user)
    new_password = "a-new-strong-password-123"
    resp = client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": DEFAULT_TEST_PASSWORD, "new_password": new_password},
    )
    assert resp.status_code == 204

    resp2 = client.post("/api/auth/login", json={"email": user.email, "password": new_password})
    assert resp2.status_code == 200
