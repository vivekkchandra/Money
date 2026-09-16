"""Container-worker orchestration. HTTP handlers never import or execute this flow."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from money.adapters.eligibility import (
    Trading212EligibilityAdapter,
    Trading212EligibilityService,
    eligibility_failures,
)
from money.api.errors import classify_failure
from money.api.observability import failure_context
from money.crews.cross_examination import CrossExaminationPacket, run_cross_examination
from money.policy.governance import consensus, evidence_independence, snapshot_failures
from money.product.metering import CallMeter
from money.research.budgets import BudgetLimits, BudgetReservation, TokenBudgetManager
from money.scanner.discovery import discover_snapshot
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    AuditFinding,
    Candidate,
    CIOAuditReport,
    Claim,
    Contract,
    DecisionPacket,
    DocumentFact,
    EvidenceIndependenceReport,
    EvidenceRecord,
    FinancialFact,
    FirmReport,
    FirstPassReport,
    InstrumentMetadata,
    JobStatus,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    RedTeamReport,
    ResearchMandate,
    ResearchSnapshot,
    ResearchState,
    TradingAgentsResearchReport,
    Usage,
    content_hash,
    utc_now,
)
from money.signals.generation import SignalDesign
from money.storage.store import FIRST_PASS_FIRMS, ResearchStore


class FirstPassFirm(Protocol):
    def research(self, mandate: ResearchMandate, snapshot: ResearchSnapshot) -> FirmReport: ...


@dataclass(frozen=True)
class ResearchRuntime:
    mode: str
    eligibility: Trading212EligibilityService
    snapshot_builder: Callable[[InstrumentMetadata], ResearchSnapshot]
    firms: tuple[FirstPassFirm, ...]
    validate: Callable[[ResearchSnapshot, tuple[FirmReport, ...]], LeanValidationReport]
    audit: Callable[
        [ResearchSnapshot, tuple[FirmReport, ...], LeanValidationReport], CIOAuditReport
    ]
    red_team: Callable[[ResearchSnapshot, tuple[FirmReport, ...]], RedTeamReport]
    budget_limits: BudgetLimits | None = None
    invocation_budgets: tuple[tuple[str, str, str, int], ...] = ()
    cio_runtime: Callable[[], Contract | None] | None = None
    provenance: Contract | None = None
    discover: Callable[[ResearchMandate, ResearchSnapshot], Candidate] | None = None
    cross_examine: Callable[
        [str, ResearchMandate, ResearchSnapshot, tuple[FirmReport, ...],
         LeanValidationReport, CIOAuditReport], CrossExaminationPacket
    ] | None = None
    signal_builder: (
        Callable[
            [
                str,
                ResearchMandate,
                ResearchSnapshot,
                tuple[FirmReport, ...],
                LeanValidationReport,
                CIOAuditReport,
                RedTeamReport,
                ResearchState,
            ],
            SignalDesign,
        ]
        | None
    ) = None


def run_first_pass(
    job_id: str,
    store: ResearchStore,
    mandate: ResearchMandate,
    snapshot: ResearchSnapshot,
    firms: tuple[FirstPassFirm, ...],
    *,
    sealed_firms: frozenset[str] = frozenset(),
    budget: TokenBudgetManager | None = None,
    reservations: dict[str, str] | None = None,
) -> None:
    """Each firm capability is only two immutable contracts, never a report reader."""
    if len(firms) != 3:
        raise ValueError("all three independent firms are required")
    if not sealed_firms <= FIRST_PASS_FIRMS:
        raise ValueError("unknown sealed firm")

    def invoke(firm: FirstPassFirm) -> FirmReport:
        def operation() -> FirmReport:
            return firm.research(
                ResearchMandate.model_validate_json(mandate.model_dump_json()),
                ResearchSnapshot.model_validate_json(snapshot.model_dump_json()),
            )
        reservation = (reservations or {}).get(getattr(firm, "firm", ""))
        if budget and reservation:
            return CallMeter(store).invoke_reserved(reservation, operation)
        return operation()

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="money-first-pass") as pool:
        futures = [
            pool.submit(invoke, firm)
            for firm in firms
            if getattr(firm, "firm", None) not in sealed_firms
        ]
        for future in as_completed(futures):
            report = future.result()
            if (
                report.firm not in FIRST_PASS_FIRMS
                or report.snapshot_id != snapshot.snapshot_id
                or report.snapshot_hash != snapshot.hash
            ):
                raise ValueError("firm report identity or snapshot mismatch")
            evidence_ids = {e.evidence_id for e in snapshot.evidence}
            if any(not set(c.evidence_ids).issubset(evidence_ids) for c in report.claims):
                raise ValueError("firm cites evidence outside the frozen snapshot")
            store.save_report(job_id, report.firm, report)
            if budget and reservations and report.firm in reservations:
                budget.settle(reservations[report.firm], report.usage)
    # Atomic storage operation verifies that all persisted identities are present.
    store.lock_first_pass(job_id)


def locked_reports(job_id: str, store: ResearchStore) -> tuple[FirstPassReport, ...]:
    content = store.get_reports(job_id)  # Raises before the persistent barrier.
    return (
        TradingAgentsResearchReport.model_validate(content["tradingagents"]),
        AIHedgeFundResearchReport.model_validate(content["ai_hedge_fund"]),
        QlibQuantResearchReport.model_validate(content["qlib"]),
    )


def run_research(job_id: str, store: ResearchStore, runtime: ResearchRuntime) -> None:
    job = store.get_job(job_id)
    if job is None:
        raise ValueError("research job not found")
    mandate = ResearchMandate.model_validate(store.get_mandate(job_id))
    checkpoint = store.get_checkpoint(job_id)
    artifacts = checkpoint["artifacts"]
    budget = TokenBudgetManager(store, runtime.budget_limits) if runtime.budget_limits else None
    if budget:
        budget.reconcile_job(job_id)
    if "source_manifest" in artifacts:
        if runtime.provenance is None or content_hash(artifacts["source_manifest"]) != content_hash(
            runtime.provenance
        ):
            raise ValueError("RESEARCH_CONFIGURATION_CHANGED")
    elif runtime.provenance is not None:
        store.save_artifact(job_id, "source_manifest", runtime.provenance)
        artifacts["source_manifest"] = runtime.provenance.model_dump(mode="json")
    instrument = (
        InstrumentMetadata.model_validate(artifacts["eligibility"])
        if "eligibility" in artifacts and "passed" not in artifacts["eligibility"]
        else runtime.eligibility.get_instrument_metadata(job["ticker"])
    )
    failures = eligibility_failures(instrument, mandate, utc_now())
    if failures:
        if "eligibility" not in artifacts:
            store.save_artifact(job_id, "eligibility", {"passed": False, "reasons": failures})
        store.complete_job(
            job_id,
            {
                "research_id": job_id,
                "ticker": job["ticker"],
                "final_state": "REJECT",
                "mandate": mandate.model_dump(mode="json"),
                "reasons": list(failures),
                "signal": None,
                "runtime": runtime.mode,
                "issued_at": utc_now().isoformat(),
            },
            state=JobStatus.REJECTED,
        )
        return
    assert instrument is not None
    if "eligibility" not in artifacts:
        store.save_artifact(job_id, "eligibility", instrument)
    if checkpoint["snapshot"] is None:
        if checkpoint["stage"] in {"ELIGIBILITY_CHECK", "DISCOVERY"}:
            store.update_stage(job_id, JobStatus.DISCOVERY)
        store.update_stage(job_id, JobStatus.SNAPSHOT_BUILD)
        snapshot = runtime.snapshot_builder(instrument)
        store.save_snapshot(job_id, snapshot)
    else:
        snapshot = ResearchSnapshot.model_validate(checkpoint["snapshot"])
    if snapshot.ticker != job["ticker"]:
        raise ValueError("snapshot ticker mismatch")
    failures = snapshot_failures(snapshot, utc_now())
    if failures:
        store.complete_job(
            job_id,
            {
                "research_id": job_id,
                "ticker": job["ticker"],
                "final_state": "REJECT",
                "snapshot_id": snapshot.snapshot_id,
                "reasons": failures,
                "signal": None,
                "runtime": runtime.mode,
            },
            state=JobStatus.REJECTED,
        )
        return
    if "discovery" in artifacts:
        candidate = Candidate.model_validate(artifacts["discovery"])
    elif runtime.discover is not None:
        discovery = runtime.discover
        candidate = (
            CallMeter(store).invoke(
                job_id, component="discovery", provider="money", model=None,
                operation=lambda: discovery(mandate, snapshot),
            )
            if runtime.mode == "live" else discovery(mandate, snapshot)
        )
    else:
        candidate = discover_snapshot(snapshot)
    if "discovery" not in artifacts:
        store.save_artifact(job_id, "discovery", candidate)
    if runtime.provenance is not None and "source_manifest" not in artifacts:
        store.save_artifact(job_id, "source_manifest", runtime.provenance)
    if runtime.mode == "live" and not candidate.discovery:
        store.complete_job(
            job_id,
            {
                "research_id": job_id,
                "ticker": job["ticker"],
                "final_state": "REJECT",
                "snapshot_id": snapshot.snapshot_id,
                "mandate": mandate.model_dump(mode="json"),
                "signal": None,
                "reasons": ["NO_DISCOVERY_EVIDENCE"],
                "runtime": "live",
            },
            state=JobStatus.REJECTED,
        )
        return
    reservations: dict[str, str] = {}
    if budget:
        requests = []
        for agent, provider, model, maximum in runtime.invocation_budgets:
            if agent in checkpoint["sealed_firms"] or agent == "crewai":
                continue
            reservation = f"{job_id}:{job['attempt_count']}:{agent}"
            requests.append(
                BudgetReservation(
                    reservation_id=reservation,
                    job_id=job_id,
                    stage="FIRST_PASS_RESEARCH",
                    agent=agent,
                    provider=provider,
                    model=model,
                    maximum_tokens=maximum,
                    prompt_version="money-native-v1",
                )
            )
            reservations[agent] = reservation
        budget.reserve_many(tuple(requests))
    if job["locked_at"] is None:
        store.update_stage(job_id, JobStatus.FIRST_PASS_RESEARCH)
        try:
            run_first_pass(
                job_id,
                store,
                mandate,
                snapshot,
                runtime.firms,
                sealed_firms=frozenset(checkpoint["sealed_firms"]),
                budget=budget,
                reservations=reservations,
            )
        except Exception as error:
            logging.getLogger(__name__).error(
                "first_pass_failed",
                extra={"research_id": job_id, **failure_context(error)},
            )
            if classify_failure(error).retryable:
                raise
            # A missing report cannot be converted into agreement or substituted.
            store.fail_job(
                job_id,
                "FIRST_PASS_FAILED",
                "An independent firm failed; no research signal was issued.",
            )
            return
    reports = locked_reports(job_id, store)
    if "lean" in artifacts:
        lean = LeanValidationReport.model_validate(artifacts["lean"])
    else:
        store.update_stage(job_id, JobStatus.LEAN_VALIDATION)
        lean = (
            CallMeter(store).invoke(
                job_id, component="lean", provider="lean", model=None,
                operation=lambda: runtime.validate(snapshot, reports),
            )
            if runtime.mode == "live" else runtime.validate(snapshot, reports)
        )
        store.save_artifact(job_id, "lean", lean)
    if "audit" in artifacts:
        audit = CIOAuditReport.model_validate(artifacts["audit"])
        if "red_team" not in artifacts:
            raise ValueError("CIO_CHECKPOINT_INCOMPLETE")
        red_team = RedTeamReport.model_validate(artifacts["red_team"])
    else:
        store.update_stage(job_id, JobStatus.CREWAI_AUDIT)
        if budget:
            for agent, provider, model, maximum in runtime.invocation_budgets:
                if agent != "crewai":
                    continue
                reservation = f"{job_id}:{job['attempt_count']}:{agent}"
                budget.reserve(
                    reservation_id=reservation,
                    job_id=job_id,
                    stage="CREWAI_AUDIT",
                    agent=agent,
                    provider=provider,
                    model=model,
                    maximum_tokens=maximum,
                    prompt_version="money-native-v1",
                )
                reservations[agent] = reservation
        def audit_operation() -> CIOAuditReport:
            return runtime.audit(snapshot, locked_reports(job_id, store), lean)

        audit = (
            CallMeter(store).invoke_reserved(reservations["crewai"], audit_operation)
            if budget and "crewai" in reservations else audit_operation()
        )
        red_team = runtime.red_team(snapshot, reports)
        cio_runtime = runtime.cio_runtime() if runtime.cio_runtime else None
        store.save_cio_artifacts(job_id, audit, red_team, runtime=cio_runtime)
        if budget and "crewai" in reservations:
            budget.settle(reservations["crewai"], getattr(cio_runtime, "usage", Usage()))
    if "independence" in artifacts:
        independence = EvidenceIndependenceReport.model_validate(artifacts["independence"])
    else:
        independence = evidence_independence(snapshot, reports)
        store.save_artifact(job_id, "independence", independence)
    examination = None
    if "cross_examination" in artifacts:
        examination = CrossExaminationPacket.model_validate(artifacts["cross_examination"])
    elif audit.completed:
        store.update_stage(job_id, JobStatus.CROSS_EXAMINATION)
        examination = (
            runtime.cross_examine(job_id, mandate, snapshot, reports, lean, audit)
            if runtime.cross_examine else
            run_cross_examination(snapshot, reports, lean, audit, first_pass_locked=True)
        )
        store.save_artifact(job_id, "cross_examination", examination)
    store.update_stage(job_id, JobStatus.CONSENSUS)
    state, reasons = consensus(
        mandate,
        snapshot,
        reports,
        lean,
        audit,
        red_team,
        independence,
        utc_now(),
        rounds=len(examination.rounds) if examination else 0,
        cross_examination_disagreement=examination.material_disagreement if examination else False,
    )
    signal = None
    if runtime.signal_builder is not None:
        if "signal_design" in artifacts:
            design = SignalDesign.model_validate(artifacts["signal_design"])
        else:
            design = runtime.signal_builder(
                job_id, mandate, snapshot, reports, lean, audit, red_team, state
            )
            store.save_artifact(job_id, "signal_design", design)
        signal = design.signal
        if state in {ResearchState.RESEARCH_CANDIDATE, ResearchState.WATCH} and signal is None:
            state, reasons = ResearchState.INSUFFICIENT_EVIDENCE, design.reasons
    packet = DecisionPacket(
        research_id=job_id,
        mandate=mandate,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.hash,
        candidate=candidate,
        eligibility=instrument,
        reports=reports,
        lean=lean,
        audit=audit,
        red_team=red_team,
        independence=independence,
        final_state=state,
        reasons=reasons,
        sources=snapshot.evidence,
        issued_at=utc_now(),
        signal=signal,
        runtime="demo" if runtime.mode == "demo" else "live",
        money_version=version("money"),
        git_commit=os.environ.get("MONEY_GIT_SHA", "unknown"),
        frozen_snapshot=snapshot,
        source_manifest_hash=content_hash(runtime.provenance) if runtime.provenance else None,
        cross_examination_hash=examination.hash if examination else None,
        cross_examination_rounds=len(examination.rounds) if examination else 0,
    )
    store.complete_job(job_id, packet)


class DemoFirm:
    """Explicit synthetic fixture, never an upstream execution or a live recommendation."""

    def __init__(self, firm: str) -> None:
        self.firm = firm

    def research(self, mandate: ResearchMandate, snapshot: ResearchSnapshot) -> FirmReport:
        schemas = {
            "tradingagents": (TradingAgentsResearchReport, "technical", "ohlcv"),
            "ai_hedge_fund": (AIHedgeFundResearchReport, "fundamental", "financial"),
            "qlib": (QlibQuantResearchReport, "quantitative", "ohlcv"),
        }
        schema, family, kind = schemas[self.firm]
        evidence = next(e for e in snapshot.evidence if e.payload.kind == kind)
        return schema(
            snapshot_id=snapshot.snapshot_id,
            snapshot_hash=snapshot.hash,
            conclusion="Synthetic demonstration: evidence is available for independent review. "
            "No upstream model was executed and no investment signal is issued.",
            claims=(
                Claim.model_validate(
                    {
                        "claim_id": f"{self.firm}:coverage",
                        "family": family,
                        "statement": f"The frozen snapshot contains {kind} evidence.",
                        "evidence_ids": (evidence.evidence_id,),
                    }
                ),
            ),
            model_version="synthetic-fixture-v1",
            prompt_version="no-llm",
            upstream_sha="0" * 40,
            model_family=f"fixture:{self.firm}",
            feature_families=(kind,),
            argument_families=("coverage",),
            created_at=utc_now(),
            runtime="demo",
        )


def demo_snapshot(instrument: InstrumentMetadata) -> ResearchSnapshot:
    now, snapshot_id = utc_now(), str(uuid4())
    common = {
        "snapshot_id": snapshot_id,
        "source": "Money synthetic demonstration",
        "provider": "money-demo",
        "observation_time": now - timedelta(minutes=1),
        "publication_time": now - timedelta(minutes=1),
        "retrieval_time": now,
        "fresh_until": now + timedelta(minutes=30),
        "pit_safe": True,
    }
    records = (
        EvidenceRecord.model_validate(
            dict(
                common,
                evidence_id="demo-price",
                source_id="price-v1",
                canonical_source_id="demo:price",
                critical=True,
                payload=PriceBar(
                    open=Decimal("100"),
                    high=Decimal("103"),
                    low=Decimal("99"),
                    close=Decimal("102"),
                    volume=100000,
                    currency="GBX",
                ),
            )
        ),
        EvidenceRecord.model_validate(
            dict(
                common,
                evidence_id="demo-financial",
                source_id="filing-v1",
                canonical_source_id="demo:filing",
                payload=FinancialFact(
                    metric="cash",
                    value=Decimal("1000000"),
                    unit="GBP",
                    period_end=now - timedelta(days=90),
                ),
            )
        ),
        EvidenceRecord.model_validate(
            dict(
                common,
                evidence_id="demo-news",
                source_id="announcement-v1",
                canonical_source_id="demo:announcement",
                payload=DocumentFact(
                    kind="announcement",
                    title="Synthetic results notice",
                    excerpt="Demonstration evidence only; not a real company.",
                    url="https://example.invalid/money-demo",
                ),
            )
        ),
    )
    return ResearchSnapshot(
        snapshot_id=snapshot_id,
        ticker=instrument.ticker,
        created_at=now,
        price_cutoff=now,
        news_cutoff=now,
        filing_cutoff=now,
        fundamental_cutoff=now,
        instrument=instrument,
        evidence=records,
    )


def unavailable_validation(
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
) -> LeanValidationReport:
    return LeanValidationReport(
        state="INSUFFICIENT_EVIDENCE",
        snapshot_id=snapshot.snapshot_id,
        runner_version="not-configured",
        findings=("A qualified LEAN runner is not configured; no backtest PASS is inferred.",),
    )


def demo_audit(
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
    lean: LeanValidationReport,
) -> CIOAuditReport:
    evidence_ids = {e.evidence_id for e in snapshot.evidence}
    findings = tuple(
        AuditFinding(
            auditor="Deterministic demonstration evidence auditor (CrewAI not executed)",
            claim_id=claim.claim_id,
            state="VERIFIED" if set(claim.evidence_ids) <= evidence_ids else "UNSUPPORTED",
            explanation="Independently checked referenced evidence exists in the frozen snapshot; "
            "this does not verify a market thesis.",
            evidence_ids=claim.evidence_ids,
        )
        for report in reports
        for claim in report.claims
    )
    return CIOAuditReport(
        snapshot_id=snapshot.snapshot_id,
        completed=True,
        findings=findings,
        active_specialists=("fixture-evidence-auditor", "validation-availability-auditor"),
    )


def demo_red_team(snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...]) -> RedTeamReport:
    return RedTeamReport(
        state="WARN",
        findings=(
            "All evidence is synthetic; it cannot support an investment decision.",
            "No live research firm or LEAN validation has been executed.",
        ),
    )


def build_runtime(
    mode: str,
    *,
    store: ResearchStore | None = None,
    live_manifest: Path | None = None,
    manifest_hash: str | None = None,
) -> ResearchRuntime:
    if mode == "live":
        if store is None or live_manifest is None or manifest_hash is None:
            raise ValueError("LIVE_RUNTIME_CONFIGURATION_REQUIRED")
        from money.research.live import build_live_runtime, load_manifest

        return build_live_runtime(store, load_manifest(live_manifest, manifest_hash))
    if mode not in {"demo", "unconfigured"}:
        raise ValueError("unknown research runtime mode")
    instruments: tuple[InstrumentMetadata, ...] = ()
    if mode == "demo":
        instruments = (
            InstrumentMetadata(
                ticker="DEMO.L",
                company="Demonstration plc (synthetic)",
                instrument_type="STOCK",
                quote_currency="GBX",
                isa_available=True,
                currently_available=True,
                business_activities=("software",),
                activities_verified=True,
                verified_at=utc_now(),
                source="Synthetic fixture, not a Trading 212 eligibility assertion",
                provider="money-demo",
                source_id="demo-universe-v1",
            ),
        )
    return ResearchRuntime(
        mode=mode,
        eligibility=Trading212EligibilityAdapter(instruments),
        snapshot_builder=demo_snapshot,
        firms=tuple(DemoFirm(firm) for firm in sorted(FIRST_PASS_FIRMS)) if mode == "demo" else (),
        validate=unavailable_validation,
        audit=demo_audit,
        red_team=demo_red_team,
    )
