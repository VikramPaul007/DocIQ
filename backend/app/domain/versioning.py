"""Draft/publish lifecycle for template versions — see "Business Rules for
Publishing" in docs/technical-implementation-plan.md. Rule/scoring validation
is added here once the rule engine lands (Milestone 5); for now publish only
re-checks the schema.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.orm import Session

from app.domain import schema_subset
from app.models import Template, TemplateVersion, VersionStatus
from app.security import utcnow


class PublishError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def get_or_create_draft(db: Session, template: Template) -> TemplateVersion:
    draft = (
        db.query(TemplateVersion)
        .filter(
            TemplateVersion.template_id == template.id,
            TemplateVersion.status == VersionStatus.draft,
        )
        .first()
    )
    if draft is not None:
        return draft

    latest = (
        db.query(TemplateVersion)
        .filter(TemplateVersion.template_id == template.id)
        .order_by(TemplateVersion.version_no.desc())
        .first()
    )
    draft = TemplateVersion(
        workspace_id=template.workspace_id,
        template_id=template.id,
        version_no=(latest.version_no + 1) if latest else 1,
        status=VersionStatus.draft,
        system_prompt=latest.system_prompt if latest else "",
        json_schema=latest.json_schema if latest else {},
        rules=latest.rules if latest else [],
        scoring=latest.scoring if latest else {},
        agent_generated=False,
    )
    db.add(draft)
    db.flush()
    return draft


def validate_version(version: TemplateVersion) -> list[str]:
    """Every check publish requires; also backs the standalone /validate
    endpoint so users can check before committing to publish."""
    return schema_subset.validate(version.json_schema)


def publish(db: Session, version: TemplateVersion, *, published_by: uuid.UUID) -> TemplateVersion:
    if version.status != VersionStatus.draft:
        raise PublishError(["Only a draft version can be published"])
    if version.agent_generated and version.human_reviewed_by is None:
        raise PublishError(
            ["An AI-generated configuration must be marked reviewed before publishing"]
        )

    errors = validate_version(version)
    if errors:
        raise PublishError(errors)

    content = {
        "system_prompt": version.system_prompt,
        "json_schema": version.json_schema,
        "rules": version.rules,
        "scoring": version.scoring,
    }
    version.content_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    version.status = VersionStatus.published
    version.published_by = published_by
    version.published_at = utcnow()
    db.flush()
    return version
