"""Integration test of the worker's claim/process loop against the real DB,
running as the ``dociq_worker`` role (matching production), with Fake LLM/OCR
providers — no network calls.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import (
    Document,
    Job,
    JobStatus,
    Record,
    Template,
    TemplateVersion,
    VersionStatus,
    Workspace,
)
from app.pipeline.worker import claim_job, process_job
from app.providers.llm import FakeLlmProvider
from app.providers.ocr import FakeOcrProvider
from app.providers.storage import put_document
from tests.conftest import minimal_pdf_with_text

_worker_engine = create_engine(settings.worker_dsn)
_WorkerSession = sessionmaker(bind=_worker_engine, autoflush=False, expire_on_commit=False)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"name": {"type": "string"}},
}
RULES = [
    {
        "id": "has-name",
        "category": "completeness",
        "name": "n",
        "message": "m",
        "weight": 10,
        "condition": {"field": "name", "op": "exists"},
    }
]
SCORING = {"weights": {"has-name": 10}, "thresholds": {"low": 0, "medium": 5, "high": 20}}


@pytest.fixture(autouse=True)
def _clear_stray_queued_jobs(db):
    """claim_job() picks the globally oldest QUEUED job by design (a shared
    worker pool processing one FIFO queue) — exactly right in production, but
    it means a job some *other* test file's document-upload test created (and
    never processes) would otherwise get claimed here instead of this test's
    own job. Clear the slate first; doesn't affect other files' own already-
    completed assertions since test files run sequentially, not in parallel.
    """
    db.query(Job).filter(Job.status == JobStatus.queued).delete()
    db.commit()


def _setup_published_job(db, pdf_bytes: bytes | None = None) -> uuid.UUID:
    ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.commit()
    template = Template(workspace_id=ws.id, name="t1", purpose="p", document_type="pdf")
    db.add(template)
    db.commit()
    version = TemplateVersion(
        workspace_id=ws.id,
        template_id=template.id,
        version_no=1,
        status=VersionStatus.published,
        system_prompt="Extract the name.",
        json_schema=SCHEMA,
        rules=RULES,
        scoring=SCORING,
        content_hash="abc",
    )
    db.add(version)
    db.commit()

    storage_key = uuid.uuid4()
    put_document(storage_key, pdf_bytes or minimal_pdf_with_text("Name: Alice Example Person"))
    document = Document(
        workspace_id=ws.id,
        template_version_id=version.id,
        storage_key=storage_key,
        sha256="x",
        size=10,
        page_count=1,
        original_filename="a.pdf",
    )
    db.add(document)
    db.commit()
    job = Job(workspace_id=ws.id, document_id=document.id, kind="runtime", status=JobStatus.queued)
    db.add(job)
    db.commit()
    return job.id


def test_job_runs_to_completion(db) -> None:
    _setup_published_job(db)

    worker_db = _WorkerSession()
    try:
        job = claim_job(worker_db)
        assert job is not None
        process_job(worker_db, job, FakeLlmProvider([{"name": "Alice"}]), FakeOcrProvider())
        worker_db.refresh(job)
        assert job.status == "REVIEW_REQUIRED"
        record = worker_db.query(Record).filter(Record.job_id == job.id).first()
        assert record.extraction == {"name": "Alice"}
        assert record.rule_results[0]["passed"] is True  # name is present -> rule passes
        assert record.score == 0  # score counts only *failed* rules' weights
        assert record.risk_level == "low"
    finally:
        worker_db.close()


def test_transient_failure_retries_then_fails_then_manual_retry_recovers(db) -> None:
    job_id = _setup_published_job(db)

    class FlakyProvider:
        def complete_json(self, **kwargs):
            raise ConnectionError("simulated outage")

    worker_db = _WorkerSession()
    try:
        job = claim_job(worker_db)
        assert job is not None
        job.max_attempts = 1  # fail on the first attempt, for a fast test
        worker_db.commit()
        process_job(worker_db, job, FlakyProvider(), FakeOcrProvider())
        worker_db.refresh(job)
        assert job.status == "FAILED"
        assert job.error_class == "transient"
        assert "simulated outage" in job.last_error
    finally:
        worker_db.close()

    # Same reset the API's /jobs/{id}/retry performs.
    fixture_job = db.get(Job, job_id)
    fixture_job.status = JobStatus.queued
    fixture_job.attempts = 0
    fixture_job.locked_until = None
    db.commit()

    worker_db2 = _WorkerSession()
    try:
        claimed = claim_job(worker_db2)
        assert claimed is not None
        process_job(worker_db2, claimed, FakeLlmProvider([{"name": "Alice"}]), FakeOcrProvider())
        worker_db2.refresh(claimed)
        assert claimed.status == "REVIEW_REQUIRED"
    finally:
        worker_db2.close()


def test_resumed_job_skips_already_completed_stages(db) -> None:
    """Simulates a crash after OCR committed but before EXTRACTION: the next
    claim must not re-run OCR. Uses a blank (no text layer) PDF so OCR is
    actually invoked at all."""
    from tests.conftest import minimal_blank_pdf

    _setup_published_job(db, minimal_blank_pdf())

    class CountingOcr(FakeOcrProvider):
        def __init__(self) -> None:
            super().__init__(text="counted text")
            self.calls = 0

        def ocr_page(self, png_bytes: bytes):
            self.calls += 1
            return super().ocr_page(png_bytes)

    ocr = CountingOcr()

    worker_db = _WorkerSession()
    try:
        job = claim_job(worker_db)
        assert job is not None
        job_id = job.id

        class CrashAfterOcrProvider:
            def complete_json(self, **kwargs):
                raise ConnectionError("simulated crash mid-extraction")

        job.max_attempts = 5  # stays QUEUED (not FAILED) after this single failure
        worker_db.commit()
        process_job(worker_db, job, CrashAfterOcrProvider(), ocr)
        worker_db.refresh(job)
        assert job.status == "QUEUED"
        assert job.stage == "OCR"  # last *completed* stage
    finally:
        worker_db.close()

    assert ocr.calls == 1

    worker_db2 = _WorkerSession()
    try:
        job2 = worker_db2.get(Job, job_id)
        job2.next_run_at = job2.next_run_at.replace(year=2000)  # make it immediately claimable
        worker_db2.commit()
        claimed = claim_job(worker_db2)
        assert claimed is not None
        process_job(worker_db2, claimed, FakeLlmProvider([{"name": "Alice"}]), ocr)
        worker_db2.refresh(claimed)
        assert claimed.status == "REVIEW_REQUIRED"
    finally:
        worker_db2.close()

    assert ocr.calls == 1  # OCR was not re-run on resume
