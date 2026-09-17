"""Administrator-configured live assembly. No demo or provider fallback paths."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import ExitStack
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from uuid import uuid4

from pydantic import Field, PrivateAttr, TypeAdapter, model_validator

from money.adapters.eligibility import eligibility_failures
from money.adapters.native import NativeRunSettings
from money.adapters.native_process import BoundedNativeRunner, NativeProcessPolicy
from money.adapters.native_qlib import QlibNativeRunner
from money.adapters.native_qualitative import AIHedgeFundNativeRunner, TradingAgentsNativeRunner
from money.adapters.upstream import (
    AIHedgeFundAdapter,
    LeanAdapter,
    QlibAdapter,
    QlibRunner,
    QualitativeRunner,
    TradingAgentsAdapter,
)
from money.backtest.lean import (
    LeanContainerRunner,
    LeanContainerSettings,
    LeanCostAssumptions,
    LeanStudyParameters,
    LeanStudyQualification,
)
from money.crews.cio import CIOResult, CrewAICioAdapter, CrewAINativeRunner
from money.data.identifiers import InstrumentIdentifiers
from money.data.live_eligibility import EligibilityReview, Trading212LiveEligibilityService
from money.data.qualification import ProviderQualification
from money.data.quality.market import evaluate_market_quality
from money.data.resilience import ProviderCircuit
from money.data.uk.filing_documents import (
    CompaniesHouseFilingDocuments,
    FinancialCurrencyProof,
    ReviewedStorageHost,
)
from money.data.uk.live import CompaniesHouseProvider, EODHDProvider, Trading212MetadataProvider
from money.models.registry import ModelRegistry
from money.research.budgets import BudgetLimits
from money.research.inference_config import InferenceSelection as InferenceSelection
from money.risk.costs import CostApplicability
from money.scanner.discovery import DiscoveryPolicy, discover_snapshot
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Candidate,
    CIOAuditReport,
    Contract,
    DocumentFact,
    EvidenceRecord,
    FinancialFact,
    FirmReport,
    InstrumentMetadata,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    RedTeamReport,
    ResearchMandate,
    ResearchSnapshot,
    ResearchState,
    TradingAgentsResearchReport,
    content_hash,
    utc_now,
)
from money.signals.generation import SignalDesign, SignalPolicy, generate_signal
from money.storage import ResearchStore

if TYPE_CHECKING:
    from money.flows.research import ResearchRuntime


class VerifiedInstrument(Contract):
    metadata: InstrumentMetadata
    identifiers: InstrumentIdentifiers
    eligibility_proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    ethical_proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    spread_bps: Decimal = Field(ge=0, le=10000)
    spread_evidence: EvidenceRecord
    corporate_action_coverage_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    corporate_actions_complete: bool
    supplemental_evidence: tuple[EvidenceRecord, ...] = ()
    cost_applicability: CostApplicability
    archived_market_evidence: tuple[EvidenceRecord, ...] = Field(default=(), max_length=4000)
    archived_market_proof_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    filing_documents: tuple[FinancialCurrencyProof, ...] = Field(default=(), max_length=4)
    issuer_jurisdiction: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    issuer_jurisdiction_proof_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def reviewed_filing_identity(self) -> VerifiedInstrument:
        if (self.issuer_jurisdiction is None) != (self.issuer_jurisdiction_proof_hash is None):
            raise ValueError("ISSUER_JURISDICTION_PROOF_REQUIRED")
        filings = [proof.filing_id for proof in self.filing_documents]
        documents = [proof.document_content_hash for proof in self.filing_documents]
        if len(filings) != len(set(filings)) or len(documents) != len(set(documents)):
            raise ValueError("LIVE_FILING_DOCUMENT_DUPLICATE")
        if any(
            proof.company_number != self.identifiers.companies_house_number
            for proof in self.filing_documents
        ):
            raise ValueError("LIVE_FILING_COMPANY_MISMATCH")
        return self


def validate_invocation_budgets(
    selections: tuple[InferenceSelection, InferenceSelection, InferenceSelection],
    calls: int,
    limits: BudgetLimits,
    correspondence_challenges: int = 0,
) -> None:
    if not 0 <= correspondence_challenges <= 24:
        raise ValueError("LIVE_CORRESPONDENCE_CHALLENGE_LIMIT_INVALID")
    # UTF-8 bytes conservatively bound tokenized input plus protocol overhead.
    # Operators must deliberately budget the actual configured maximum; do not
    # silently reduce native roles or start a stage that cannot be admitted.
    reservations = tuple(
        calls * (item.maximum_prompt_bytes + item.max_output_tokens + 1024) for item in selections
    )
    correspondence = tuple(
        correspondence_challenges * 2 * count
        * (item.maximum_prompt_bytes + item.max_output_tokens + 1024)
        for item, count in zip(selections, (2, 1, 1), strict=True)
    )
    totals = tuple(base + extra for base, extra in zip(reservations, correspondence, strict=True))
    if (
        sum(totals) > min(limits.per_job, limits.per_candidate, limits.daily)
        or max(totals) > limits.per_agent
        or max(sum(reservations[:2]), reservations[2], sum(correspondence)) > limits.per_stage
    ):
        raise ValueError("LIVE_BUDGET_CONFIGURATION_INSUFFICIENT")
    for provider_model, limit in limits.per_model:
        required = sum(
            tokens
            for item, tokens in zip(selections, totals, strict=True)
            if f"{item.provider}/{item.model}" == provider_model
        )
        if required > limit:
            raise ValueError("LIVE_MODEL_BUDGET_CONFIGURATION_INSUFFICIENT")


class LiveManifest(Contract):
    version: Literal["money-live-v1"] = "money-live-v1"
    reviewed_by: str = Field(min_length=1)
    qualification_artifacts: tuple[tuple[str, str], ...]
    # Each (sha256, relative file path) references actual reviewed bytes.
    instruments: tuple[VerifiedInstrument, ...] = ()
    # Full reviewed catalogues can be released independently of the seed rows.
    # Both catalogue bytes and every per-instrument proof remain hash-pinned.
    instrument_catalog: tuple[str, str] | None = None
    _catalog_instruments: tuple[VerifiedInstrument, ...] | None = PrivateAttr(default=None)
    # Eligibility freezes the full qualified universe; expensive research may
    # have complete supplemental inputs for only a subset. Catalogue chunks
    # remain individually bounded and hash-pinned like every other proof.
    eligibility_catalogs: tuple[tuple[str, str], ...] = Field(default=(), max_length=512)
    _catalog_eligibility: tuple[EligibilityReview, ...] | None = PrivateAttr(default=None)
    universe_account_binding_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    universe_provenance: tuple[str, str] | None = None
    provider_qualifications: tuple[ProviderQualification, ...] = Field(min_length=1)
    market_credential_environment_variable: str = "EODHD_API_KEY"
    filings_credential_environment_variable: str = "COMPANIES_HOUSE_API_KEY"
    filing_document_storage_hosts: tuple[ReviewedStorageHost, ...] = Field(default=(), max_length=8)
    tradingagents: InferenceSelection
    ai_hedge_fund: InferenceSelection
    crewai: InferenceSelection
    native_timeout_seconds: int = Field(default=240, ge=30, le=1200)
    native_max_calls: int = Field(default=16, ge=4, le=32)
    native_egress_policy_verified: Literal[True]
    native_egress_verification_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    qlib_enabled: bool = Field(default=True, strict=True, exclude_if=lambda value: value is True)
    qlib_registry_id: str | None = Field(default=None, min_length=1)
    qlib_artifact_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    lean: LeanContainerSettings
    lean_costs: LeanCostAssumptions
    lean_parameters: LeanStudyParameters
    lean_qualification: LeanStudyQualification
    budgets: BudgetLimits
    signal_policy: SignalPolicy = Field(default_factory=SignalPolicy)
    discovery_policy: DiscoveryPolicy = Field(default_factory=DiscoveryPolicy)
    enable_native_cross_examination: bool = False
    cross_examination_maximum_challenges: int = Field(default=8, ge=1, le=24)

    @property
    def reviewed_instruments(self) -> tuple[VerifiedInstrument, ...]:
        if self.instrument_catalog is not None and self._catalog_instruments is None:
            raise ValueError("LIVE_INSTRUMENT_CATALOG_NOT_LOADED")
        return (*self.instruments, *(self._catalog_instruments or ()))

    @property
    def eligibility_reviews(self) -> tuple[EligibilityReview, ...]:
        """Full hash-verified universe, with legacy manifest compatibility."""
        if self.eligibility_catalogs:
            if self._catalog_eligibility is None:
                raise ValueError("LIVE_ELIGIBILITY_CATALOG_NOT_LOADED")
            return self._catalog_eligibility
        return tuple(
            EligibilityReview(
                metadata=item.metadata, identifiers=item.identifiers,
                eligibility_proof_hash=item.eligibility_proof_hash,
                ethical_proof_hash=item.ethical_proof_hash,
            )
            for item in self.reviewed_instruments
        )

    def validate_instrument_coverage(self, instruments: tuple[VerifiedInstrument, ...]) -> None:
        tickers = [item.metadata.ticker for item in instruments]
        broker_ids = [item.identifiers.trading212_id for item in instruments]
        if len(tickers) != len(set(tickers)) or len(broker_ids) != len(set(broker_ids)):
            raise ValueError("LIVE_MANIFEST_DUPLICATE_IDENTITY")
        if any(item.filing_documents for item in instruments):
            company_qualification = next(
                item for item in self.provider_qualifications if item.provider == "companies-house"
            )
            if not {"filing", "financial"} <= set(company_qualification.datasets):
                raise ValueError("LIVE_FILING_DOCUMENT_COVERAGE_MISSING")

    @model_validator(mode="after")
    def qualification_consistency(self) -> LiveManifest:
        if self.qlib_enabled:
            if self.qlib_registry_id is None or self.qlib_artifact_hash is None:
                raise ValueError("QLIB_ENABLED_REQUIRES_PROMOTED_MODEL")
        elif self.qlib_registry_id is not None or self.qlib_artifact_hash is not None:
            raise ValueError("QLIB_DISABLED_CANNOT_REFERENCE_MODEL")
        # A local transport is useful for development, never a public worker
        # qualification. This gate is independent of ambient environment flags.
        for selection in (self.tradingagents, self.ai_hedge_fund, self.crewai):
            selection.require_hosted()
        if not self.instruments and self.instrument_catalog is None:
            raise ValueError("LIVE_INSTRUMENT_CATALOG_REQUIRED")
        if len(self.eligibility_catalogs) != len(set(self.eligibility_catalogs)):
            raise ValueError("LIVE_ELIGIBILITY_CATALOG_DUPLICATE")
        if self.eligibility_catalogs and (
            self.universe_account_binding_sha256 is None or self.universe_provenance is None
        ):
            raise ValueError("LIVE_UNIVERSE_ACCOUNT_PROVENANCE_REQUIRED")
        if (self.universe_account_binding_sha256 is None) != (self.universe_provenance is None):
            raise ValueError("LIVE_UNIVERSE_ACCOUNT_PROVENANCE_REQUIRED")
        if self.lean_parameters.scenario_policy != self.signal_policy:
            raise ValueError("LEAN_SCENARIO_POLICY_MISMATCH")
        validate_invocation_budgets(
            (self.tradingagents, self.ai_hedge_fund, self.crewai),
            self.native_max_calls,
            self.budgets,
            self.cross_examination_maximum_challenges if self.enable_native_cross_examination else 0,
        )
        providers = [item.provider for item in self.provider_qualifications]
        if len(providers) != len(set(providers)):
            raise ValueError("LIVE_MANIFEST_DUPLICATE_IDENTITY")
        if not {"eodhd", "companies-house"} <= set(providers):
            raise ValueError("LIVE_MANIFEST_REQUIRED_PROVIDER_MISSING")
        required_datasets = {
            "eodhd": {"ohlcv", "corporate_action", "news"},
            "companies-house": {"filing"},
        }
        for provider in self.provider_qualifications:
            if not provider.datasets or not required_datasets.get(provider.provider, set()) <= set(
                provider.datasets
            ):
                raise ValueError("LIVE_MANIFEST_REQUIRED_DATASET_MISSING")
        storage_hosts = [review.host for review in self.filing_document_storage_hosts]
        if len(storage_hosts) != len(set(storage_hosts)):
            raise ValueError("FILING_STORAGE_HOST_DUPLICATE")
        self.validate_instrument_coverage(self.instruments)
        return self


class LiveProvenance(Contract):
    manifest_hash: str
    provider_qualifications: tuple[ProviderQualification, ...]
    qlib_enabled: bool = Field(default=True, strict=True, exclude_if=lambda value: value is True)
    qlib_artifact_hash: str | None
    qlib_registry_id: str | None
    lean_image: str
    cost_version: str
    native_egress_verification_hash: str
    model_selections: tuple[tuple[str, str, str], ...]


def read_qualification_bytes(root: Path, relative: str, maximum: int) -> bytes:
    """Bounded descriptor-relative read, rejecting symlinks in every artifact component.

    The configured bundle root is trusted; relative artifact paths are not. Open
    directories without following links, rather than check a path then reopen it.
    O_NONBLOCK prevents special files (including FIFOs) blocking before fstat.
    These Unix capabilities are required by the supported Linux/macOS runtimes.
    """
    path = Path(relative)
    if (
        not relative or path.is_absolute() or path.as_posix() != relative
        or "\\" in relative or any(part in {".", ".."} for part in path.parts)
        or not path.parts or maximum <= 0
    ):
        raise ValueError("QUALIFICATION_ARTIFACT_INVALID")
    try:
        with ExitStack() as stack:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(root, directory_flags)
            stack.callback(os.close, descriptor)
            for part in path.parts[:-1]:
                descriptor = os.open(part, directory_flags, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
            file_descriptor = os.open(
                path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            stream = stack.enter_context(os.fdopen(file_descriptor, "rb"))
            attributes = os.fstat(stream.fileno())
            if not stat.S_ISREG(attributes.st_mode) or attributes.st_size > maximum:
                raise ValueError("QUALIFICATION_ARTIFACT_INVALID")
            raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                raise ValueError("QUALIFICATION_ARTIFACT_INVALID")
            return raw
    except (OSError, ValueError) as error:
        raise ValueError("QUALIFICATION_ARTIFACT_INVALID") from error


def load_manifest(path: Path, expected_hash: str) -> LiveManifest:
    try:
        root = path.parent.resolve()
        raw = read_qualification_bytes(root, path.name, 5_000_000)
    except (ValueError, OSError, RuntimeError) as error:
        raise ValueError("LIVE_MANIFEST_INVALID") from error
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("LIVE_MANIFEST_HASH_MISMATCH")
    manifest = LiveManifest.model_validate_json(raw)
    verified = set()
    eligibility_parts: dict[tuple[str, str], tuple[EligibilityReview, ...]] = {}
    universe_provenance: dict[str, Any] | None = None
    for digest, relative in manifest.qualification_artifacts:
        artifact = read_qualification_bytes(root, relative, 2_000_000)
        if hashlib.sha256(artifact).hexdigest() != digest:
            raise ValueError("QUALIFICATION_ARTIFACT_HASH_MISMATCH")
        verified.add(digest)
        if manifest.instrument_catalog == (digest, relative):
            manifest._catalog_instruments = TypeAdapter(
                tuple[VerifiedInstrument, ...]
            ).validate_json(artifact)
        if (digest, relative) in manifest.eligibility_catalogs:
            part = TypeAdapter(tuple[EligibilityReview, ...]).validate_json(artifact)
            if not part:
                raise ValueError("LIVE_ELIGIBILITY_CATALOG_EMPTY")
            eligibility_parts[(digest, relative)] = part
        if manifest.universe_provenance == (digest, relative):
            provenance = json.loads(artifact)
            if not isinstance(provenance, dict):
                raise ValueError("LIVE_UNIVERSE_ACCOUNT_PROVENANCE_INVALID")
            universe_provenance = provenance
    instruments = manifest.reviewed_instruments
    if not instruments:
        raise ValueError("LIVE_INSTRUMENT_CATALOG_REQUIRED")
    manifest.validate_instrument_coverage(instruments)
    if manifest.universe_provenance is not None:
        if universe_provenance is None:
            raise ValueError("LIVE_UNIVERSE_ACCOUNT_PROVENANCE_NOT_LOADED")
        try:
            observed = datetime.fromisoformat(universe_provenance["retrieved_at"])
            now = utc_now()
            if (universe_provenance["credential_binding_sha256"] != manifest.universe_account_binding_sha256
                    or universe_provenance["account_context"] != "STOCKS_AND_SHARES_ISA"
                    or universe_provenance["retrieval_environment"] != "live"
                    or observed.utcoffset() is None
                    or not observed <= now < observed + timedelta(hours=24)
                    or universe_provenance["account_review_hash"] not in verified):
                raise ValueError("LIVE_UNIVERSE_ACCOUNT_PROVENANCE_INVALID")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("LIVE_UNIVERSE_ACCOUNT_PROVENANCE_INVALID") from error
    if manifest.eligibility_catalogs:
        if set(eligibility_parts) != set(manifest.eligibility_catalogs):
            raise ValueError("LIVE_ELIGIBILITY_CATALOG_NOT_LOADED")
        reviews = tuple(
            review for reference in manifest.eligibility_catalogs
            for review in eligibility_parts[reference]
        )
        if not reviews or len(reviews) > 100000:
            raise ValueError("LIVE_ELIGIBILITY_CATALOG_SIZE_INVALID")
        tickers = [review.metadata.ticker for review in reviews]
        broker_ids = [review.identifiers.trading212_id for review in reviews]
        economic_ids = [
            (review.identifiers.isin, review.identifiers.exchange, review.identifiers.quote_currency)
            for review in reviews
        ]
        if (len(tickers) != len(set(tickers)) or len(broker_ids) != len(set(broker_ids))
                or len(economic_ids) != len(set(economic_ids))):
            raise ValueError("LIVE_ELIGIBILITY_CATALOG_DUPLICATE_IDENTITY")
        now = utc_now()
        for review in reviews:
            review.identifiers.require_current(now)
            if eligibility_failures(review.metadata, ResearchMandate(), now):
                raise ValueError("LIVE_ELIGIBILITY_CATALOG_NOT_CURRENT")
        index = {review.metadata.ticker: review for review in reviews}
        for item in instruments:
            expected = EligibilityReview(
                metadata=item.metadata, identifiers=item.identifiers,
                eligibility_proof_hash=item.eligibility_proof_hash,
                ethical_proof_hash=item.ethical_proof_hash,
            )
            if index.get(item.metadata.ticker) != expected:
                raise ValueError("LIVE_ELIGIBILITY_CATALOG_INSTRUMENT_MISMATCH")
        manifest._catalog_eligibility = reviews
    required = {
        manifest.native_egress_verification_hash,
        manifest.lean_qualification.historical_eligibility_hash,
        manifest.lean_qualification.survivorship_audit_hash,
        manifest.lean_qualification.corporate_action_audit_hash,
        *(review.review_evidence_hash for review in manifest.filing_document_storage_hosts),
    }
    for item in instruments:
        required.update(
            (
                item.eligibility_proof_hash,
                item.ethical_proof_hash,
                item.corporate_action_coverage_hash,
            )
        )
        if (
            item.metadata.provider == "money-demo"
            or item.metadata.ticker != item.identifiers.ticker
            or item.metadata.quote_currency != item.identifiers.quote_currency
        ):
            raise ValueError("LIVE_INSTRUMENT_INVALID")
        if item.archived_market_evidence:
            if item.archived_market_proof_hash is None:
                raise ValueError("ARCHIVED_MARKET_PROOF_MISSING")
            required.add(item.archived_market_proof_hash)
        if item.issuer_jurisdiction is not None:
            if (item.issuer_jurisdiction == "GB"
                    or item.identifiers.companies_house_number is not None
                    or item.issuer_jurisdiction_proof_hash is None):
                raise ValueError("LIVE_FOREIGN_ISSUER_JURISDICTION_INVALID")
            required.add(item.issuer_jurisdiction_proof_hash)
        required.update(proof.evidence_hash for proof in item.filing_documents)
    for provider in manifest.provider_qualifications:
        if provider.qualification_report_hash:
            required.add(provider.qualification_report_hash)
    for review in manifest.eligibility_reviews:
        required.update((review.eligibility_proof_hash, review.ethical_proof_hash))
    if not required <= verified:
        raise ValueError("QUALIFICATION_ARTIFACT_MISSING")
    return manifest


def merge_archived_market(
    current: list[EvidenceRecord],
    archived: tuple[EvidenceRecord, ...],
    qualifications: dict[str, ProviderQualification],
    snapshot_id: str,
    instrument: InstrumentMetadata,
) -> list[EvidenceRecord]:
    """Explicit original-publication archive takes priority only without conflicts.

    This never backdates current provider observations. The independently reviewed
    archived record keeps its original publication/retrieval/freshness metadata.
    Current overlapping bars must agree exactly, including raw units and volume.
    """
    now = utc_now()
    original: dict[str, EvidenceRecord] = {}
    for record in archived:
        if (
            not isinstance(record.payload, PriceBar)
            or record.payload.currency != instrument.quote_currency
        ):
            raise ValueError("ARCHIVED_MARKET_IDENTITY_INVALID")
        qualification = qualifications.get(record.provider)
        if qualification is None:
            raise ValueError("ARCHIVED_MARKET_PROVIDER_UNQUALIFIED")
        qualification.require("ohlcv", now, historical=True)
        if (
            "GB" not in qualification.geography
            or "STOCK" not in qualification.instrument_types
            or instrument.quote_currency not in qualification.currencies
        ):
            raise ValueError("ARCHIVED_MARKET_COVERAGE_MISSING")
        if (
            record.observation_time < qualification.earliest_observation
            or record.conflicting
            or not record.available_at(now)
            or record.fresh_until <= now
        ):
            raise ValueError("ARCHIVED_MARKET_PIT_OR_FRESHNESS_INVALID")
        key = record.observation_time.date().isoformat()
        if key in original:
            raise ValueError("ARCHIVED_MARKET_DUPLICATE_SESSION")
        original[key] = EvidenceRecord.model_validate(
            record.model_dump() | {"snapshot_id": snapshot_id, "hash": ""}
        )
    result = list(original.values())
    for record in current:
        if isinstance(record.payload, PriceBar):
            previous = original.get(record.observation_time.date().isoformat())
            if previous is not None:
                if previous.payload != record.payload:
                    raise ValueError("ARCHIVED_MARKET_PROVIDER_CONFLICT")
                continue
        result.append(record)
    return result


class SnapshotSources(Protocol):
    """The data-only subset needed before native qualification can begin.

    A qualification operator may supply validated data sources without inventing
    a completed LiveManifest. Production still supplies its admitted manifest.
    """

    @property
    def reviewed_instruments(self) -> tuple[VerifiedInstrument, ...]: ...

    @property
    def provider_qualifications(self) -> tuple[ProviderQualification, ...]: ...

    @property
    def market_credential_environment_variable(self) -> str: ...

    @property
    def filings_credential_environment_variable(self) -> str: ...

    @property
    def filing_document_storage_hosts(self) -> tuple[ReviewedStorageHost, ...]: ...


class LiveSnapshotBuilder:
    def __init__(
        self, manifest: SnapshotSources, store: ResearchStore, *, qlib_enabled: bool = True
    ) -> None:
        self.manifest = manifest
        self.qlib_enabled = qlib_enabled
        self.circuit = ProviderCircuit(store)

    def __call__(self, instrument: InstrumentMetadata) -> ResearchSnapshot:
        item = next(
            (i for i in self.manifest.reviewed_instruments if i.metadata.ticker == instrument.ticker),
            None,
        )
        if item is None:
            raise ValueError("RESEARCH_INSTRUMENT_EVIDENCE_REQUIRED")
        now, snapshot_id = utc_now(), str(uuid4())
        qualifications = {q.provider: q for q in self.manifest.provider_qualifications}
        market = EODHDProvider(
            os.environ.get(self.manifest.market_credential_environment_variable, ""),
            qualifications["eodhd"],
        )
        records: list[EvidenceRecord] = []
        for dataset in ("ohlcv", "corporate_action", "news"):
            records.extend(
                self.circuit.call(
                    f"eodhd:{dataset}",
                    partial(market.fetch, item.identifiers, dataset, snapshot_id, now),
                )
            )
        if item.archived_market_evidence:
            records = merge_archived_market(
                records, item.archived_market_evidence, qualifications, snapshot_id, instrument
            )
        if item.identifiers.companies_house_number:
            filing = CompaniesHouseProvider(
                os.environ.get(self.manifest.filings_credential_environment_variable, "")
            )
            qualifications["companies-house"].require("filing", now)
            records.extend(
                self.circuit.call(
                    "companies-house:filing", lambda: filing.filings(item.identifiers, snapshot_id, now)
                )
            )
        elif (not item.issuer_jurisdiction or item.issuer_jurisdiction == "GB"
                or not item.issuer_jurisdiction_proof_hash):
            raise ValueError("ISSUER_JURISDICTION_PROOF_REQUIRED")
        if item.filing_documents:
            document_provider = CompaniesHouseFilingDocuments(
                os.environ.get(self.manifest.filings_credential_environment_variable, ""),
                qualifications["companies-house"],
                storage_hosts=self.manifest.filing_document_storage_hosts,
            )
            for selection in item.filing_documents:
                bundle = self.circuit.call(
                    "companies-house:filing-document",
                    partial(
                        document_provider.fetch,
                        item.identifiers,
                        selection.filing_id,
                        snapshot_id,
                        selection,
                    ),
                )
                records.extend(bundle.evidence)
                # Bounded safe projection is sealed by snapshot/decision-packet hashes.
                # Never mutate the factory-time manifest, copy raw bytes, or duplicate
                # all normalized facts into document metadata.
                provenance = bundle.model_dump(
                    mode="json", exclude={"raw_document", "evidence"}
                )
                records.append(
                    EvidenceRecord(
                        snapshot_id=snapshot_id,
                        source="Companies House document retrieval provenance",
                        provider="companies-house",
                        source_id=bundle.evidence[0].source_id,
                        canonical_source_id=bundle.evidence[0].canonical_source_id,
                        observation_time=bundle.retrieval_time,
                        publication_time=bundle.availability_time,
                        retrieval_time=bundle.retrieval_time,
                        fresh_until=min(record.fresh_until for record in bundle.evidence),
                        pit_safe=True,
                        critical=True,
                        payload=DocumentFact(
                            kind="filing",
                            title="Verified document retrieval provenance; raw redistribution prohibited",
                            excerpt=json.dumps(provenance, sort_keys=True, separators=(",", ":")),
                            url=bundle.metadata_url,
                        ),
                    )
                )
        for evidence in (*item.supplemental_evidence, item.spread_evidence):
            if evidence.provider not in qualifications:
                raise ValueError("PROVIDER_UNQUALIFIED")
            qualifications[evidence.provider].require(evidence.payload.kind, now)
            if evidence.fresh_until <= now or not evidence.available_at(now):
                raise ValueError("CRITICAL_DATA_STALE")
            records.append(
                EvidenceRecord.model_validate(
                    evidence.model_dump() | {"snapshot_id": snapshot_id, "hash": ""}
                )
            )
        if not any(r.payload.kind == "filing" for r in records):
            raise ValueError("CRITICAL_FILINGS_MISSING")
        if not any(
            r.payload.kind == "financial" and r.payload.metric != "spread_bps" for r in records
        ):
            raise ValueError("CRITICAL_FUNDAMENTALS_MISSING")
        spread = item.spread_evidence.payload
        if (
            not isinstance(spread, FinancialFact)
            or spread.metric != "spread_bps"
            or spread.unit != "bps"
            or spread.value != item.spread_bps
        ):
            raise ValueError("SPREAD_EVIDENCE_MISMATCH")
        created = utc_now()
        records.sort(key=lambda r: (r.observation_time, r.evidence_id))
        snapshot = ResearchSnapshot(
            snapshot_id=snapshot_id,
            ticker=instrument.ticker,
            created_at=created,
            price_cutoff=created,
            news_cutoff=created,
            filing_cutoff=created,
            fundamental_cutoff=created,
            instrument=instrument,
            evidence=tuple(records),
            qlib_enabled=self.qlib_enabled,
        )
        quality = evaluate_market_quality(
            snapshot,
            created,
            spread_bps=item.spread_bps,
            adjustment_basis="RAW",
            corporate_actions_complete=item.corporate_actions_complete,
        )
        if not quality.passed:
            raise ValueError("MARKET_QUALITY_FAILED:" + ",".join(quality.reasons))
        return snapshot


class DiscoveryQuantFirm:
    """Reuse objective quant inference without exposing it to qualitative firms."""

    firm = "qlib"

    def __init__(self, adapter: QlibAdapter) -> None:
        self.adapter = adapter
        self._key: tuple[str, str] | None = None
        self._report: QlibQuantResearchReport | None = None

    def research(
        self, mandate: ResearchMandate, snapshot: ResearchSnapshot
    ) -> QlibQuantResearchReport:
        key = (content_hash(mandate), snapshot.hash)
        if key != self._key or self._report is None:
            self._report = self.adapter.research(mandate, snapshot)
            self._key = key
        return self._report


def build_live_runtime(store: ResearchStore, manifest: LiveManifest) -> ResearchRuntime:
    from money.flows.research import ResearchRuntime
    from money.research.correspondence import LiveCorrespondence
    from money.research.qlib_mode import qlib_enabled
    from money.scanner.universe import UniverseSnapshotBuilder

    if "MONEY_QLIB_ENABLED" in os.environ and qlib_enabled(os.environ) != manifest.qlib_enabled:
        raise ValueError("LIVE_QLIB_MODE_DIFFERS_FROM_PINNED_MANIFEST")
    model = None
    if manifest.qlib_enabled:
        if manifest.qlib_registry_id is None or manifest.qlib_artifact_hash is None:
            raise ValueError("QLIB_ENABLED_REQUIRES_PROMOTED_MODEL")
        model = ModelRegistry(store).load_active(
            manifest.qlib_registry_id, manifest.qlib_artifact_hash, utc_now()
        )
    settings = NativeRunSettings(
        timeout_seconds=manifest.native_timeout_seconds, max_calls=manifest.native_max_calls
    )

    def wrapped(runner: Any, schema: Any, selection: InferenceSelection) -> BoundedNativeRunner:
        inference = selection.inference()
        return BoundedNativeRunner(
            runner,
            schema,
            NativeProcessPolicy(
                timeout_seconds=manifest.native_timeout_seconds,
                gateway_hosts=inference.allowed_network_hosts,
                gateway_port=inference.allowed_network_port,
            ),
        )

    trading = TradingAgentsAdapter(
        cast(
            QualitativeRunner,
            wrapped(
                TradingAgentsNativeRunner(manifest.tradingagents.inference(), settings),
                TradingAgentsResearchReport,
                manifest.tradingagents,
            ),
        )
    )
    hedge = AIHedgeFundAdapter(
        cast(
            QualitativeRunner,
            wrapped(
                AIHedgeFundNativeRunner(manifest.ai_hedge_fund.inference(), settings),
                AIHedgeFundResearchReport,
                manifest.ai_hedge_fund,
            ),
        )
    )
    quant = DiscoveryQuantFirm(
        QlibAdapter(
            cast(
                QlibRunner,
                BoundedNativeRunner(
                    QlibNativeRunner(model),
                    QlibQuantResearchReport,
                    NativeProcessPolicy(timeout_seconds=manifest.native_timeout_seconds),
                ),
            )
        )
    ) if model is not None else None
    cio = CrewAICioAdapter(
        wrapped(
            CrewAINativeRunner(manifest.crewai.inference(), settings, qualified_model=model),
            CIOResult,
            manifest.crewai,
        )
    )
    validator = LeanAdapter(
        LeanContainerRunner(
            manifest.lean,
            manifest.lean_costs,
            manifest.lean_parameters,
            manifest.lean_qualification,
        )
    )

    def signal_builder(
        research_id: str,
        mandate: ResearchMandate,
        snapshot: ResearchSnapshot,
        reports: tuple[FirmReport, ...],
        lean: LeanValidationReport,
        audit: CIOAuditReport,
        red_team: RedTeamReport,
        state: ResearchState,
    ) -> SignalDesign:
        item = next(i for i in manifest.reviewed_instruments if i.metadata.ticker == snapshot.ticker)
        issued_at = utc_now()
        if item.spread_evidence.fresh_until <= issued_at:
            return SignalDesign(
                policy_version=manifest.signal_policy.version, reasons=("SPREAD_EVIDENCE_STALE",)
            )
        quality = evaluate_market_quality(
            snapshot,
            issued_at,
            spread_bps=item.spread_bps,
            adjustment_basis="RAW",
            corporate_actions_complete=item.corporate_actions_complete,
        )
        return generate_signal(
            research_id,
            mandate,
            snapshot,
            reports,
            lean,
            audit,
            red_team,
            state=state,
            issued_at=issued_at,
            market_quality=quality,
            cost_applicability=item.cost_applicability,
            policy=manifest.signal_policy,
        )

    def discover(mandate: ResearchMandate, snapshot: ResearchSnapshot) -> Candidate:
        return discover_snapshot(
            snapshot, quant=quant.research(mandate, snapshot) if quant else None,
            policy=manifest.discovery_policy
        )

    def bound_reviews() -> tuple[EligibilityReview, ...]:
        if manifest.eligibility_catalogs:
            from money.qualification.universe import credential_binding

            if credential_binding(os.environ) != manifest.universe_account_binding_sha256:
                raise ValueError("LIVE_ISA_ACCOUNT_BINDING_MISMATCH")
        return manifest.eligibility_reviews

    # Check before even issuing metadata requests with a possibly different key.
    bound_reviews()
    eligibility = Trading212LiveEligibilityService(
        Trading212MetadataProvider(
            os.environ.get("TRADING212_API_KEY", ""),
            os.environ.get("TRADING212_API_SECRET", ""),
        ),
        bound_reviews,
    )

    def validate_sources() -> None:
        checked_at = utc_now()
        for qualification in manifest.provider_qualifications:
            for dataset in qualification.datasets:
                qualification.require(dataset, checked_at)

    snapshots = UniverseSnapshotBuilder(
        eligibility.get_isa_universe, bound_reviews,
        LiveSnapshotBuilder(manifest, store, qlib_enabled=manifest.qlib_enabled),
        validate_sources=validate_sources,
    )
    return ResearchRuntime(
        mode="live",
        qlib_enabled=manifest.qlib_enabled,
        eligibility=eligibility,
        snapshot_builder=snapshots,
        universe_context=snapshots.context_for,
        firms=(trading, hedge, quant) if quant is not None else (trading, hedge),
        validate=validator.validate,
        audit=cio.audit,
        red_team=cio.red_team,
        budget_limits=manifest.budgets,
        invocation_budgets=tuple(
            (
                name,
                selection.provider,
                selection.model,
                manifest.native_max_calls
                * (selection.maximum_prompt_bytes + selection.max_output_tokens + 1024),
            )
            for name, selection in (
                ("tradingagents", manifest.tradingagents),
                ("ai_hedge_fund", manifest.ai_hedge_fund),
                ("crewai", manifest.crewai),
            )
        ),
        cio_runtime=lambda: cio.last_result,
        signal_builder=signal_builder,
        discover=discover,
        cross_examine=LiveCorrespondence(store, manifest, model).examine
        if manifest.enable_native_cross_examination else None,
        provenance=LiveProvenance(
            manifest_hash=content_hash(manifest),
            provider_qualifications=manifest.provider_qualifications,
            qlib_enabled=manifest.qlib_enabled,
            qlib_artifact_hash=manifest.qlib_artifact_hash,
            qlib_registry_id=manifest.qlib_registry_id,
            lean_image=manifest.lean.image,
            cost_version=manifest.lean_costs.version,
            native_egress_verification_hash=manifest.native_egress_verification_hash,
            model_selections=tuple(
                (name, selection.provider, selection.model)
                for name, selection in (
                    ("tradingagents", manifest.tradingagents),
                    ("ai_hedge_fund", manifest.ai_hedge_fund),
                    ("crewai", manifest.crewai),
                )
            ),
        ),
    )
