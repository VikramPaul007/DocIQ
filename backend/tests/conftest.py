from __future__ import annotations

import io
import random
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.main import app
from app.models import Membership, User
from app.security import hash_password


def minimal_pdf_with_text(text_content: str) -> bytes:
    """A hand-written, minimal-but-valid single-page PDF with a real text
    layer — pdfium can parse this without a correct xref table (it falls
    back to a linear scan), so no PDF-authoring library is needed in tests."""
    stream = f"BT /F1 24 Tf 10 100 Td ({text_content}) Tj ET".encode()
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF"
    )


def minimal_blank_pdf(pages: int = 1) -> bytes:
    """A valid PDF with no text layer at all — exercises the OCR-fallback
    path. Built with pypdfium2 itself rather than hand-written PDF syntax."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()

# Child-to-parent order so FK constraints don't block the cleanup even though
# most of them would CASCADE anyway.
_ALL_TABLES = [
    "reviews",
    "records",
    "jobs",
    "documents",
    "template_versions",
    "templates",
    "memberships",
    "workspaces",
    "audit_events",
    "login_rate_limits",
    "sessions",
    "users",
]

# dociq_app intentionally lacks DELETE/TRUNCATE on some of these (audit_events,
# reviews, template_versions — see migration 0001), so cleanup must run as the
# owner role, not through the app engine used by the rest of the tests.
_owner_engine = create_engine(settings.owner_dsn)


def _truncate_all() -> None:
    with _owner_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(_ALL_TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session", autouse=True)
def _clean_database() -> Iterator[None]:
    _truncate_all()
    yield
    _truncate_all()


@pytest.fixture()
def db() -> Iterator[Session]:
    """A DB session for arranging test fixtures. Runs with the platform-admin
    RLS bypass so tests can freely create workspaces/memberships/etc. without
    needing a real logged-in identity — the RLS-enforcement tests themselves
    (test_rls.py) open their own session with an explicit, non-admin context.

    Binds the Session to a single, explicitly-owned Connection (rather than
    the Engine) and sets `app.is_platform_admin` with a plain (non-local) SET:
    a transaction-scoped `set_config(..., true)` — what production request
    handling correctly uses, see app.db.set_rls_context — would revert on this
    fixture's first `db.commit()`, since tests commit more than once per test.
    Binding to a Connection (instead of letting the Session check in/out from
    the pool each transaction) is what guarantees that SET keeps applying to
    the *same* physical connection for every commit in the test.
    """
    connection = engine.connect()
    connection.execute(text("SET app.is_platform_admin = 'on'"))
    connection.commit()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
        session.commit()
    finally:
        session.close()
        # This connection goes back to the engine's pool on close(), and that
        # pool is shared with the app's own request-handling sessions (same
        # `engine`) — reset the bypass so a later request in this test run
        # can't accidentally inherit it from a reused pooled connection.
        connection.execute(text("RESET app.is_platform_admin"))
        connection.commit()
        connection.close()


@pytest.fixture()
def client() -> TestClient:
    # https:// base_url so httpx's cookie jar honours our Secure session cookie
    # across requests made with the same client (a plain http:// testserver
    # would silently drop it). A random per-test fake client IP keeps the
    # login rate limiter (shared Postgres state, not reset between tests)
    # from one test's attempts tripping another's.
    fake_ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    return TestClient(app, base_url="https://testserver", client=(fake_ip, 12345))


def unique_email(prefix: str = "user") -> str:
    # Not *.test/*.example/*.invalid: email-validator rejects those IANA
    # special-use TLDs outright, even without a deliverability check.
    return f"{prefix}-{uuid.uuid4().hex[:10]}@dociq-fixtures.com"


DEFAULT_TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def make_user(db: Session):
    def _make(*, is_platform_admin: bool = False, password: str = DEFAULT_TEST_PASSWORD) -> User:
        user = User(
            email=unique_email(),
            password_hash=hash_password(password),
            is_platform_admin=is_platform_admin,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture()
def login_as(client: TestClient):
    """Logs the given user in on ``client`` and returns the raw CSRF token.
    Subsequent calls on the same client must pass ``headers={"X-CSRF-Token": token}``
    for any non-GET request.
    """

    def _login(user: User, password: str = DEFAULT_TEST_PASSWORD) -> str:
        resp = client.post("/api/auth/login", json={"email": user.email, "password": password})
        assert resp.status_code == 200, resp.text
        return resp.json()["csrf_token"]

    return _login


@pytest.fixture()
def make_membership(db: Session):
    def _make(*, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str) -> Membership:
        membership = Membership(workspace_id=workspace_id, user_id=user_id, role=role)
        db.add(membership)
        db.commit()
        return membership

    return _make
