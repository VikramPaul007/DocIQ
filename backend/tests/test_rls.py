"""Row-level security: the dociq_app role must not see another user's
workspaces even when queried directly with the ORM — the policy, not app code,
is what's enforced here."""

from __future__ import annotations

import uuid

from app.db import SessionLocal, set_rls_context
from app.models import Membership, Workspace


def test_rls_hides_other_workspaces_from_app_role(db, make_user) -> None:
    user_a = make_user()
    user_b = make_user()

    ws_a = Workspace(name=f"ws-a-{uuid.uuid4().hex[:8]}")
    ws_b = Workspace(name=f"ws-b-{uuid.uuid4().hex[:8]}")
    db.add_all([ws_a, ws_b])
    db.commit()

    db.add(Membership(workspace_id=ws_a.id, user_id=user_a.id, role="admin"))
    db.add(Membership(workspace_id=ws_b.id, user_id=user_b.id, role="admin"))
    db.commit()

    session = SessionLocal()
    try:
        set_rls_context(session, user_id=user_a.id, is_platform_admin=False)
        visible_ids = {row.id for row in session.query(Workspace).all()}
        assert ws_a.id in visible_ids
        assert ws_b.id not in visible_ids
    finally:
        session.rollback()
        session.close()


def test_platform_admin_bypasses_rls(db, make_user) -> None:
    admin = make_user(is_platform_admin=True)
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()

    session = SessionLocal()
    try:
        set_rls_context(session, user_id=admin.id, is_platform_admin=True)
        visible_ids = {row.id for row in session.query(Workspace).all()}
        assert ws.id in visible_ids
    finally:
        session.rollback()
        session.close()


def test_rls_hides_memberships_with_no_context_set(db, make_user) -> None:
    user_a = make_user()
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    db.add(Membership(workspace_id=ws.id, user_id=user_a.id, role="admin"))
    db.commit()

    session = SessionLocal()
    try:
        # No set_rls_context call: app.user_id is unset for this transaction.
        visible_ids = {row.id for row in session.query(Workspace).all()}
        assert ws.id not in visible_ids
    finally:
        session.rollback()
        session.close()
