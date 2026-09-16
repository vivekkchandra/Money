"""Administrator-configured live assembly. No demo or provider fallback paths."""

from __future__ import annotations

import hashlib
import os
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from pydantic import Field, model_validator

from money.adapters.eligibility import Trading212EligibilityAdapter
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
from money.data.qualification import ProviderQualification
from money.data.quality.market import evaluate_market_quality
from money.data.resilience import ProviderCircuit
from money.data.uk.live import CompaniesHouseProvider, EODHDProvider
from money.models.registry import ModelRegistry
from money.research.budgets import BudgetLimits
from money.research.inference import HTTPInference, InferenceConfiguration
from money.risk.costs import CostApplicability
from money.scanner.discovery import DiscoveryPolicy, discover_snapshot
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Candidate,
    CIOAuditReport,
    Contract,
    EvidenceRecord,
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


class InferenceSelection(Contract):
    provider: str
    model: str
    endpoint: str
    credential_environment_variable: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,80}$")
    protocol: Literal["openai-compatible", "anthropic"] = "openai-compatible"
    temperature: float | None = Field(default=0, ge=0, le=2)
    reasoning_effort: str | None = None
    max_output_tokens: int = Field(default=3000, ge=256, le=16000)
    maximum_prompt_bytes: int = Field(default=30000, ge=1000, le=200000)
    timeout_seconds: int = Field(default=60, ge=1, le=180)
    input_gbp_per_million: Decimal | None = Field(default=None, ge=0)
    output_gbp_per_million: Decimal | None = Field(default=None, ge=0)

    def inference(self) -> HTTPInference:
        secret = os.environ.get(self.credential_environment_variable)
        if not secret:
            raise ValueError("INFERENCE_CREDENTIAL_MISSING")
        return HTTPInference(
            InferenceConfiguration(
                **self.model_dump(exclude={"credential_environment_variable"}), api_key=secret
            )
        )


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


def validate_invocation_budgets(
    selections: tuple[InferenceSelection, InferenceSelection, InferenceSelection],
    calls: int,
    limits: BudgetLimits,
) -> None:
    # UTF-8 bytes conservatively bound tokenized input plus protocol overhead.
    # Operators must deliberately budget the actual configured maximum; do not
    # silently reduce native roles or start a stage that cannot be admitted.
    reservations = tuple(
        calls * (item.maximum_prompt_bytes + item.max_output_tokens + 1024) for item in selections
    )
    if (
        sum(reservations) > min(limits.per_job, limits.per_candidate, limits.daily)
        or max(reservations) > limits.per_agent
        or max(sum(reservations[:2]), reservations[2]) > limits.per_stage
    ):
        raise ValueError("LIVE_BUDGET_CONFIGURATION_INSUFFICIENT")
    for provider_model, limit in limits.per_model:
        required = sum(
            tokens
            for item, tokens in zip(selections, reservations, strict=True)
            if f"{item.provider}/{item.model}" == provider_model
        )
        if required > limit:
            raise ValueError("LIVE_MODEL_BUDGET_CONFIGURATION_INSUFFICIENT")


class LiveManifest(Contract):
    version: Literal["money-live-v1"] = "money-live-v1"
    reviewed_by: str = Field(min_length=1)
    qualification_artifacts: tuple[tuple[str, str], ...]
    # Each (sha256, relative file path) references actual reviewed bytes.
    instruments: tuple[VerifiedInstrument, ...] = Field(min_length=1)
    provider_qualifications: tuple[ProviderQualification, ...] = Field(min_length=1)
    market_credential_environment_variable: str = "EODHD_API_KEY"
    filings_credential_environment_variable: str = "COMPANIES_HOUSE_API_KEY"
    tradingagents: InferenceSelection
    ai_hedge_fund: InferenceSelection
    crewai: InferenceSelection
    native_timeout_seconds: int = Field(default=240, ge=30, le=1200)
    native_max_calls: int = Field(default=16, ge=4, le=32)
    native_egress_policy_verified: Literal[True]
    native_egress_verification_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    qlib_registry_id: str
    qlib_artifact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    lean: LeanContainerSettings
    lean_costs: LeanCostAssumptions
    lean_parameters: LeanStudyParameters
    lean_qualification: LeanStudyQualification
    budgets: BudgetLimits
    signal_policy: SignalPolicy = Field(default_factory=SignalPolicy)
    discovery_policy: DiscoveryPolicy = Field(default_factory=DiscoveryPolicy)

    @model_validator(mode="after")
    def qualification_consistency(self) -> LiveManifest:
        if self.lean_parameters.scenario_policy != self.signal_policy:
            raise ValueError("LEAN_SCENARIO_POLICY_MISMATCH")
        validate_invocation_budgets(
            (self.tradingagents, self.ai_hedge_fund, self.crewai),
            self.native_max_calls,
            self.budgets,
        )
        providers = [item.provider for item in self.provider_qualifications]
        tickers = [item.metadata.ticker for item in self.instruments]
        if len(providers) != len(set(providers)) or len(tickers) != len(set(tickers)):
            raise ValueError("LIVE_MANIFEST_DUPLICATE_IDENTITY")
        if not {"eodhd", "companies-house"} <= set(providers):
            raise ValueError("LIVE_MANIFEST_REQUIRED_PROVIDER_MISSING")
        return self


