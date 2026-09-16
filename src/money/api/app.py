"""Thin control API. Enqueue/read only; research runs in a separate process."""

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from money.api.observability import configure_logging
from money.api.settings import Settings
from money.schemas.contracts import ResearchMandate, Ticker
from money.storage.store import (
    EnqueueRateExceeded,
    IdempotencyConflict,
    QueueCapacityExceeded,
    ResearchStore,
    StoreError,
)

logger = logging.getLogger(__name__)


class CreateResearchJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: Ticker
    mandate: ResearchMandate = Field(default_factory=ResearchMandate)


Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_hash: Hash
    credential_version: Hash


class CreateSessionRequest(SessionRequest):
    expires_at: datetime


class RateLimitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Hash
    limit: int = Field(default=5, ge=1, le=20)
    window_seconds: int = Field(default=60, ge=60, le=60)


class RequestBoundary:
    """Authenticate and bound actual streamed bytes before JSON parsing."""

    def __init__(self, app: ASGIApp, application: FastAPI) -> None:
        self.app = app
        self.application = application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings: Settings = self.application.state.settings
        headers = dict(scope.get("headers", []))
        started = time.monotonic()
        request_id = uuid4().hex
        public = scope.get("path") in {"/health", "/health/live", "/health/ready"}
        # Internal auth services always require the web service token, including dev.
        if not public and (
            not settings.money_allow_unauthenticated_dev
            or scope.get("path", "").startswith("/internal/")
            or scope.get("path") == "/research/system"
        ):
            token = settings.research_api_token
            expected = f"Bearer {token.get_secret_value()}".encode() if token else b""
            if not expected or not secrets.compare_digest(
                headers.get(b"authorization", b""), expected
            ):
                await JSONResponse(
                    {"detail": "Authentication required", "code": "AUTH_REQUIRED"}, status_code=401
                )(scope, receive, send)
                return
        body = bytearray()
        deadline = time.monotonic() + settings.money_request_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise TimeoutError
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError:
                await JSONResponse(
                    {"detail": "Request body deadline exceeded", "code": "REQUEST_TIMEOUT"},
                    status_code=408,
                )(scope, receive, send)
                return
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > settings.money_request_max_bytes:
                await JSONResponse(
                    {"detail": "Request too large", "code": "REQUEST_TOO_LARGE"}, status_code=413
                )(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        response_started = False

        async def response(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message.setdefault("headers", []).extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
                logger.info(
                    "api_request",
                    extra={
                        "request_id": request_id,
                        "status": message["status"],
                        "duration_seconds": time.monotonic() - started,
                    },
                )
            await send(message)

        try:
            await self.app(scope, replay, response)
        except Exception as error:  # noqa: BLE001 - prevent unsafe exception text reaching server logs
            logger.error(
                "api_failure",
                extra={"request_id": request_id, "failure_class": type(error).__name__},
            )
            if not response_started:
                await JSONResponse(
                    {"detail": "Research service unavailable", "code": "SERVICE_UNAVAILABLE"},
                    status_code=503,
                )(scope, replay, response)


def create_app(settings: Settings | None = None, store: ResearchStore | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        configured = settings or Settings()  # type: ignore[call-arg]
        application.state.settings = configured
        application.state.store = store or ResearchStore(
            configured.database_url.get_secret_value(),
            allow_sqlite=configured.money_env in {"development", "test"},
            pool_size=configured.money_db_pool_size,
            pool_timeout=configured.money_db_pool_timeout_seconds,
            statement_timeout_ms=configured.money_db_statement_timeout_ms,
        )
        yield
        if store is None:
            application.state.store.engine.dispose()

    application = FastAPI(
        title="Money research control API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(RequestBoundary, application=application)

    def repository(request: Request) -> ResearchStore:
        return request.app.state.store.for_workspace(request.app.state.settings.money_workspace_id)

    @application.exception_handler(RequestValidationError)
    async def invalid_input(_: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic errors can contain raw request input, including secrets. Do not echo it.
        return JSONResponse(
            {
                "detail": "Invalid research request",
                "code": "INVALID_REQUEST",
                "fields": [
                    ".".join(str(item) for item in entry["loc"]) for entry in error.errors()
                ],
            },
            status_code=422,
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, error: SQLAlchemyError) -> JSONResponse:
        logger.error("Research database unavailable: %s", type(error).__name__)
        return JSONResponse(
            {
                "detail": "Research storage is temporarily unavailable",
                "code": "STORAGE_UNAVAILABLE",
            },
            status_code=503,
        )

    @application.exception_handler(StoreError)
    async def invalid_operation(_: Request, error: StoreError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Research operation is not permitted", "code": "INVALID_OPERATION"},
            status_code=409,
        )

    @application.exception_handler(Exception)
    async def internal_error(_: Request, error: Exception) -> JSONResponse:
        logger.error("api_failure", extra={"failure_class": type(error).__name__})
        return JSONResponse(
            {"detail": "Research service unavailable", "code": "SERVICE_UNAVAILABLE"},
            status_code=503,
        )

    @application.post("/internal/auth/rate-limit")
    def auth_rate_limit(
        body: RateLimitRequest, request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        config = request.app.state.settings
        global_limit = db.consume_rate_limit(
            "login-global", config.money_login_global_per_minute, 60
        )
        if not global_limit["allowed"]:
            return global_limit
        return db.consume_rate_limit(
            f"login:{body.key}", min(body.limit, config.money_login_per_minute), 60
        )

    @application.post("/internal/auth/sessions")
    def create_session(
        body: CreateSessionRequest, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, bool]:
        db.create_session(body.token_hash, body.credential_version, body.expires_at)
        return {"created": True}

    @application.post("/internal/auth/sessions/validate")
    def validate_session(
        body: SessionRequest, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, bool]:
        return {"valid": db.session_valid(body.token_hash, body.credential_version)}

    @application.post("/internal/auth/sessions/revoke")
    def revoke_session(
        body: SessionRequest, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, bool]:
        db.revoke_session(body.token_hash, body.credential_version)
        return {"revoked": True}

    @application.post("/research/jobs", status_code=202)
    def create_job(
        body: CreateResearchJob, request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        config: Settings = request.app.state.settings
        if (
            config.money_env == "production"
            and db.health(config.money_research_mode)["worker"] != "ready"
        ):
            raise HTTPException(
                503, "Research compute is unavailable", headers={"Retry-After": "60"}
            )
        try:
            key = request.headers.get("Idempotency-Key")
            if key is not None and (
                not 1 <= len(key) <= 128
                or not key.isascii()
                or not all(c.isalnum() or c in "-_.:" for c in key)
            ):
                raise HTTPException(422, "Invalid idempotency key")
            return db.create_job(
                body.ticker,
                body.mandate,
                capacity=config.money_queue_capacity,
                rate_per_minute=config.money_enqueue_per_minute,
                idempotency_key=key,
                max_attempts=config.money_job_max_attempts,
            )
        except IdempotencyConflict as error:
            raise HTTPException(409, "Idempotency key conflicts with an earlier request") from error
        except QueueCapacityExceeded as error:
            raise HTTPException(
                503, "Research queue is at capacity", headers={"Retry-After": "60"}
            ) from error
        except EnqueueRateExceeded as error:
            raise HTTPException(
                429, "Research submission rate exceeded", headers={"Retry-After": "60"}
            ) from error

    @application.get("/research/jobs")
    def list_jobs(
        db: Annotated[ResearchStore, Depends(repository)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        return {"jobs": db.list_jobs(limit)}

    def require_job(db: ResearchStore, job_id: UUID) -> dict[str, Any]:
        job = db.get_job(str(job_id))
        if job is None:
            raise HTTPException(404, "Research job not found")
        return job

    @application.get("/research/jobs/{job_id}")
    def get_job(job_id: UUID, db: Annotated[ResearchStore, Depends(repository)]) -> dict[str, Any]:
        return require_job(db, job_id)

    @application.get("/research/jobs/{job_id}/reports")
    def get_reports(
        job_id: UUID, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        require_job(db, job_id)
        return db.public_reports(str(job_id))

    @application.get("/research/jobs/{job_id}/evidence")
    def get_evidence(
        job_id: UUID, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        require_job(db, job_id)
        return db.get_evidence(str(job_id))

    @application.get("/research/signals")
    def signals(
        db: Annotated[ResearchStore, Depends(repository)], expired: bool = False
    ) -> dict[str, Any]:
        return {"signals": db.list_signals(expired=expired)}

    @application.get("/research/outcomes")
    def outcomes(db: Annotated[ResearchStore, Depends(repository)]) -> dict[str, Any]:
        return {"outcomes": db.list_outcomes()}

    @application.get("/research/alerts")
    def alerts(db: Annotated[ResearchStore, Depends(repository)]) -> dict[str, Any]:
        from money.alerts.delivery import AlertOutbox

        return {"alerts": AlertOutbox(db).list_web()}

    @application.get("/research/discovery")
    def discovery(db: Annotated[ResearchStore, Depends(repository)]) -> dict[str, Any]:
        return {"candidates": db.list_discovery()}

    @application.get("/research/universe")
    def universe(db: Annotated[ResearchStore, Depends(repository)]) -> dict[str, Any]:
        return {"instruments": db.list_universe(), "coverage": "previously_researched_only"}

    @application.get("/research/system")
    def system(
        request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        config = request.app.state.settings
        return {
            **db.health(config.money_research_mode),
            **db.operational_metrics(include_details=True),
            "version": config.money_version,
            "git_sha": config.money_git_sha,
            "environment": config.money_env,
            "auth_mode": config.money_auth_mode,
        }

    @application.get("/health")
    def health(request: Request, db: Annotated[ResearchStore, Depends(repository)]) -> JSONResponse:
        config: Settings = request.app.state.settings
        try:
            result = db.health(config.money_research_mode)
            return JSONResponse(result, status_code=200 if result["status"] == "ok" else 503)
        except SQLAlchemyError:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "database": "unavailable",
                    "worker": "unknown",
                    "mode": config.money_research_mode,
                },
                status_code=503,
            )

    @application.get("/health/live")
    def live(request: Request) -> dict[str, str]:
        config = request.app.state.settings
        return {
            "status": "ok",
            "version": config.money_version,
            "git_sha": config.money_git_sha,
            "environment": config.money_env,
        }

    @application.get("/health/ready")
    def ready(request: Request, db: Annotated[ResearchStore, Depends(repository)]) -> JSONResponse:
        config = request.app.state.settings
        try:
            result = {
                **db.health(config.money_research_mode),
                **db.operational_metrics(),
                "version": config.money_version,
                "git_sha": config.money_git_sha,
                "environment": config.money_env,
                "auth_mode": config.money_auth_mode,
            }
            return JSONResponse(result, status_code=200 if result["status"] == "ok" else 503)
        except SQLAlchemyError:
            return JSONResponse(
                {"status": "unavailable", "database": "unavailable"}, status_code=503
            )

    return application


app = create_app()
