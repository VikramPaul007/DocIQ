"""Background worker: claims queued/retryable jobs one at a time via
``SELECT ... FOR UPDATE SKIP LOCKED``, advances them through the pipeline
stages in order, and commits each stage's output together with the advanced
``job.stage`` marker — a resumed job (crash, restart) re-enters right after
the last stage that actually committed.

Runs as the ``dociq_worker`` DB role (BYPASSRLS — see migration 0001), so no
RLS context needs to be set for any query here, unlike the API.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.audit import record_audit_event
from app.config import settings
from app.logging_config import configure_logging
from app.models import Document, Job, JobStage, JobStatus, Record, TemplateVersion
from app.pipeline import stages
from app.pipeline.stages import PermanentStageError, TransientStageError
from app.providers.llm import LlmProvider, OpenAICompatibleProvider
from app.providers.ocr import OcrProvider, PaddleOcrProvider
from app.providers.storage import get_document
from app.security import utcnow

logger = logging.getLogger("dociq.worker")

STAGE_ORDER = [
    JobStage.ocr,
    JobStage.extraction,
    JobStage.validation,
    JobStage.rules,
    JobStage.scoring,
    JobStage.summary,
]
LEASE_DURATION = timedelta(minutes=5)
RETRY_BACKOFF = [timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=8)]
POLL_INTERVAL_SECONDS = 1.0
WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"


def claim_job(db: Session) -> Job | None:
    now = utcnow()
    row = db.execute(
        text(
            """
            UPDATE jobs SET locked_by = :worker_id, locked_until = :locked_until
            WHERE id = (
                SELECT id FROM jobs
                WHERE status IN ('QUEUED', 'PROCESSING')
                  AND next_run_at <= :now
                  AND (locked_until IS NULL OR locked_until < :now)
                ORDER BY next_run_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        ),
        {"worker_id": WORKER_ID, "locked_until": now + LEASE_DURATION, "now": now},
    ).first()
    if row is None:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, row.id)


def _get_or_create_record(db: Session, job: Job, version_id: uuid.UUID) -> Record:
    record = db.query(Record).filter(Record.job_id == job.id).first()
    if record is not None:
        return record
    record = Record(
        workspace_id=job.workspace_id,
        job_id=job.id,
        document_id=job.document_id,
        template_version_id=version_id,
    )
    db.add(record)
    db.flush()
    return record


def _run_stage(
    stage: JobStage,
    record: Record,
    document: Document,
    version: TemplateVersion,
    llm: LlmProvider,
    ocr: OcrProvider,
) -> None:
    if stage == JobStage.ocr:
        document_bytes = get_document(document.storage_key)
        stages.run_ocr(record, document_bytes, ocr)
    elif stage == JobStage.extraction:
        stages.run_extraction(record, version, llm)
    elif stage == JobStage.validation:
        stages.run_validation(record, version)
    elif stage == JobStage.rules:
        stages.run_rules(record, version)
    elif stage == JobStage.scoring:
        stages.run_scoring(record, version)
    elif stage == JobStage.summary:
        stages.run_summary(record, llm)


def _handle_stage_failure(db: Session, job: Job, exc: Exception, *, error_class: str) -> None:
    db.rollback()  # discard this stage's partial writes; last committed stage stands
    job.last_error = str(exc)[:2000]
    job.error_class = error_class
    if error_class == "permanent" or job.attempts + 1 >= job.max_attempts:
        job.status = JobStatus.failed
        job.locked_until = None
        record_audit_event(
            db,
            actor_id=None,
            workspace_id=job.workspace_id,
            entity_type="job",
            entity_id=job.id,
            action="failed",
            data={"stage": job.stage, "error_class": error_class, "error": job.last_error},
        )
    else:
        job.attempts += 1
        job.next_run_at = utcnow() + RETRY_BACKOFF[min(job.attempts - 1, len(RETRY_BACKOFF) - 1)]
        job.status = JobStatus.queued
        job.locked_until = None
    db.commit()


def process_job(db: Session, job: Job, llm: LlmProvider, ocr: OcrProvider) -> None:
    document = db.get(Document, job.document_id)
    if document is None:
        job.status = JobStatus.failed
        job.error_class = "permanent"
        job.last_error = "document no longer exists"
        db.commit()
        return

    version_id = (
        uuid.UUID(job.config_snapshot["template_version_id"])
        if job.kind == "test" and job.config_snapshot
        else document.template_version_id
    )
    version = db.get(TemplateVersion, version_id)
    if version is None:
        job.status = JobStatus.failed
        job.error_class = "permanent"
        job.last_error = "template version no longer exists"
        db.commit()
        return

    record = _get_or_create_record(db, job, version_id)
    job.status = JobStatus.processing
    db.commit()

    start_index = STAGE_ORDER.index(JobStage(job.stage)) + 1 if job.stage else 0

    for stage in STAGE_ORDER[start_index:]:
        try:
            _run_stage(stage, record, document, version, llm, ocr)
        except TransientStageError as exc:
            _handle_stage_failure(db, job, exc, error_class="transient")
            return
        except PermanentStageError as exc:
            _handle_stage_failure(db, job, exc, error_class="permanent")
            return
        job.stage = stage.value
        db.commit()

    job.status = JobStatus.review_required
    record_audit_event(
        db,
        actor_id=None,
        workspace_id=job.workspace_id,
        entity_type="job",
        entity_id=job.id,
        action="review_required",
        data={
            "risk_level": record.risk_level,
            "score": str(record.score) if record.score else None,
        },
    )
    db.commit()


def main() -> None:
    configure_logging()
    engine = create_engine(settings.worker_dsn, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    llm = OpenAICompatibleProvider()
    ocr = PaddleOcrProvider()
    logger.info("worker_started", extra={"extra_fields": {"worker_id": WORKER_ID}})

    while True:
        db = session_factory()
        try:
            job = claim_job(db)
            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            logger.info("job_claimed", extra={"extra_fields": {"job_id": str(job.id)}})
            try:
                process_job(db, job, llm, ocr)
            except Exception:
                # Anything that escaped process_job's own handling (a bug, a
                # DB error mid-transaction): log it, drop the connection, and
                # let the lease expire — another claim picks the job back up
                # rather than looping forever on a session in a bad state.
                logger.exception("job_crashed", extra={"extra_fields": {"job_id": str(job.id)}})
                db.rollback()
        finally:
            db.close()


if __name__ == "__main__":
    main()
