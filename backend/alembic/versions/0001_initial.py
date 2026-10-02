"""Initial schema: tables, row-level security, immutability triggers.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-02

Assumes the ``dociq_app`` and ``dociq_worker`` roles already exist (created by
``python -m app.cli bootstrap-roles``, which the ``migrate`` CLI command runs
before this). GRANTs below would fail on a role that doesn't exist yet.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        pg.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ---- users / sessions / rate limiting (no RLS: not workspace-scoped) ----
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", pg.CITEXT(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "sessions",
        _uuid_pk(),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", pg.INET()),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "login_rate_limits",
        sa.Column("ip", pg.INET(), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )

    # ---- workspace-scoped domain tables ----
    op.create_table(
        "workspaces",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column(
            "created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "memberships",
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('admin','reviewer')", name="ck_memberships_role"),
    )

    op.create_table(
        "templates",
        _uuid_pk(),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("document_type", sa.String(100), nullable=False),
        sa.Column(
            "created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("workspace_id", "name", name="uq_templates_workspace_name"),
    )

    op.create_table(
        "template_versions",
        _uuid_pk(),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "template_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("json_schema", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("rules", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("scoring", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("agent_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "human_reviewed_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("human_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column(
            "published_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("template_id", "version_no", name="uq_versions_template_no"),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_versions_status"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_versions_one_draft_per_template "
        "ON template_versions (template_id) WHERE status = 'draft'"
    )

    op.create_table(
        "documents",
        _uuid_pk(),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "template_version_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("template_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "storage_key",
            pg.UUID(as_uuid=True),
            nullable=False,
            unique=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column(
            "uploaded_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "supersedes_document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "jobs",
        _uuid_pk(),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False, server_default="runtime"),
        sa.Column("config_snapshot", pg.JSONB()),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("stage", sa.String(20)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(100)),
        sa.Column("last_error", sa.Text()),
        sa.Column("error_class", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
        sa.CheckConstraint("kind IN ('runtime','test')", name="ck_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('QUEUED','PROCESSING','REVIEW_REQUIRED','COMPLETED','FAILED')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "stage IS NULL OR stage IN "
            "('OCR','EXTRACTION','VALIDATION','RULES','SCORING','SUMMARY')",
            name="ck_jobs_stage",
        ),
        sa.CheckConstraint(
            "error_class IS NULL OR error_class IN ('transient','permanent')",
            name="ck_jobs_error_class",
        ),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "next_run_at"])
    op.create_index("ix_jobs_workspace", "jobs", ["workspace_id"])

    op.create_table(
        "records",
        _uuid_pk(),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "template_version_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("template_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ocr", pg.JSONB()),
        sa.Column("extraction", pg.JSONB()),
        sa.Column("extraction_valid", sa.Boolean()),
        sa.Column("validation_errors", pg.JSONB()),
        sa.Column("rule_results", pg.JSONB()),
        sa.Column("score", sa.Numeric(10, 4)),
        sa.Column("risk_level", sa.String(20)),
        sa.Column("contributors", pg.JSONB()),
        sa.Column("summary", sa.Text()),
        sa.Column("model_meta", pg.JSONB()),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('low','medium','high')",
            name="ck_records_risk_level",
        ),
    )
    op.create_index("ix_records_workspace", "records", ["workspace_id"])

    op.create_table(
        "reviews",
        _uuid_pk(),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "record_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "action IN ('approve','reject','request_correction')", name="ck_reviews_action"
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("workspace_id", pg.UUID(as_uuid=True)),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True)),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_events_workspace", "audit_events", ["workspace_id"])
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"])

    # ---- helper functions for RLS policies ----
    op.execute(
        """
        CREATE FUNCTION is_platform_admin() RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT current_setting('app.is_platform_admin', true) = 'on';
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION current_user_workspace_ids() RETURNS SETOF uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT workspace_id FROM memberships
          WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid;
        $$;
        """
    )

    # ---- row-level security ----
    workspace_scoped_tables = [
        "memberships",
        "templates",
        "template_versions",
        "documents",
        "jobs",
        "records",
        "reviews",
    ]
    op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspaces_isolation ON workspaces
        USING (is_platform_admin() OR id IN (SELECT current_user_workspace_ids()))
        WITH CHECK (is_platform_admin() OR id IN (SELECT current_user_workspace_ids()));
        """
    )
    for table in workspace_scoped_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
            USING (
              is_platform_admin() OR workspace_id IN (SELECT current_user_workspace_ids())
            )
            WITH CHECK (
              is_platform_admin() OR workspace_id IN (SELECT current_user_workspace_ids())
            );
            """
        )

    # audit_events gets its own split policy: a system-level event (workspace_id
    # IS NULL — login, logout, platform-admin actions) can never satisfy "workspace_id
    # IN (...)", so a combined USING/WITH CHECK policy would block every such INSERT
    # even though the actor is legitimately authenticated. Reads stay restricted
    # (platform admins see everything, members see their workspaces' events, no one
    # else sees workspace_id IS NULL rows); writes are always allowed because the
    # application — not the end user — fully controls what gets inserted here, and
    # the append-only trigger plus narrowed grants (SELECT, INSERT only) already
    # make the table tamper-evident.
    # Postgres also checks the SELECT policy when an INSERT ... RETURNING
    # needs to hand the new row back (SQLAlchemy always does this, to read
    # back server defaults like `id`/`at`) — so without the actor_id clause, a
    # user's own system-level event (workspace_id IS NULL: login, logout,
    # password change) would fail that RETURNING with the very same
    # "new row violates row-level security policy" error, even though the
    # INSERT itself is unconditionally allowed below.
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY audit_events_select ON audit_events
        FOR SELECT
        USING (
          is_platform_admin()
          OR workspace_id IN (SELECT current_user_workspace_ids())
          OR actor_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        );
        """
    )
    op.execute(
        """
        CREATE POLICY audit_events_insert ON audit_events
        FOR INSERT
        WITH CHECK (true);
        """
    )

    # ---- immutability / append-only triggers ----
    op.execute(
        """
        CREATE FUNCTION fn_block_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% on % is not allowed (append-only table)', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_no_update
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION fn_block_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reviews_no_update
        BEFORE UPDATE OR DELETE ON reviews
        FOR EACH ROW EXECUTE FUNCTION fn_block_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION fn_template_versions_immutable() RETURNS trigger AS $$
        BEGIN
          -- IS NOT DISTINCT FROM (not `=`): several of these columns are
          -- nullable, and SQL's three-valued logic makes `NULL = NULL`
          -- evaluate to NULL (not TRUE), which would make this whole AND
          -- chain NULL — and therefore never true — the moment any one of
          -- them happens to be NULL in both OLD and NEW.
          IF OLD.status = 'published' THEN
            IF TG_OP = 'UPDATE'
               AND NEW.status = 'archived'
               AND NEW.template_id IS NOT DISTINCT FROM OLD.template_id
               AND NEW.version_no IS NOT DISTINCT FROM OLD.version_no
               AND NEW.system_prompt IS NOT DISTINCT FROM OLD.system_prompt
               AND NEW.json_schema IS NOT DISTINCT FROM OLD.json_schema
               AND NEW.rules IS NOT DISTINCT FROM OLD.rules
               AND NEW.scoring IS NOT DISTINCT FROM OLD.scoring
               AND NEW.agent_generated IS NOT DISTINCT FROM OLD.agent_generated
               AND NEW.content_hash IS NOT DISTINCT FROM OLD.content_hash
               AND NEW.published_by IS NOT DISTINCT FROM OLD.published_by
               AND NEW.published_at IS NOT DISTINCT FROM OLD.published_at
            THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION
              'template_versions: published version % is immutable (only archiving allowed)',
              OLD.id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_template_versions_immutable
        BEFORE UPDATE ON template_versions
        FOR EACH ROW EXECUTE FUNCTION fn_template_versions_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_template_versions_no_delete
        BEFORE DELETE ON template_versions
        FOR EACH ROW EXECUTE FUNCTION fn_block_mutation();
        """
    )

    # ---- grants ----
    # dociq_app: subject to RLS above; full DML except where narrowed below.
    all_tables = [
        "users",
        "sessions",
        "login_rate_limits",
        "workspaces",
        "memberships",
        "templates",
        "template_versions",
        "documents",
        "jobs",
        "records",
        "reviews",
    ]
    for table in all_tables:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO dociq_app")
    # template_versions: app never deletes a version (immutability trigger guards
    # UPDATE already; revoking DELETE privilege removes the capability outright).
    op.execute("REVOKE DELETE ON template_versions FROM dociq_app")
    # reviews/audit_events: insert-only audit trail, no UPDATE/DELETE privilege at all.
    op.execute("GRANT SELECT, INSERT ON audit_events TO dociq_app")
    op.execute("REVOKE UPDATE, DELETE ON reviews FROM dociq_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dociq_app")
    op.execute("GRANT EXECUTE ON FUNCTION is_platform_admin() TO dociq_app")
    op.execute("GRANT EXECUTE ON FUNCTION current_user_workspace_ids() TO dociq_app")

    # dociq_worker: BYPASSRLS (set in bootstrap), narrower table grants.
    for table in ["documents", "jobs", "records", "template_versions"]:
        op.execute(f"GRANT SELECT ON {table} TO dociq_worker")
    for table in ["jobs", "records"]:
        op.execute(f"GRANT INSERT, UPDATE ON {table} TO dociq_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON documents TO dociq_worker")
    op.execute("GRANT SELECT, INSERT ON audit_events TO dociq_worker")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dociq_worker")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reviews CASCADE")
    op.execute("DROP TABLE IF EXISTS records CASCADE")
    op.execute("DROP TABLE IF EXISTS jobs CASCADE")
    op.execute("DROP TABLE IF EXISTS documents CASCADE")
    op.execute("DROP TABLE IF EXISTS template_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS templates CASCADE")
    op.execute("DROP TABLE IF EXISTS memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS workspaces CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
    op.execute("DROP TABLE IF EXISTS login_rate_limits CASCADE")
    op.execute("DROP TABLE IF EXISTS sessions CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP FUNCTION IF EXISTS fn_template_versions_immutable() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS fn_block_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS current_user_workspace_ids() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS is_platform_admin() CASCADE")
