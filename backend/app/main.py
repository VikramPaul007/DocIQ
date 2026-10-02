from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, users, workspaces
from app.config import settings
from app.db import engine
from app.logging_config import configure_logging
from app.providers.storage import ensure_bucket, get_s3_client, storage_healthy

configure_logging()
logger = logging.getLogger("dociq.api")

_docs_enabled = settings.env != "prod"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_bucket(get_s3_client())
    yield


app = FastAPI(
    title="DocIQ API",
    docs_url="/api/docs" if _docs_enabled else None,
    redoc_url="/api/redoc" if _docs_enabled else None,
    openapi_url="/api/openapi.json" if _docs_enabled else None,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request",
        extra={
            "extra_fields": {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            }
        },
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("unhandled_exception", extra={"extra_fields": {"request_id": request_id}})
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "request_id": request_id},
    )


@app.get("/api/health")
def health() -> dict[str, bool | str]:
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("health_check_db_failed")

    storage_ok = storage_healthy()
    return {
        "status": "ok" if (db_ok and storage_ok) else "degraded",
        "db": db_ok,
        "storage": storage_ok,
    }


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(workspaces.router)