class LiveProvenance(Contract):
    manifest_hash: str
    provider_qualifications: tuple[ProviderQualification, ...]
    qlib_artifact_hash: str
    qlib_registry_id: str
    lean_image: str
    cost_version: str
    native_egress_verification_hash: str
    model_selections: tuple[tuple[str, str, str], ...]


def load_manifest(path: Path, expected_hash: str) -> LiveManifest:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 5_000_000:
        raise ValueError("LIVE_MANIFEST_INVALID")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("LIVE_MANIFEST_HASH_MISMATCH")
    manifest = LiveManifest.model_validate_json(raw)
    verified = set()
    root = path.parent.resolve()
    for digest, relative in manifest.qualification_artifacts:
        artifact = root / relative
        if (
            artifact.is_symlink()
            or not artifact.resolve().is_relative_to(root)
            or not artifact.is_file()
            or artifact.stat().st_size > 2_000_000
        ):
            raise ValueError("QUALIFICATION_ARTIFACT_INVALID")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            raise ValueError("QUALIFICATION_ARTIFACT_HASH_MISMATCH")
        verified.add(digest)
    required = {
        manifest.native_egress_verification_hash,
        manifest.lean_qualification.historical_eligibility_hash,
        manifest.lean_qualification.survivorship_audit_hash,
        manifest.lean_qualification.corporate_action_audit_hash,
    }
    for item in manifest.instruments:
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
    for provider in manifest.provider_qualifications:
        if provider.qualification_report_hash:
            required.add(provider.qualification_report_hash)
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


class LiveSnapshotBuilder:
    def __init__(self, manifest: LiveManifest, store: ResearchStore) -> None:
        self.manifest = manifest
        self.circuit = ProviderCircuit(store)

    def __call__(self, instrument: InstrumentMetadata) -> ResearchSnapshot:
        item = next(i for i in self.manifest.instruments if i.metadata.ticker == instrument.ticker)
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
        filing = CompaniesHouseProvider(
            os.environ.get(self.manifest.filings_credential_environment_variable, "")
        )
        qualifications["companies-house"].require("filing", now)
        records.extend(
            self.circuit.call(
                "companies-house:filing", lambda: filing.filings(item.identifiers, snapshot_id, now)
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
        if not any(
            r.payload.kind == "financial" and r.payload.metric != "spread_bps" for r in records
        ):
            raise ValueError("CRITICAL_FUNDAMENTALS_MISSING")
        from money.schemas.contracts import FinancialFact

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

    model = ModelRegistry(store).load_active(
        manifest.qlib_registry_id, manifest.qlib_artifact_hash, utc_now()
    )
    settings = NativeRunSettings(
        timeout_seconds=manifest.native_timeout_seconds, max_calls=manifest.native_max_calls
    )

    def wrapped(runner: Any, schema: Any, selection: InferenceSelection) -> BoundedNativeRunner:
        return BoundedNativeRunner(
            runner,
            schema,
            NativeProcessPolicy(
                timeout_seconds=manifest.native_timeout_seconds,
                gateway_hosts=selection.inference().allowed_network_hosts,
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
    )
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
        item = next(i for i in manifest.instruments if i.metadata.ticker == snapshot.ticker)
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
            snapshot, quant=quant.research(mandate, snapshot), policy=manifest.discovery_policy
        )

    return ResearchRuntime(
        mode="live",
        eligibility=Trading212EligibilityAdapter(tuple(i.metadata for i in manifest.instruments)),
        snapshot_builder=LiveSnapshotBuilder(manifest, store),
        firms=(trading, hedge, quant),
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
        provenance=LiveProvenance(
            manifest_hash=content_hash(manifest),
            provider_qualifications=manifest.provider_qualifications,
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
