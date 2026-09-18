"""Research-only LEAN admission with explicit assumptions, not signed cost reviews.

This seam never constructs a production ``LeanStudyQualification``. It reuses
the pinned, networkless, order-free LEAN container and retains its unqualified
production report alongside the narrower experimental result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from money.adapters.native import NativeDeadline
from money.adapters.upstream import (
    UPSTREAM_SHAS,
    InvalidUpstreamReport,
    UnsupportedSnapshotData,
    UpstreamUnavailable,
)
from money.backtest.lean import (
    LEAN_SHA,
    LeanContainerRunner,
    LeanContainerSettings,
    LeanCostAssumptions,
    LeanStudyParameters,
    historical_dataset_hash,
)
from money.qualification.core import QualificationContext
from money.research.market_data import HistoricalMarketData
from money.schemas.contracts import (
    Contract,
    FirmReport,
    LeanValidationReport,
    PriceBar,
    ResearchSnapshot,
    content_hash,
)


class ResearchCostAssumptions(Contract):
    """Scenario assumptions; deliberately no reviewer or broker-observation claim."""

    version: str = Field(min_length=1, max_length=100)
    basis: Literal["ASSUMED_NOT_OBSERVED_BROKER_COSTS"] = "ASSUMED_NOT_OBSERVED_BROKER_COSTS"
    spread_bps: float = Field(ge=0, lt=10000)
    slippage_bps: float = Field(ge=0, lt=10000)
    fees_bps: float = Field(default=0, ge=0, lt=10000)
    rationale: str = Field(min_length=1, max_length=1500)

    @model_validator(mode="after")
    def finite_total(self) -> Self:
        if self.spread_bps + self.slippage_bps + self.fees_bps >= 10000:
            raise ValueError("RESEARCH_ASSUMED_COSTS_INVALID")
        return self

    def lean_costs(self, start: datetime, end: datetime) -> LeanCostAssumptions:
        return LeanCostAssumptions(
            version=self.version,
            source=f"{self.basis}: {self.rationale}",
            effective_from=start,
            effective_to=end + timedelta(seconds=1),
            round_trip_cost_bps=self.spread_bps + self.slippage_bps + self.fees_bps,
            spread_bps=self.spread_bps,
            slippage_bps=self.slippage_bps,
            applicability_reasons=(
                "Predeclared personal research sensitivity scenario; not measured broker costs.",
            ),
        )


class ResearchBacktestReadiness(Contract):
    scope: Literal["RESEARCH_TESTING_ONLY"] = "RESEARCH_TESTING_ONLY"
    state: Literal["BLOCKED", "BACKTEST_READY"]
    blockers: tuple[str, ...]
    snapshot_hash: str
    market_data_hash: str | None = None
    first_pass_report_hashes: tuple[str, ...] = ()
    assumptions: ResearchCostAssumptions | None = None
    parameters: LeanStudyParameters
    commercial_release_permitted: Literal[False] = False
    limitations: tuple[str, ...] = (
        "Retrospective study of a currently selected instrument, not a survivorship-corrected universe study.",
        "Assumed costs are not observed broker quotes; no trade or execution approval is granted.",
    )


def _first_pass_blockers(snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...]) -> list[str]:
    if snapshot.qlib_enabled:
        return ["RESEARCH_TESTING_QLIB_MUST_BE_EXPLICITLY_DISABLED"]
    if len(reports) != 2 or {report.firm for report in reports} != {"tradingagents", "ai_hedge_fund"}:
        return ["INDEPENDENT_FIRST_PASS_REPORTS_REQUIRED"]
    allowed = {record.evidence_id for record in snapshot.evidence}
    for value in reports:
        report = FirmReport.model_validate_json(value.model_dump_json())
        if (
            report.snapshot_id != snapshot.snapshot_id
            or report.snapshot_hash != snapshot.hash
            or report.runtime != "live"
            or report.upstream_sha != UPSTREAM_SHAS[report.firm]
            or report.created_at < snapshot.created_at
            or len({claim.claim_id for claim in report.claims}) != len(report.claims)
            or any(not claim.evidence_ids or not set(claim.evidence_ids) <= allowed for claim in report.claims)
        ):
            return ["INDEPENDENT_FIRST_PASS_REPORTS_INVALID"]
    return []


def prepare_backtest(
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
    market: HistoricalMarketData | None,
    assumptions: ResearchCostAssumptions | None,
    parameters: LeanStudyParameters | None = None,
    *,
    first_pass_locked: bool = False,
) -> ResearchBacktestReadiness:
    """Require only inputs actually used by the fixed price-based LEAN study.

    No Companies House, ethics, issuer jurisdiction, Fundamentals or manual cost
    approval enters this decision. Historical prices and their availability,
    action basis and exact mapping remain genuine technical requirements.
    """
    snapshot = ResearchSnapshot.model_validate_json(snapshot.model_dump_json())
    parameters = parameters or LeanStudyParameters()
    blockers = []
    if getattr(snapshot, "purpose", None) != "RESEARCH_TESTING":
        blockers.append("EXPLICIT_RESEARCH_TESTING_SNAPSHOT_REQUIRED")
    if not first_pass_locked:
        blockers.append("FIRST_PASS_LOCKED_REQUIRED")
    blockers.extend(_first_pass_blockers(snapshot, reports))
    if assumptions is None:
        blockers.append("DOCUMENTED_RESEARCH_COST_ASSUMPTIONS_REQUIRED")
    if market is None:
        blockers.append("REAL_HISTORICAL_MARKET_DATA_REQUIRED")
    else:
        market = HistoricalMarketData.model_validate_json(market.model_dump_json())
        if (
            market.mapping.trading212_id != snapshot.instrument.source_id
            or market.mapping.currency != snapshot.instrument.quote_currency
        ):
            blockers.append("EXACT_MARKET_SECURITY_MAPPING_REQUIRED")
        if not market.observed_at <= snapshot.created_at < market.valid_until:
            blockers.append("MARKET_DATA_SNAPSHOT_FRESHNESS_REQUIRED")
        expected = market.evidence_records(snapshot.snapshot_id)
        actual = tuple(record for record in snapshot.evidence if isinstance(record.payload, PriceBar))
        if {record.hash for record in actual} != {record.hash for record in expected}:
            blockers.append("FROZEN_MARKET_DATA_MISMATCH")
        if not market.historical_availability_verified:
            blockers.append("HISTORICAL_PRICE_AVAILABILITY_EVIDENCE_REQUIRED")
        if not market.corporate_actions_complete or market.adjustment_basis not in {
            "RAW_NO_ACTIONS", "SPLIT_ADJUSTED_TOTAL_RETURN"
        }:
            blockers.append("STRATEGY_CORPORATE_ACTION_HANDLING_REQUIRED")
        if len(market.bars) < 100:
            blockers.append("WALK_FORWARD_OOS_HISTORY_REQUIRED")
        if any(not record.available_at(snapshot.price_cutoff) for record in expected):
            if "HISTORICAL_PRICE_AVAILABILITY_EVIDENCE_REQUIRED" not in blockers:
                blockers.append("HISTORICAL_PRICE_AVAILABILITY_EVIDENCE_REQUIRED")
    return ResearchBacktestReadiness(
        state="BLOCKED" if blockers else "BACKTEST_READY",
        blockers=tuple(dict.fromkeys(blockers)),
        snapshot_hash=snapshot.hash,
        market_data_hash=market.hash if market else None,
        first_pass_report_hashes=tuple(content_hash(report) for report in reports),
        assumptions=assumptions,
        parameters=parameters,
    )


class ResearchLeanResult(Contract):
    scope: Literal["RESEARCH_TESTING_ONLY"] = "RESEARCH_TESTING_ONLY"
    state: Literal["BLOCKED", "LEAN_EXECUTED", "LEAN_VALIDATED"]
    execution_performed: bool = False
    readiness: ResearchBacktestReadiness
    blockers: tuple[str, ...] = ()
    runtime_image: str | None = None
    upstream_sha: str = LEAN_SHA
    original_lean_report: LeanValidationReport | None = None
    hypothesis_result: Literal["PASS", "FAIL", "INSUFFICIENT_EVIDENCE"] = "INSUFFICIENT_EVIDENCE"
    commercial_release_permitted: Literal[False] = False
    production_qualified: Literal[False] = False


class ResearchBacktestConfiguration(Contract):
    """Optional executable research settings, never signatures or fabricated facts."""

    schema_version: Literal["money-research-backtest-v1"] = "money-research-backtest-v1"
    assumptions: ResearchCostAssumptions | None = None
    runtime: LeanContainerSettings | None = None
    parameters: LeanStudyParameters = Field(default_factory=LeanStudyParameters)


def run_research_lean(
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
    market: HistoricalMarketData | None,
    assumptions: ResearchCostAssumptions | None,
    settings: LeanContainerSettings | None,
    parameters: LeanStudyParameters | None = None,
    *,
    first_pass_locked: bool = False,
) -> ResearchLeanResult:
    readiness = prepare_backtest(
        snapshot, reports, market, assumptions, parameters, first_pass_locked=first_pass_locked
    )
    blockers = list(readiness.blockers)
    if settings is None:
        blockers.append("PINNED_LEAN_RUNTIME_IMAGE_REQUIRED")
    if blockers:
        return ResearchLeanResult(state="BLOCKED", readiness=readiness, blockers=tuple(blockers))
    assert settings is not None and market is not None and assumptions is not None
    costs = assumptions.lean_costs(market.bars[0].timestamp, snapshot.price_cutoff)
    runner = LeanContainerRunner(settings, costs, readiness.parameters)
    try:
        report = runner(snapshot, reports)
    except NativeDeadline:
        code = "LEAN_RUNTIME_TIMEOUT"
    except UpstreamUnavailable:
        code = "PINNED_LEAN_RUNTIME_UNAVAILABLE"
    except (UnsupportedSnapshotData, InvalidUpstreamReport, ValueError, OSError):
        code = "LEAN_DATA_OR_RUNTIME_RESULT_INVALID"
    else:
        records = market.evidence_records(snapshot.snapshot_id)
        try:
            details = json.loads(report.findings[0])
            if (
                details["image"] != settings.image
                or details["upstream_sha"] != LEAN_SHA
                or details["dataset_hash"] != historical_dataset_hash(records)
                or details["parameter_hash"] != content_hash(readiness.parameters)
                or details["statistics"]["bars_received"] != len(records)
                or report.snapshot_id != snapshot.snapshot_id
                or report.runner_version != "money-lean-oci-v1"
            ):
                raise ValueError("LEAN_IDENTITY_MISMATCH")
            covered = (
                report.out_of_sample and report.walk_forward and report.pit_safe
                and report.costs_included and report.sensitivity_checked
                and report.observations >= readiness.parameters.minimum_oos_observations
                and report.maximum_drawdown is not None and report.mae is not None
                and report.mfe is not None
                and len(details["statistics"]["regimes_observed"]) >= 2
            )
            statistics = details["statistics"]
            failed = covered and (
                statistics["mean_return"] <= 0
                or statistics["cost_stress_mean_return"] <= 0
                or statistics["maximum_drawdown"] > readiness.parameters.maximum_drawdown
                or any(scenario["mean_return"] <= 0 for scenario in statistics["parameter_sensitivity"])
            )
        except (ValueError, TypeError, KeyError, IndexError):
            code = "LEAN_DATA_OR_RUNTIME_RESULT_INVALID"
        else:
            return ResearchLeanResult(
                state="LEAN_VALIDATED" if covered else "LEAN_EXECUTED",
                execution_performed=True,
                readiness=readiness,
                blockers=() if covered else ("LEAN_OOS_WALK_FORWARD_OR_SENSITIVITY_COVERAGE_REQUIRED",),
                runtime_image=settings.image,
                original_lean_report=report,
                hypothesis_result=("FAIL" if failed else "PASS") if covered else "INSUFFICIENT_EVIDENCE",
            )
    return ResearchLeanResult(
        state="BLOCKED", readiness=readiness, blockers=(code,), runtime_image=settings.image
    )


def run_configured_research_lean(
    ctx: QualificationContext,
    snapshot: ResearchSnapshot,
    firms: Mapping[str, Any],
    market: HistoricalMarketData | None,
) -> dict[str, Any]:
    """Resumable runner seam: an unsigned cost scenario is fine, missing data is not.

    Existing source archives must supply a validated market dataset to this
    function. Merely filling an input with ``pit_safe=true`` is not an archive
    qualification mechanism and is deliberately not offered by this template.
    """
    ctx.template(
        "inputs/research-backtest.json",
        ResearchBacktestConfiguration().model_dump(mode="json"),
    )
    ctx.template(
        "inputs/research-backtest.schema.json", ResearchBacktestConfiguration.model_json_schema()
    )
    configuration = ResearchBacktestConfiguration.model_validate(
        ctx.read_json("inputs/research-backtest.json")
    )
    reports: tuple[FirmReport, ...] = ()
    locked = False
    if firms.get("complete") is True:
        for reference in firms.get("artifacts", []):
            if not isinstance(reference, (list, tuple)) or len(reference) != 2:
                continue
            sealed = json.loads(ctx.verify_artifact(reference[0], reference[1]))
            if (
                isinstance(sealed, dict)
                and sealed.get("state") == "FIRST_PASS_LOCKED"
                and sealed.get("snapshot_hash") == snapshot.hash
                and sealed.get("qlib_enabled") is False
                and sealed.get("reports") == firms.get("reports")
            ):
                reports = tuple(FirmReport.model_validate(item) for item in sealed["reports"])
                locked = True
                break
    result = run_research_lean(
        snapshot, reports, market, configuration.assumptions, configuration.runtime,
        configuration.parameters, first_pass_locked=locked,
    )
    actions = {
        "FIRST_PASS_LOCKED_REQUIRED": "Complete and persist both independent native reports before LEAN.",
        "INDEPENDENT_FIRST_PASS_REPORTS_REQUIRED": "Run real TradingAgents and AI Hedge Fund on this frozen snapshot; do not substitute reports.",
        "REAL_HISTORICAL_MARKET_DATA_REQUIRED": "Supply real historical OHLCV through a configured provider with an exact security mapping.",
        "HISTORICAL_PRICE_AVAILABILITY_EVIDENCE_REQUIRED": "Supply archived price availability for the study's historical decisions; a current history download is not historical PIT proof.",
        "STRATEGY_CORPORATE_ACTION_HANDLING_REQUIRED": "Provide complete action coverage and a supported adjustment basis for the actual tested history.",
        "DOCUMENTED_RESEARCH_COST_ASSUMPTIONS_REQUIRED": "Set documented assumed spread, slippage and fees in inputs/research-backtest.json; no signed review is required.",
        "PINNED_LEAN_RUNTIME_IMAGE_REQUIRED": "Set runtime.image in inputs/research-backtest.json to the built LEAN image's actual sha256 digest; the existing pinned upstream label is checked.",
        "PINNED_LEAN_RUNTIME_UNAVAILABLE": "Make Docker and the digest-pinned LEAN image available in this research environment.",
        "WALK_FORWARD_OOS_HISTORY_REQUIRED": "Provide enough genuine historical sessions for predeclared walk-forward and OOS measurements.",
        "LEAN_OOS_WALK_FORWARD_OR_SENSITIVITY_COVERAGE_REQUIRED": "Review the genuine LEAN metrics and provide adequate predeclared OOS, walk-forward and sensitivity coverage.",
    }
    for code in result.blockers:
        ctx.block(code, actions.get(code, "Resolve the recorded genuine market/runtime prerequisite without fabricating evidence."))
    value = result.model_dump(mode="json")
    value["complete"] = result.state == "LEAN_VALIDATED"
    value["artifacts"] = [ctx.artifact(value)]
    ctx.write_json("outputs/research-lean-result.json", value)
    return value
