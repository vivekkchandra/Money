"""Thin control API. Enqueue/read only; research runs in a separate process."""

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from money.api.settings import Settings
from money.schemas.contracts import ResearchMandate, Ticker
from money.storage.store import (
    EnqueueRateExceeded,
    QueueCapacityExceeded,
    ResearchStore,
)

logger = logging.getLogger(__name__)


class CreateResearchJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: Ticker
    mandate: ResearchMandate = Field(default_factory=ResearchMandate)


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
        if scope.get("path") != "/health" and not settings.money_allow_unauthenticated_dev:
            token = settings.research_api_token
            expected = f"Bearer {token.get_secret_value()}".encode() if token else b""
            if not expected or not secrets.compare_digest(
                headers.get(b"authorization", b""), expected
            ):
                await JSONResponse({"detail": "Authentication required"}, status_code=401)(
                    scope, receive, send
                )
                return
        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > settings.money_request_max_bytes:
                await JSONResponse({"detail": "Request too large"}, status_code=413)(
                    scope, receive, send
                )
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return buffered.pop(0) if buffered else await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, store: ResearchStore | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configured = settings or Settings()  # type: ignore[call-arg]
        application.state.settings = configured
        application.state.store = store or ResearchStore(
            configured.database_url.get_secret_value(),
            allow_sqlite=configured.money_env in {"development", "test"},
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
        return request.app.state.store

    @application.exception_handler(RequestValidationError)
    async def invalid_input(_: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic errors can contain raw request input, including secrets. Do not echo it.
        return JSONResponse(
            {
                "detail": "Invalid research request",
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
            {"detail": "Research storage is temporarily unavailable"}, status_code=503
        )

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
            return db.create_job(
                body.ticker,
                body.mandate,
                capacity=config.money_queue_capacity,
                rate_per_minute=config.money_enqueue_per_minute,
            )
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

    @application.get("/research/discovery")
    def discovery(db: Annotated[ResearchStore, Depends(repository)]) -> dict[str, Any]:
        return {"candidates": db.list_discovery()}

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

    return application


app = create_app()
