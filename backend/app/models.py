"""SQLAlchemy ORM models.

Status/role/category columns are plain VARCHAR with a Python-side ``enum.Enum``
(via ``sa.Enum(..., native_enum=False)``) plus a DB CHECK constraint for
defense in depth. Native Postgres ENUM types are avoided deliberately: adding a
value later would need ``ALTER TYPE ... ADD VALUE``, which can't run inside a
transaction in older Postgres and complicates Alembic migrations for no real
benefit here.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class MembershipRole(enum.StrEnum):
    admin = "admin"
    reviewer = "reviewer"


class VersionStatus(enum.StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class RuleCategory(enum.StrEnum):
    completeness = "completeness"
    validation = "validation"
    eligibility = "eligibility"
    risk = "risk"
    compliance = "compliance"
    business = "business"


class JobKind(enum.StrEnum):
    runtime = "runtime"
    test = "test"


class JobStatus(enum.StrEnum):
    queued = "QUEUED"
    processing = "PROCESSING"
    review_required = "REVIEW_REQUIRED"
    completed = "COMPLETED"
    failed = "FAILED"


class JobStage(enum.StrEnum):
    ocr = "OCR"
    extraction = "EXTRACTION"
    validation = "VALIDATION"
    rules = "RULES"
    scoring = "SCORING"
    summary = "SUMMARY"


class ErrorClass(enum.StrEnum):
    transient = "transient"
    permanent = "permanent"


class RiskLevel(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ReviewAction(enum.StrEnum):
    approve = "approve"
    reject = "reject"
    request_correction = "request_correction"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session_(Base):
    """Named Session_ to avoid clashing with sqlalchemy.orm.Session."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class LoginRateLimit(Base):
    """Per-IP sliding-window counter for login attempts (no Redis)."""

    __tablename__ = "login_rate_limits"

    ip: Mapped[str] = mapped_column(INET, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("role IN ('admin','reviewer')", name="ck_memberships_role"),)


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_templates_workspace_name"),)


class TemplateVersion(Base):
    __tablename__ = "template_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Denormalized from templates.workspace_id so the RLS policy (and most
    # queries) don't need a join/subquery through templates.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VersionStatus.draft.value
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    json_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rules: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    scoring: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    agent_generated: Mapped[bool] = mapped_column(default=False, nullable=False)
    human_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    human_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("template_id", "version_no", name="uq_versions_template_no"),
        CheckConstraint("status IN ('draft','published','archived')", name="ck_versions_status"),
        # Partial unique index (one draft per template) is created in the migration
        # since SQLAlchemy's declarative layer doesn't express partial uniques cleanly.
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("template_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    storage_key: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True, default=uuid.uuid4
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    supersedes_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Denormalized from documents.workspace_id, see TemplateVersion.workspace_id.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=JobKind.runtime.value)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.queued.value)
    stage: Mapped[str | None] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('runtime','test')", name="ck_jobs_kind"),
        CheckConstraint(
            "status IN ('QUEUED','PROCESSING','REVIEW_REQUIRED','COMPLETED','FAILED')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "stage IS NULL OR stage IN "
            "('OCR','EXTRACTION','VALIDATION','RULES','SCORING','SUMMARY')",
            name="ck_jobs_stage",
        ),
        CheckConstraint(
            "error_class IS NULL OR error_class IN ('transient','permanent')",
            name="ck_jobs_error_class",
        ),
        Index("ix_jobs_claim", "status", "next_run_at"),
    )


class Record(Base):
    __tablename__ = "records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Denormalized from jobs.workspace_id, see TemplateVersion.workspace_id.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("template_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocr: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_valid: Mapped[bool | None] = mapped_column()
    validation_errors: Mapped[list[Any] | None] = mapped_column(JSONB)
    rule_results: Mapped[list[Any] | None] = mapped_column(JSONB)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    risk_level: Mapped[str | None] = mapped_column(String(20))
    contributors: Mapped[list[Any] | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    model_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('low','medium','high')",
            name="ck_records_risk_level",
        ),
    )


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Denormalized from records.workspace_id, see TemplateVersion.workspace_id.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "action IN ('approve','reject','request_correction')", name="ck_reviews_action"
        ),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
