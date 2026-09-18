"""Money-owned research contracts. No broker execution objects belong here."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def content_hash(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def required_first_pass_firms(qlib_enabled: bool = True) -> frozenset[str]:
    """The explicitly selected mode, never an unavailable-runtime fallback."""
    return frozenset({"tradingagents", "ai_hedge_fund", *({"qlib"} if qlib_enabled else set())})


EXCLUDED_ACTIVITIES = (
    "defence",
    "weapons",
    "firearms",
    "material_military_contracting",
    "oil_exploration",
    "oil_production",
    "integrated_oil",
    "oil_refining",
    "oil_services",
)
Ticker = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9._-]*$")]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
Fraction = Annotated[float, Field(ge=0, le=1)]


class ResearchMandate(Contract):
    version: int = Field(default=1, ge=1)
    broker: Literal["Trading212"] = "Trading212"
    # Read legacy mandates without changing their sealed bytes. Account type is
    # not a research qualification and new mandates make no account assertion.
    account_type: Literal["StocksAndSharesISA"] | None = None
    maximum_capital_gbp: Decimal = Field(default=Decimal("200"), gt=0, le=200)
    instrument_types: tuple[Literal["STOCK"], ...] = ("STOCK",)
    quote_currencies: tuple[Literal["GBP", "GBX"], ...] = ("GBX",)
    excluded_activities: tuple[str, ...] = EXCLUDED_ACTIVITIES
    minimum_horizon_days: int = Field(default=1, ge=1, le=30)
    maximum_horizon_days: int = Field(default=30, ge=1, le=30)
    stretch_profit_gbp: Decimal = Field(default=Decimal("1000"), ge=0, le=1000)
    stretch_may_override_risk: Literal[False] = False

    @model_validator(mode="after")
    def enforce_mandate(self) -> Self:
        if self.minimum_horizon_days > self.maximum_horizon_days:
            raise ValueError("research horizon is reversed")
        if not self.quote_currencies or self.instrument_types != ("STOCK",):
            raise ValueError("individual stocks and at least one permitted currency are required")
        if not set(EXCLUDED_ACTIVITIES).issubset(self.excluded_activities):
            raise ValueError("ethical exclusions cannot be removed")
        return self


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    ELIGIBILITY_CHECK = "ELIGIBILITY_CHECK"
    DISCOVERY = "DISCOVERY"
    SNAPSHOT_BUILD = "SNAPSHOT_BUILD"
    FIRST_PASS_RESEARCH = "FIRST_PASS_RESEARCH"
    FIRST_PASS_LOCKED = "FIRST_PASS_LOCKED"
    LEAN_VALIDATION = "LEAN_VALIDATION"
    CREWAI_AUDIT = "CREWAI_AUDIT"
    CROSS_EXAMINATION = "CROSS_EXAMINATION"
    CONSENSUS = "CONSENSUS"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ResearchState(StrEnum):
    STRONG_RESEARCH_CANDIDATE = "STRONG_RESEARCH_CANDIDATE"
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"
    WATCH = "WATCH"
    REJECT = "REJECT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EXPIRED = "EXPIRED"


class InstrumentMetadata(Contract):
    ticker: Ticker
    company: str = Field(min_length=1, max_length=200)
    instrument_type: str
    quote_currency: str
    # Legacy historical evidence only; ignored by current qualification. Keep
    # its serialization stable so old immutable snapshot hashes still verify.
    isa_available: bool | None = None
    # Current presence in the live broker universe, NOT an ISA/buyability claim.
    currently_available: bool | None = None
    business_activities: tuple[str, ...] = ()
    activities_verified: bool = False
    verified_at: AwareDatetime
    source: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    source_id: str = Field(min_length=1)


class PriceBar(Contract):
    kind: Literal["ohlcv"] = "ohlcv"
    open: PositiveMoney
    high: PositiveMoney
    low: PositiveMoney
    close: PositiveMoney
    volume: int = Field(ge=0)
    currency: Literal["GBP", "GBX"]

    @model_validator(mode="after")
    def valid_bar(self) -> Self:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("invalid OHLC price range")
        return self


class FinancialFact(Contract):
    kind: Literal["financial"] = "financial"
    metric: str = Field(min_length=1, max_length=100)
    value: Decimal
    unit: str = Field(min_length=1, max_length=30)
    period_end: AwareDatetime


class DocumentFact(Contract):
    kind: Literal["filing", "announcement", "news", "corporate_action", "macro"]
    title: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(max_length=20000)
    url: str = Field(min_length=1, max_length=2000)


EvidencePayload = Annotated[PriceBar | FinancialFact | DocumentFact, Field(discriminator="kind")]


class EvidenceRecord(Contract):
    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    snapshot_id: str
    source: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    canonical_source_id: str = Field(min_length=1)
    observation_time: AwareDatetime
    publication_time: AwareDatetime | None
    retrieval_time: AwareDatetime
    fresh_until: AwareDatetime
    pit_safe: bool
    critical: bool = False
    conflicting: bool = False
    payload: EvidencePayload
    hash: str = ""

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if self.pit_safe and self.publication_time is None:
            raise ValueError("PIT-safe evidence requires publication availability")
        if self.publication_time and self.publication_time > self.retrieval_time:
            raise ValueError("retrieval cannot precede publication")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("evidence hash mismatch")
        object.__setattr__(self, "hash", digest)
        return self

    def available_at(self, cutoff: datetime) -> bool:
        return bool(
            self.pit_safe
            and self.publication_time is not None
            and self.publication_time <= cutoff
            and self.observation_time <= cutoff
        )


class ResearchSnapshot(Contract):
    snapshot_id: str
    ticker: Ticker
    created_at: AwareDatetime
    price_cutoff: AwareDatetime
    news_cutoff: AwareDatetime
    filing_cutoff: AwareDatetime
    fundamental_cutoff: AwareDatetime
    instrument: InstrumentMetadata
    evidence: tuple[EvidenceRecord, ...]
    historical: bool = False
    # Legacy snapshots required Qlib. Omit that default to preserve archived
    # hashes; explicit disabled mode is frozen into every new snapshot hash.
    qlib_enabled: bool = Field(default=True, strict=True, exclude_if=lambda value: value is True)
    # Live bulk discovery binds each candidate to the complete pre-screen
    # universe. Omit absent bindings to retain existing archived hash contracts.
    universe_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )
    hash: str = ""

    @property
    def required_first_pass_firms(self) -> frozenset[str]:
        return required_first_pass_firms(self.qlib_enabled)

    @model_validator(mode="after")
    def freeze_facts(self) -> Self:
        if self.instrument.ticker != self.ticker:
            raise ValueError("instrument and snapshot ticker mismatch")
        if self.instrument.verified_at > self.created_at:
            raise ValueError("instrument metadata was unavailable when snapshot was frozen")
        if self.historical and self.instrument.verified_at > min(
            self.price_cutoff, self.news_cutoff, self.filing_cutoff, self.fundamental_cutoff
        ):
            raise ValueError("historical instrument metadata contains look-ahead information")
        if len({e.evidence_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence identity")
        for cutoff in (
            self.price_cutoff,
            self.news_cutoff,
            self.filing_cutoff,
            self.fundamental_cutoff,
        ):
            if cutoff > self.created_at:
                raise ValueError("snapshot cutoff cannot be in the future")
        for record in self.evidence:
            if record.snapshot_id != self.snapshot_id:
                raise ValueError("evidence belongs to another snapshot")
            cutoff = self.cutoff_for(record)
            if isinstance(record.payload, FinancialFact) and record.payload.period_end > cutoff:
                raise ValueError("financial reporting period contains look-ahead information")
            if record.observation_time > cutoff:
                raise ValueError("look-ahead observation")
            if record.publication_time and record.publication_time > cutoff:
                raise ValueError("look-ahead publication")
            if record.retrieval_time > self.created_at:
                raise ValueError("evidence retrieved after snapshot was frozen")
            if self.historical and not record.available_at(cutoff):
                raise ValueError("unsafe historical evidence: publication availability unknown")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("snapshot hash mismatch")
        object.__setattr__(self, "hash", digest)
        return self

    def cutoff_for(self, record: EvidenceRecord) -> datetime:
        match record.payload.kind:
            case "ohlcv":
                return self.price_cutoff
            case "financial":
                return self.fundamental_cutoff
            case "filing":
                return self.filing_cutoff
            case _:
                return self.news_cutoff


class DiscoveryReason(Contract):
    channel: Literal["technical", "quantitative", "catalyst", "fundamental"]
    reason: str
    evidence_ids: tuple[str, ...]


class Candidate(Contract):
    ticker: Ticker
    discovery: tuple[DiscoveryReason, ...]


class Claim(Contract):
    claim_id: str
    family: Literal["technical", "fundamental", "catalyst", "quantitative", "risk"]
    statement: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...]
    classification: Literal["INFERENCE", "FIRM_OPINION"] = "FIRM_OPINION"


class Usage(Contract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_gbp: Decimal | None = Field(default=None, ge=0)


class FirmReport(Contract):
    firm: Literal["tradingagents", "ai_hedge_fund", "qlib"]
    snapshot_id: str
    snapshot_hash: str
    conclusion: str = Field(min_length=1, max_length=8000)
    claims: tuple[Claim, ...]
    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    upstream_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_family: str
    llm_provider_family: str | None = None
    feature_families: tuple[str, ...] = ()
    argument_families: tuple[str, ...] = ()
    created_at: AwareDatetime
    usage: Usage = Field(default_factory=Usage)
    runtime: Literal["live", "demo"] = "live"


class TradingAgentsResearchReport(FirmReport):
    firm: Literal["tradingagents"] = "tradingagents"


class AIHedgeFundResearchReport(FirmReport):
    firm: Literal["ai_hedge_fund"] = "ai_hedge_fund"


class QlibQuantResearchReport(FirmReport):
    firm: Literal["qlib"] = "qlib"
    prediction_score: float | None = None
    rank: int | None = Field(default=None, ge=1)
    universe_size: int | None = Field(default=None, ge=1)
    regime: str = "unknown"
    uncertainty: float | None = Field(default=None, ge=0)
    validation_metadata: tuple[str, ...] = ()


FirstPassReport = Annotated[
    TradingAgentsResearchReport | AIHedgeFundResearchReport | QlibQuantResearchReport,
    Field(discriminator="firm"),
]


class LeanValidationReport(Contract):
    state: Literal["PASS", "FAIL", "INSUFFICIENT_EVIDENCE"]
    snapshot_id: str
    runner_version: str
    observations: int = Field(default=0, ge=0)
    walk_forward: bool = False
    out_of_sample: bool = False
    pit_safe: bool = False
    survivorship_checked: bool = False
    costs_included: bool = False
    sensitivity_checked: bool = False
    scenario_policy_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    spread_bps: float | None = Field(default=None, ge=0)
    slippage_bps: float | None = Field(default=None, ge=0)
    maximum_drawdown: Fraction | None = None
    mae: float | None = None
    mfe: float | None = None
    findings: tuple[str, ...] = ()


class AuditFinding(Contract):
    auditor: str
    claim_id: str | None = None
    state: Literal["VERIFIED", "UNSUPPORTED", "CONTRADICTED", "WARN"]
    explanation: str
    evidence_ids: tuple[str, ...] = ()


class CIOAuditReport(Contract):
    snapshot_id: str
    completed: bool
    findings: tuple[AuditFinding, ...]
    active_specialists: tuple[str, ...]
    material_disagreement: bool = False


class RedTeamReport(Contract):
    state: Literal["PASS", "WARN", "VETO"]
    findings: tuple[str, ...]


class EvidenceIndependenceReport(Contract):
    unique_sources: int = Field(ge=0)
    unique_providers: int = Field(ge=0)
    evidence_families: tuple[str, ...]
    source_overlap: Fraction
    model_overlap: Fraction
    feature_overlap: Fraction
    argument_overlap: Fraction
    strength: Literal["LOW", "MEDIUM", "HIGH"]
    limitations: tuple[str, ...]


class ResearchSignal(Contract):
    research_id: str
    ticker: Ticker
    state: Literal["STRONG_RESEARCH_CANDIDATE", "RESEARCH_CANDIDATE", "WATCH"]
    issued_at: AwareDatetime
    valid_until: AwareDatetime
    entry_low: PositiveMoney
    entry_high: PositiveMoney
    quote_currency: Literal["GBP", "GBX"]
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    event_invalidators: tuple[str, ...] = Field(min_length=1)
    potential_targets: tuple[PositiveMoney, ...]
    assumed_capital_gbp: Decimal = Field(gt=0, le=200)
    illustrative_allocation_gbp: Decimal = Field(gt=0, le=200)
    modelled_downside_gbp: Decimal = Field(ge=0, le=200)
    horizon_days: int = Field(ge=1, le=30)
    calibrated_confidence: Fraction | None = None
    invalidated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def limits(self) -> Self:
        if self.valid_until <= self.issued_at or self.entry_high < self.entry_low:
            raise ValueError("invalid signal interval")
        if self.illustrative_allocation_gbp > self.assumed_capital_gbp:
            raise ValueError("allocation exceeds capital")
        if self.modelled_downside_gbp > self.illustrative_allocation_gbp:
            raise ValueError("downside exceeds allocation")
        if (self.valid_until - self.issued_at).total_seconds() > self.horizon_days * 86400:
            raise ValueError("signal validity exceeds research horizon")
        return self

    def effective_state(self, now: datetime) -> ResearchState:
        if now >= self.valid_until or (self.invalidated_at and now >= self.invalidated_at):
            return ResearchState.EXPIRED
        return ResearchState(self.state)


class DecisionPacket(Contract):
    research_id: str
    mandate: ResearchMandate
    snapshot_id: str
    snapshot_hash: str
    candidate: Candidate
    eligibility: InstrumentMetadata
    reports: tuple[FirstPassReport, ...]
    lean: LeanValidationReport
    audit: CIOAuditReport
    red_team: RedTeamReport
    independence: EvidenceIndependenceReport
    final_state: ResearchState
    reasons: tuple[str, ...]
    sources: tuple[EvidenceRecord, ...]
    issued_at: AwareDatetime
    signal: ResearchSignal | None = None
    cross_examination_rounds: int = Field(default=0, ge=0, le=2)
    runtime: Literal["live", "demo"]
    qlib_enabled: bool = Field(default=True, strict=True, exclude_if=lambda value: value is True)
    # Optional/omitted for old packets: existing immutable hashes remain valid.
    money_version: str | None = Field(default=None, exclude_if=lambda value: value is None)
    git_commit: str | None = Field(default=None, exclude_if=lambda value: value is None)
    frozen_snapshot: ResearchSnapshot | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_manifest_hash: str | None = Field(default=None, exclude_if=lambda value: value is None)
    cross_examination_hash: str | None = Field(default=None, exclude_if=lambda value: value is None)
    hash: str = ""

    @model_validator(mode="after")
    def consistent_packet(self) -> Self:
        if not self.qlib_enabled and self.frozen_snapshot is None:
            raise ValueError("disabled Qlib requires explicit frozen snapshot provenance")
        if self.frozen_snapshot is not None and (
            self.frozen_snapshot.hash != self.snapshot_hash
            or self.frozen_snapshot.snapshot_id != self.snapshot_id
            or self.frozen_snapshot.evidence != self.sources
            or self.frozen_snapshot.qlib_enabled != self.qlib_enabled
        ):
            raise ValueError("packet frozen snapshot mismatch")
        required = required_first_pass_firms(self.qlib_enabled)
        if {r.firm for r in self.reports} != required:
            raise ValueError("packet requires all independent firms")
        if len(self.reports) != len(required):
            raise ValueError("packet requires exactly the configured independent reports")
        if any(report.runtime != self.runtime for report in self.reports):
            raise ValueError("packet runtime must match all report runtimes")
        if any(
            r.snapshot_id != self.snapshot_id or r.snapshot_hash != self.snapshot_hash
            for r in self.reports
        ):
            raise ValueError("packet reports use different snapshots")
        if self.lean.snapshot_id != self.snapshot_id or self.audit.snapshot_id != self.snapshot_id:
            raise ValueError("validation or audit uses a different snapshot")
        if self.candidate.ticker != self.eligibility.ticker:
            raise ValueError("packet candidate mismatch")
        if any(s.snapshot_id != self.snapshot_id for s in self.sources):
            raise ValueError("packet evidence uses a different snapshot")
        if self.signal:
            if self.runtime == "demo" or self.final_state.value != self.signal.state:
                raise ValueError("signal inconsistent with packet state/runtime")
            if self.signal.research_id != self.research_id:
                raise ValueError("signal research identity mismatch")
            if self.signal.ticker != self.candidate.ticker:
                raise ValueError("signal candidate mismatch")
            if self.signal.quote_currency != self.eligibility.quote_currency:
                raise ValueError("signal and instrument quote currencies differ")
            if (
                not self.mandate.minimum_horizon_days
                <= self.signal.horizon_days
                <= self.mandate.maximum_horizon_days
            ):
                raise ValueError("signal horizon exceeds mandate")
            if (
                self.lean.state != "PASS"
                or not self.audit.completed
                or self.red_team.state == "VETO"
            ):
                raise ValueError("signal requires validation and completed audit without veto")
            if self.signal.assumed_capital_gbp > self.mandate.maximum_capital_gbp:
                raise ValueError("signal exceeds mandate capital")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("packet hash mismatch")
        object.__setattr__(self, "hash", digest)
        return self
