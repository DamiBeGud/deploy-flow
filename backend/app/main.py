import logging
import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.identity import DevelopmentIdentityProvider
from app.api.routes import router
from app.config import settings
from app.persistence.database import Session

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
app = FastAPI(
    title="DeployFlow", version="0.3.0", description="Phase 3 — bounded repository analysis agent"
)
app.state.identity_provider = DevelopmentIdentityProvider()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT"],
    allow_headers=[
        "Content-Type",
        "Idempotency-Key",
        "Last-Event-ID",
        "X-Correlation-ID",
        "Authorization",
    ],
    expose_headers=["X-Correlation-ID"],
)


def problem(request, status, detail):
    return JSONResponse(
        {
            "type": "about:blank",
            "title": "Request failed",
            "status": status,
            "detail": detail,
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
        status_code=status,
        media_type="application/problem+json",
    )


@app.middleware("http")
async def correlation(request: Request, call_next):
    supplied = request.headers.get("X-Correlation-ID", "")
    request.state.correlation_id = (
        supplied if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", supplied) else str(uuid4())
    )
    try:
        response = await call_next(request)
    except Exception:
        log.error("request_failed correlation_id=%s", request.state.correlation_id)
        response = problem(request, 500, "Internal server error")
    response.headers["X-Correlation-ID"] = request.state.correlation_id
    log.info(
        "request method=%s status=%s correlation_id=%s",
        request.method,
        response.status_code,
        request.state.correlation_id,
    )
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return problem(request, exc.status_code, exc.detail)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Pydantic's default details may echo sensitive request values.
    fields = sorted(
        {
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("loc")
        }
    )
    log.warning(
        "request_validation_failed method=%s path=%s fields=%s correlation_id=%s",
        request.method,
        request.url.path,
        fields,
        getattr(request.state, "correlation_id", None),
    )
    return problem(
        request, 422, "Request validation failed; check the documented field constraints"
    )


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        with Session() as db:
            db.execute(text("SELECT 1 FROM alembic_version"))
    except Exception:
        raise HTTPException(503, "Database or migrations unavailable") from None
    return {"status": "ready", "analysis_mode": settings().resolved_analysis_mode}


app.include_router(router)
