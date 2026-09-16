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
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from money.accounts.api import current_principal
from money.accounts.api import router as account_router
from money.accounts.security import PasswordCapacityExceeded
from money.accounts.service import AccountError, AccountService
from money.api.observability import configure_logging
from money.api.settings import Settings
from money.data.instruments import (
    InstrumentCatalogue,
    InstrumentSearchPage,
    InstrumentSearchQuery,
    InstrumentSearchResult,
)
from money.data.security import ProviderFailure
from money.data.universe import IsaUniverseQuery, ReviewedIsaUniverse
from money.product.api import router as product_router
from money.product.api import safe_product_error
from money.product.billing import BillingUnavailable
from money.product.service import EntitlementDenied, ProductService
from money.product.settings import ProductSettings
from money.reference import (
    CommercialLicenceError,
    require_manifest_commercial_rights,
    require_snapshot_commercial_rights,
)
from money.research.objective import ObjectivePageQuery
from money.schemas.contracts import ResearchMandate, ResearchSnapshot, Ticker, utc_now
from money.storage import models as stored
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
        maximum_bytes = (
            262144
            if scope.get("path") == "/v1/product/billing/webhook"
            else settings.money_request_max_bytes
        )

        async def reject(payload: dict[str, Any], status: int) -> None:
            logger.info(
                "api_request",
                extra={
                    "request_id": request_id,
                    "status": status,
                    "duration_seconds": time.monotonic() - started,
                },
            )
            await JSONResponse(
                payload,
                status_code=status,
                headers={
                    "X-Request-ID": request_id,
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )(scope, receive, send)

        public = scope.get("path") in {
            "/health",
            "/health/live",
            "/health/ready",
            "/v1/product/billing/webhook",
        }
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
                await reject({"detail": "Authentication required", "code": "AUTH_REQUIRED"}, 401)
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
                await reject(
                    {"detail": "Request body deadline exceeded", "code": "REQUEST_TIMEOUT"}, 408
                )
                return
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > maximum_bytes:
                await reject({"detail": "Request too large", "code": "REQUEST_TOO_LARGE"}, 413)
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
        from money.reference import load_reference_catalog

        application.state.reference_catalog = load_reference_catalog()
        application.state.product_settings = ProductSettings()
        if (
            configured.money_env == "production"
            and configured.money_auth_mode == "saas"
            and application.state.product_settings.money_billing_enabled
            and not application.state.product_settings.money_stripe_live
        ):
            raise ValueError("Production billing requires explicitly configured Stripe live mode")
        application.state.live_manifest = None
        application.state.instrument_catalogue = None
        application.state.rnd_provider = None
        if configured.money_research_mode == "live_rnd":
            from money.data.rnd_market import YFinanceProvider

            application.state.rnd_provider = YFinanceProvider(
                timeout_seconds=configured.money_rnd_provider_timeout_seconds,
            )
        if configured.money_research_mode == "live":
            from money.research.live import load_manifest

            assert configured.money_live_manifest is not None
            assert configured.money_live_manifest_sha256 is not None
            manifest = load_manifest(
                configured.money_live_manifest, configured.money_live_manifest_sha256
            )
            if configured.money_auth_mode == "saas":
                require_manifest_commercial_rights(application.state.reference_catalog, manifest)
            application.state.live_manifest = manifest
            application.state.instrument_catalogue = InstrumentCatalogue.from_manifest(manifest)
        elif configured.money_env in {"development", "test"} and (
            configured.money_research_mode == "demo" or configured.money_enable_synthetic_demo
        ):
            application.state.instrument_catalogue = InstrumentCatalogue.demonstration(utc_now())
        application.state.settings = configured
        application.state.store = store or ResearchStore(
            configured.database_url.get_secret_value(),
            allow_sqlite=configured.money_env in {"development", "test"},
            pool_size=configured.money_db_pool_size,
            pool_timeout=configured.money_db_pool_timeout_seconds,
            statement_timeout_ms=configured.money_db_statement_timeout_ms,
        )
        if configured.money_auth_mode == "saas":
            application.state.accounts = AccountService(application.state.store, configured)
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
    application.include_router(account_router)
    application.include_router(product_router)

    def repository(request: Request) -> ResearchStore:
        config = request.app.state.settings
        if config.money_auth_mode == "saas" and not request.url.path.startswith("/health"):
            if request.url.path.startswith("/internal/"):
                raise AccountError(
                    "PRIVATE_AUTH_DISABLED", "Private authentication is disabled", 403
                )
            principal = current_principal(request)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                principal.require_write()
            request.state.principal = principal
            return request.app.state.store.for_workspace(principal.workspace_id)
        return request.app.state.store.for_workspace(request.app.state.settings.money_workspace_id)

    @application.exception_handler(AccountError)
    async def account_error(_: Request, error: AccountError) -> JSONResponse:
        return JSONResponse({"detail": error.detail, "code": error.code}, status_code=error.status)

    @application.exception_handler(PasswordCapacityExceeded)
    async def password_capacity(_: Request, error: PasswordCapacityExceeded) -> JSONResponse:
        return JSONResponse(
            {"detail": "Sign-in is busy. Please try again shortly", "code": "AUTH_CAPACITY"},
            status_code=429,
            headers={"Retry-After": "5"},
        )

    @application.exception_handler(CommercialLicenceError)
    async def licence_error(_: Request, error: CommercialLicenceError) -> JSONResponse:
        return JSONResponse(
            {
                "code": "MISSING_LICENCE",
                "detail": "This research is temporarily unavailable while data access rights are reviewed.",
            },
            status_code=503,
        )

    def require_result_rights(
        request: Request, db: ResearchStore, job_ids: list[str], *, require_snapshot: bool = True
    ) -> None:
        if not job_ids:
            return
        config: Settings = request.app.state.settings
        with db.engine.connect() as connection:
            personal_ids = set(connection.scalars(select(stored.jobs.c.id).where(
                stored.jobs.c.workspace_id == db.workspace_id,
                stored.jobs.c.id.in_(job_ids), stored.jobs.c.research_kind == "live_rnd",
            )))
        if personal_ids:
            if config.money_research_mode != "live_rnd" or config.money_env not in {
                "development", "test",
            }:
                raise CommercialLicenceError("PERSONAL_RND_NOT_FOR_COMMERCIAL_DISPLAY")
            job_ids = [job_id for job_id in job_ids if job_id not in personal_ids]
        if config.money_auth_mode != "saas" or not job_ids:
            return
        # One bounded batch query, scoped by BOTH membership workspace and returned job IDs.
        if len(job_ids) > 100:
            raise AccountError("PAGE_REQUIRED", "Please use paginated research history", 400)
        with db.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        stored.snapshots.c.job_id, stored.snapshots.c.payload, stored.jobs.c.ticker
                    )
                    .join(stored.jobs, stored.jobs.c.id == stored.snapshots.c.job_id)
                    .where(
                        stored.jobs.c.workspace_id == db.workspace_id, stored.jobs.c.id.in_(job_ids)
                    )
                )
                .mappings()
                .all()
            )
        if require_snapshot and {row["job_id"] for row in rows} != set(job_ids):
            raise CommercialLicenceError("MISSING_LICENCE")
        config = request.app.state.settings
        for row in rows:
            require_snapshot_commercial_rights(
                request.app.state.reference_catalog,
                ResearchSnapshot.model_validate(row["payload"]),
                allow_synthetic=(
                    config.money_env in {"development", "test"}
                    and config.money_enable_synthetic_demo
                    and row["ticker"] == "DEMO.L"
                ),
            )

    @application.exception_handler(EntitlementDenied)
    @application.exception_handler(BillingUnavailable)
    async def product_error(_: Request, error: Exception) -> JSONResponse:
        status, payload = safe_product_error(error)
        return JSONResponse(payload, status_code=status)

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

    def current_catalogue(request: Request) -> InstrumentCatalogue:
        config: Settings = request.app.state.settings
        catalogue: InstrumentCatalogue | None = request.app.state.instrument_catalogue
        if catalogue is None or (
            catalogue.mode == "demo"
            and (
                config.money_env not in {"development", "test"}
                or config.money_research_mode == "live"
                or not (config.money_research_mode == "demo" or config.money_enable_synthetic_demo)
            )
        ):
            raise AccountError(
                "INSTRUMENT_SEARCH_UNAVAILABLE", "Company search is temporarily unavailable", 503
            )
        if catalogue.mode == "live" and config.money_auth_mode == "saas":
            require_manifest_commercial_rights(
                request.app.state.reference_catalog, request.app.state.live_manifest
            )
        try:
            catalogue.require_current(utc_now())
        except ValueError as error:
            raise AccountError(
                "INSTRUMENT_SEARCH_UNAVAILABLE", "Company search is temporarily unavailable", 503
            ) from error
        return catalogue

    def rnd_search(
        request: Request, db: ResearchStore, query: InstrumentSearchQuery,
    ) -> InstrumentSearchPage:
        from money.data.rnd_cache import RndProviderCache

        provider = request.app.state.rnd_provider
        if provider is None:
            raise AccountError("RND_UNAVAILABLE", "Personal R&D is not configured", 503)
        try:
            data = RndProviderCache(db).get(
                "yfinance-search", query.query.casefold(),
                lambda: provider.search(query.query, limit=20).model_dump(mode="json"),
                ttl_seconds=300, lease_seconds=10,
            )
        except ProviderFailure as error:
            raise AccountError(
                error.code if error.code in {"PROVIDER_RATE_LIMITED", "INSTRUMENT_NOT_FOUND"}
                else "MARKET_DATA_UNAVAILABLE",
                "Public market-data search is unavailable. Please try again later.",
                429 if error.code == "PROVIDER_RATE_LIMITED" else 503,
            ) from error
        items = data["instruments"]
        return InstrumentSearchPage(
            instruments=tuple(InstrumentSearchResult(
                instrument_id=item["instrument_id"], ticker=item["ticker"],
                company=item["company"], exchange=item["exchange"],
                currency=item.get("currency") or "UNKNOWN", eligibility="UNKNOWN",
                research_allowed=True, verified_at=data["retrieved_at"], synthetic=False,
                canonical_symbol=item.get("canonical_symbol"),
                provider_symbol=item.get("provider_symbol"), country=item.get("listing_country"),
                instrument_type=item.get("instrument_type"),
            ) for item in items[query.offset:query.offset + query.limit]),
            total=len(items), limit=query.limit, offset=query.offset,
            coverage="public_provider", mode="live_rnd",
        )

    @application.get("/research/instruments", response_model=InstrumentSearchPage)
    def search_instruments(
        request: Request,
        db: Annotated[ResearchStore, Depends(repository)],
        query: Annotated[InstrumentSearchQuery, Query()],
    ) -> InstrumentSearchPage:
        if len(request.query_params.multi_items()) != len(request.query_params):
            raise AccountError("INVALID_REQUEST", "Use each search parameter once", 422)
        # Persisted limits span API replicas; the user bucket also spans workspaces.
        buckets = [(db, "instrument-search", 120)]
        if request.app.state.settings.money_auth_mode == "saas":
            principal = request.state.principal
            buckets.append(
                (
                    request.app.state.store.for_workspace("instrument-search-users"),
                    principal.user_id,
                    60,
                )
            )
        for scoped, key, limit in buckets:
            result = scoped.consume_rate_limit(key, limit, 60)
            if not result["allowed"]:
                raise HTTPException(
                    429,
                    "Company search rate exceeded",
                    headers={"Retry-After": str(result["retry_after"])},
                )
        if request.app.state.settings.money_research_mode == "live_rnd":
            return rnd_search(request, db, query)
        return current_catalogue(request).search(query, utc_now())

    @application.post("/research/jobs", status_code=202)
    def create_job(
        body: CreateResearchJob, request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        config: Settings = request.app.state.settings
        if config.money_research_mode == "live_rnd":
            if body.ticker == "DEMO.L":
                raise AccountError("SYNTHETIC_DATA_FORBIDDEN", "R&D requires genuine data", 422)
            matches = rnd_search(request, db, InstrumentSearchQuery(query=body.ticker, limit=20))
            if not any(item.ticker == body.ticker for item in matches.instruments):
                raise AccountError("INSTRUMENT_NOT_FOUND", "Select a matching listed company", 422)
        if config.money_research_mode == "live":
            failures = current_catalogue(request).admission_failures(
                body.ticker, body.mandate, utc_now()
            )
            if failures:
                raise AccountError(
                    "INSTRUMENT_NOT_RESEARCHABLE",
                    "This company does not currently meet the research requirements. "
                    "Please select an eligible company. No research allowance was used.",
                    422,
                )
        if config.money_auth_mode == "saas":
            if body.ticker == "DEMO.L" and (
                config.money_env not in {"development", "test"}
                or not config.money_enable_synthetic_demo
            ):
                raise AccountError(
                    "DEMO_UNAVAILABLE", "The synthetic demonstration is unavailable", 503
                )
            if body.ticker != "DEMO.L" and config.money_research_mode not in {"live", "live_rnd"}:
                raise AccountError(
                    "RESEARCH_UNAVAILABLE", "Live research qualification is unavailable", 503
                )
        if (
            (config.money_env == "production" or config.money_deployment_env == "hosted")
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
                research_kind="live_rnd" if config.money_research_mode == "live_rnd" else "standard",
                admission=(
                    lambda connection, job_id: ProductService(
                        db, request.app.state.product_settings
                    ).reserve_research(
                        connection, job_id, current_principal(request), body.ticker,
                        personal_rnd=config.money_research_mode == "live_rnd",
                    )
                )
                if config.money_auth_mode == "saas"
                else None,
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

    def require_job(request: Request, db: ResearchStore, job_id: UUID) -> dict[str, Any]:
        job = db.get_job(str(job_id))
        if job is None:
            raise HTTPException(404, "Research job not found")
        require_result_rights(request, db, [str(job_id)], require_snapshot=bool(job.get("packet")))
        return job

    @application.get("/research/jobs/{job_id}")
    def get_job(
        job_id: UUID, request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        return require_job(request, db, job_id)

    @application.get("/research/jobs/{job_id}/reports")
    def get_reports(
        job_id: UUID, request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        job = require_job(request, db, job_id)
        require_result_rights(request, db, [str(job_id)])
        if job["research_kind"] == "live_rnd":
            return {
                "runtime": "live_rnd", "locked": False, "reports": {},
                "components": (job.get("packet") or {}).get("components", []),
                "reason": "NATIVE_RESEARCH_INCOMPLETE",
            }
        return db.public_reports(str(job_id))

    @application.get("/research/jobs/{job_id}/evidence")
    def get_evidence(
        job_id: UUID, request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        require_job(request, db, job_id)
        require_result_rights(request, db, [str(job_id)])
        return db.get_evidence(str(job_id))

    @application.get("/research/signals")
    def signals(
        request: Request, db: Annotated[ResearchStore, Depends(repository)], expired: bool = False
    ) -> dict[str, Any]:
        result = db.list_signals(expired=expired)
        require_result_rights(request, db, [item["job_id"] for item in result])
        return {"signals": result}

    @application.get("/research/objective")
    def objective(
        request: Request, db: Annotated[ResearchStore, Depends(repository)],
        query: Annotated[ObjectivePageQuery, Query()],
    ) -> dict[str, Any]:
        from money.research.objective import AsymmetryAnalyzer, rank_opportunities
        from money.schemas.contracts import DecisionPacket
        from money.signals.generation import SignalDesign

        if request.app.state.settings.money_research_mode != "live":
            raise AccountError("LIVE_RESEARCH_REQUIRED", "The objective compares qualified live research only", 409)
        if len(request.query_params.multi_items()) != len(request.query_params):
            raise AccountError("INVALID_REQUEST", "Use each page parameter once", 422)
        budget = db.consume_rate_limit("objective-read", 30, 60)
        if not budget["allowed"]:
            raise HTTPException(429, "Please wait before refreshing", headers={"Retry-After": "60"})
        rows = db.objective_publications(limit=query.limit, offset=query.offset)
        page = rows[:query.limit]
        require_result_rights(request, db, [row["job_id"] for row in page])
        now = utc_now()
        assessments = []
        for row in page:
            assessment = AsymmetryAnalyzer.assess(
                DecisionPacket.model_validate(row["packet"]),
                SignalDesign.model_validate(row["design"]), now,
                invalidated=row["invalidated_at"] is not None,
            )
            if assessment is not None:
                assessments.append(assessment)
        return rank_opportunities(
            tuple(assessments), now=now, examined=len(page), offset=query.offset,
            has_more=len(rows) > query.limit,
        ).model_dump(mode="json")

    @application.get("/research/outcomes")
    def outcomes(
        request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        result = db.list_outcomes()
        require_result_rights(request, db, [item["job_id"] for item in result])
        return {"outcomes": result}

    @application.get("/research/alerts")
    def alerts(db: Annotated[ResearchStore, Depends(repository)]) -> dict[str, Any]:
        from money.alerts.delivery import AlertOutbox

        return {"alerts": AlertOutbox(db).list_web()}

    @application.get("/research/discovery")
    def discovery(
        request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        result = db.list_discovery()
        require_result_rights(request, db, [item["job_id"] for item in result])
        return {"candidates": result}

    @application.get("/research/universe")
    def universe(
        request: Request, db: Annotated[ResearchStore, Depends(repository)],
        query: Annotated[IsaUniverseQuery, Query()],
    ) -> dict[str, Any]:
        if request.app.state.settings.money_research_mode == "live":
            if len(request.query_params.multi_items()) != len(request.query_params):
                raise AccountError("INVALID_REQUEST", "Use each page parameter once", 422)
            rate = db.consume_rate_limit("isa-universe", 60, 60)
            if not rate["allowed"]:
                raise HTTPException(429, "Please wait before refreshing", headers={"Retry-After": "60"})
            try:
                return ReviewedIsaUniverse(current_catalogue(request)).page(
                    query, mandate=ResearchMandate(), now=utc_now(),
                ).model_dump(mode="json")
            except ValueError as error:
                raise AccountError(
                    "UNIVERSE_UNAVAILABLE", "Verified ISA coverage is temporarily unavailable", 503,
                ) from error
        result = db.list_universe()
        require_result_rights(request, db, [item["job_id"] for item in result])
        return {"instruments": result, "coverage": "previously_researched_only"}

    @application.get("/research/system")
    def system(
        request: Request, db: Annotated[ResearchStore, Depends(repository)]
    ) -> dict[str, Any]:
        config = request.app.state.settings
        rnd_status: dict[str, Any] = {}
        if config.money_research_mode == "live_rnd":
            from money.data.rnd_cache import provider_diagnostics

            rnd_status = {"rnd_providers": provider_diagnostics(db)}
        if config.money_auth_mode == "saas":
            principal = current_principal(request)
            principal.require_admin()
            return {
                **db.health(config.money_research_mode),
                "version": config.money_version,
                "environment": config.money_env,
                "auth_mode": config.money_auth_mode,
                **rnd_status,
            }
        return {
            **db.health(config.money_research_mode),
            **db.operational_metrics(include_details=True),
            "version": config.money_version,
            "git_sha": config.money_git_sha,
            "environment": config.money_env,
            "auth_mode": config.money_auth_mode,
            **rnd_status,
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
            if config.money_auth_mode == "saas":
                with db.engine.connect() as connection:
                    metrics = {
                        "schema_revision": connection.scalar(
                            text("SELECT version_num FROM alembic_version")
                        )
                        or "unknown"
                    }
            else:
                metrics = db.operational_metrics()
            result = {
                **db.health(config.money_research_mode),
                **metrics,
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
