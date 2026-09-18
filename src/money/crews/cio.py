"""Native CrewAI supervisory workflow backed by independent calculations."""

from __future__ import annotations

import json
import math
import re
from importlib import import_module
from typing import Any, Literal

from pydantic import Field

from money.adapters.native import (
    POLICY,
    InferenceSession,
    NativeInference,
    NativeRunSettings,
    qualitative_evidence,
    snapshot_payload,
)
from money.adapters.native_attestation import require_pinned_source
from money.adapters.native_qlib import QualifiedLinearModel, feature_values
from money.adapters.upstream import (
    InvalidUpstreamReport,
    QuantBar,
    QuantResearchInput,
    UpstreamUnavailable,
)
from money.schemas.contracts import (
    AuditFinding,
    CIOAuditReport,
    Contract,
    FinancialFact,
    FirmReport,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    RedTeamReport,
    ResearchSnapshot,
    Usage,
    content_hash,
)


class CIOResult(Contract):
    audit: CIOAuditReport
    red_team: RedTeamReport
    provider: str
    model: str
    prompt_version: str = "money-cio-verify-v1"
    upstream_sha: str = "2c24b95eae2b0270b2513f9e24c3f4e536a22f0d"
    usage: Usage = Field(default_factory=Usage)
    runtime: Literal["live", "demo"] = "live"


class SpecialistResult(Contract):
    findings: tuple[AuditFinding, ...] = Field(min_length=1)
    material_disagreement: bool = False


def independent_measurements(snapshot: ResearchSnapshot) -> dict[str, Any]:
    """Recalculate from raw snapshot facts, never from first-pass assertions."""
    bars = [(e, e.payload) for e in sorted(snapshot.evidence, key=lambda e: e.observation_time)
            if isinstance(e.payload, PriceBar)]
    measurements: dict[str, Any] = {"technical": {}, "fundamental": {}}
    if bars:
        record, latest = bars[-1]
        divisor = 100 if latest.currency == "GBX" else 1
        measurements["technical"]["latest_close_gbp"] = {
            "value": str(latest.close / divisor), "evidence_ids": [record.evidence_id],
        }
    if len(bars) >= 21:
        history = bars[-21:-1]
        average = sum(item.volume for _, item in history) / len(history)
        measurements["technical"]["relative_volume_20"] = {
            "value": bars[-1][1].volume / average if average > 0 else None,
            "evidence_ids": [record.evidence_id for record, _ in bars[-21:]],
            "method": "latest volume / mean of previous 20 observations; latest excluded from mean",
        }
    for record in snapshot.evidence:
        fact = record.payload
        if isinstance(fact, FinancialFact):
            measurements["fundamental"].setdefault(fact.metric, []).append({
                "value": str(fact.value), "unit": fact.unit,
                "period_end": fact.period_end.isoformat(), "evidence_ids": [record.evidence_id],
            })
    return measurements


def deterministic_audit(
    snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...], lean: LeanValidationReport,
    qualified_model: QualifiedLinearModel | None = None,
) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    required = snapshot.required_first_pass_firms
    if len(reports) != len(required) or {r.firm for r in reports} != required:
        raise InvalidUpstreamReport("CIO requires configured sealed report identities")
    if lean.snapshot_id != snapshot.snapshot_id or any(
        r.snapshot_id != snapshot.snapshot_id or r.snapshot_hash != snapshot.hash for r in reports
    ):
        raise InvalidUpstreamReport("CIO artifacts do not match the locked snapshot")
    ids = {item.evidence_id for item in snapshot.evidence}
    claims = [claim for report in reports for claim in report.claims]
    if len({claim.claim_id for claim in claims}) != len(claims):
        raise InvalidUpstreamReport("CIO found duplicate claim identity")
    for claim in claims:
        if not claim.evidence_ids or not set(claim.evidence_ids) <= ids:
            raise InvalidUpstreamReport("CIO found untraceable claim evidence")
    for record in snapshot.evidence:
        if not record.available_at(snapshot.cutoff_for(record)) or record.conflicting:
            findings.append(AuditFinding(
                auditor="Point-in-Time Auditor", state="CONTRADICTED",
                explanation="Evidence is unavailable at the decision cutoff or conflicting.",
                evidence_ids=(record.evidence_id,),
            ))
    mandatory = (
        lean.state == "PASS" and lean.observations >= 30 and lean.walk_forward
        and lean.out_of_sample and lean.pit_safe and lean.survivorship_checked
        and lean.costs_included and lean.sensitivity_checked
        and lean.spread_bps is not None and lean.slippage_bps is not None
    )
    if not mandatory:
        findings.append(AuditFinding(
            auditor="LEAN Auditor", state="UNSUPPORTED",
            explanation="LEAN does not establish all required validation controls.",
        ))
    for report in reports:
        if isinstance(report, QlibQuantResearchReport):
            state: Literal["VERIFIED", "UNSUPPORTED", "CONTRADICTED", "WARN"] = "UNSUPPORTED"
            explanation = "An independently loaded qualified model is required to verify Qlib output."
            if qualified_model is not None:
                qualified_model.require_qualified(snapshot.price_cutoff)
                bars = []
                for record in sorted(snapshot.evidence, key=lambda e: e.observation_time):
                    bar = record.payload
                    if isinstance(bar, PriceBar):
                        divisor = 100 if bar.currency == "GBX" else 1
                        bars.append(QuantBar(evidence_id=record.evidence_id,
                            observed_at=record.observation_time, open_gbp=bar.open / divisor,
                            high_gbp=bar.high / divisor, low_gbp=bar.low / divisor,
                            close_gbp=bar.close / divisor, volume=bar.volume))
                values = feature_values(QuantResearchInput(snapshot_id=snapshot.snapshot_id,
                    snapshot_hash=snapshot.hash, ticker=snapshot.ticker, cutoff=snapshot.price_cutoff,
                    minimum_horizon_days=1, maximum_horizon_days=30, bars=tuple(bars)))
                artifact = qualified_model.artifact
                expected = sum(a * b for a, b in zip(values, artifact.coefficients, strict=True)) + artifact.intercept
                matched = report.prediction_score is not None and math.isclose(
                    expected, report.prediction_score, rel_tol=1e-9, abs_tol=1e-12
                ) and report.model_version == artifact.model_version
                state = "VERIFIED" if matched else "CONTRADICTED"
                explanation = "Independently reconstructed PIT features and recomputed the qualified linear prediction."
            for claim in report.claims:
                if claim.claim_id == "qlib:prediction":
                    findings.append(AuditFinding(auditor="Qlib Quant Auditor", claim_id=claim.claim_id,
                        state=state, explanation=explanation, evidence_ids=claim.evidence_ids))
        if isinstance(report, QlibQuantResearchReport) and report.rank is not None:
            findings.append(AuditFinding(
                auditor="Qlib Quant Auditor", state="UNSUPPORTED",
                explanation="Cross-sectional rank cannot be independently recalculated from "
                "this single-instrument snapshot; a frozen comparison universe is required.",
                claim_id=report.claims[0].claim_id if report.claims else None,
                evidence_ids=report.claims[0].evidence_ids if report.claims else (),
            ))
    measurements = independent_measurements(snapshot)
    for claim in claims:
        match = re.search(r"relative\s+volume(?:\s+is|\s+of|\s*[:=])?\s*(\d+(?:\.\d+)?)", claim.statement, re.I)
        if match:
            measured = measurements["technical"].get("relative_volume_20")
            present = measured is not None and measured["value"] is not None
            matches = present and math.isclose(float(match.group(1)), measured["value"], rel_tol=0.01)
            findings.append(AuditFinding(auditor="Technical Auditor", claim_id=claim.claim_id,
                state="VERIFIED" if matches else "CONTRADICTED" if present else "UNSUPPORTED",
                explanation="Recomputed relative volume as latest / previous 20 mean, with 1% rounding tolerance.",
                evidence_ids=tuple(measured["evidence_ids"]) if measured else claim.evidence_ids))
        if re.search(r"cash(?:\s+position)?\s+(?:has\s+)?(?:strengthened|increased|improved)", claim.statement, re.I):
            cash = measurements["fundamental"].get("cash", [])
            cash = sorted(cash, key=lambda row: row["period_end"])
            comparable = len(cash) >= 2 and cash[-1]["unit"] == cash[-2]["unit"] and cash[-1]["period_end"] != cash[-2]["period_end"]
            increased = comparable and float(cash[-1]["value"]) > float(cash[-2]["value"])
            findings.append(AuditFinding(auditor="Fundamental Auditor", claim_id=claim.claim_id,
                state="VERIFIED" if increased else "CONTRADICTED" if comparable else "UNSUPPORTED",
                explanation="Compared the latest two published cash facts with matching units and distinct periods.",
                evidence_ids=tuple(e for row in cash[-2:] for e in row["evidence_ids"]) if comparable else claim.evidence_ids))
    return tuple(findings)


def _crewai_llm(session: InferenceSession) -> Any:
    try:
        module = import_module("crewai.llms.base_llm")
    except ImportError as exc:
        raise UpstreamUnavailable("the CrewAI runtime is not installed") from exc

    class MoneyCioLLM(module.BaseLLM):  # type: ignore[name-defined]
        def call(self, messages: Any, tools: object = None, callbacks: object = None,
                 available_functions: object = None, **kwargs: object) -> str:
            if tools or available_functions:
                raise InvalidUpstreamReport("CIO workflow attempted to enable external tools")
            return session.complete(
                POLICY + "\nYou are a supervisory auditor. Verify claims independently from "
                "the frozen evidence and Money's recomputed measurements. Unsupported claims "
                "must remain UNSUPPORTED. Return the requested structured result.",
                json.dumps(messages) if not isinstance(messages, str) else messages,
            )

        def supports_function_calling(self) -> bool:
            return False

        def supports_stop_words(self) -> bool:
            return False

        def get_context_window_size(self) -> int:
            return 32768

    return MoneyCioLLM(model=session.model, provider=session.provider, temperature=0)


class CrewAINativeRunner:
    def __init__(self, inference: NativeInference, settings: NativeRunSettings | None = None,
                 qualified_model: QualifiedLinearModel | None = None) -> None:
        self.inference, self.settings = inference, settings or NativeRunSettings()
        self.qualified_model = qualified_model

    def __call__(self, snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...],
                 lean: LeanValidationReport) -> CIOResult:
        if self.settings.verify_source_pin:
            require_pinned_source("crewai")
        try:
            # CrewAI initializes its optional Platform trace client at import and
            # otherwise reads the user's saved platform token even with tracing
            # disabled. Money has no platform-auth capability. This process-local
            # adapter override never edits the pinned upstream checkout.
            auth: Any = import_module("crewai_core.auth.token")

            def no_platform_auth() -> str:
                raise auth.AuthError("Money does not enable CrewAI Platform authentication")

            original_auth = auth.get_auth_token
            auth.get_auth_token = no_platform_auth
            try:
                native = import_module("crewai")
                flows = import_module("crewai.flow.flow")
            finally:
                auth.get_auth_token = original_auth
        except ImportError as exc:
            raise UpstreamUnavailable("the CrewAI runtime is not installed") from exc
        session = InferenceSession(self.inference, self.settings, usage_mode=snapshot.usage_mode)
        inference = self.inference
        runtime: Literal["live", "demo"] = "live" if self.settings.verify_source_pin else "demo"
        llm = _crewai_llm(session)
        facts = snapshot_payload(snapshot)
        fixed_findings = deterministic_audit(snapshot, reports, lean, self.qualified_model)
        measurements = independent_measurements(snapshot)
        claims = {claim.claim_id: claim for report in reports for claim in report.claims}
        allowed_ids = {record.evidence_id for record in qualitative_evidence(snapshot)}
        specialists = {"technical": "Technical Auditor", "fundamental": "Fundamental Auditor",
                       "catalyst": "Catalyst Auditor", "quantitative": "Qlib Quant Auditor",
                       "risk": "Risk Auditor"}
        active = tuple(specialists[family] for family in sorted({c.family for c in claims.values()}))

        def run_task(role: str, description: str, schema: type[Contract]) -> Contract:
            agent = native.Agent(
                role=role, goal="Independently verify frozen evidence and expose missing support",
                backstory=POLICY, llm=llm, tools=[], allow_delegation=False,
                allow_code_execution=False, max_iter=3, max_retry_limit=0,
                max_execution_time=max(1, int(self.settings.timeout_seconds)),
                verbose=False, memory=False, cache=False,
            )
            task = native.Task(description=description, expected_output="Strict schema-valid JSON",
                               agent=agent, output_pydantic=schema)
            result = native.Crew(agents=[agent], tasks=[task], memory=False,
                                 cache=False, verbose=False, share_crew=False,
                                 tracing=False).kickoff()
            output = getattr(result, "pydantic", None)
            if output is None:
                raise InvalidUpstreamReport("CrewAI did not return validated structured output")
            return schema.model_validate_json(output.model_dump_json())

        class MoneyCioFlow(flows.Flow):  # type: ignore[name-defined]
            @flows.start()
            def verify(self) -> CIOAuditReport:
                findings = list(fixed_findings)
                disagreement = False
                for family in sorted({c.family for c in claims.values()}):
                    selected = [c.model_dump(mode="json") for c in claims.values() if c.family == family]
                    description = (
                        facts + "\nINDEPENDENT_MEASUREMENTS=" + json.dumps(measurements)
                        + "\nCLAIMS_TO_AUDIT=" + json.dumps(selected)
                        + "\nLEAN_VALIDATION=" + lean.model_dump_json()
                        + "\nFor every selected claim return one finding citing snapshot evidence. "
                        "Compare every number with independent measurements. A citation existing "
                        "does not establish a claim. Explain verification or explicitly mark "
                        "UNSUPPORTED/CONTRADICTED; missing data can never be VERIFIED."
                    )
                    result = run_task(specialists[family], description, SpecialistResult)
                    assert isinstance(result, SpecialistResult)
                    selected_ids = {c["claim_id"] for c in selected}
                    result_ids = [finding.claim_id for finding in result.findings]
                    if set(result_ids) != selected_ids or len(result_ids) != len(selected_ids):
                        raise InvalidUpstreamReport("CIO specialist omitted or duplicated a claim")
                    for finding in result.findings:
                        if finding.auditor != specialists[family]:
                            raise InvalidUpstreamReport("CIO specialist identity mismatch")
                        if not set(finding.evidence_ids) <= allowed_ids:
                            raise InvalidUpstreamReport("CIO cited evidence outside snapshot")
                        if finding.state == "VERIFIED" and (
                            not finding.evidence_ids or not set(finding.evidence_ids).intersection(
                                claims[finding.claim_id or ""].evidence_ids
                            )
                        ):
                            raise InvalidUpstreamReport("CIO verification lacks claim evidence")
                    findings.extend(result.findings)
                    disagreement = disagreement or result.material_disagreement
                return CIOAuditReport(
                    snapshot_id=snapshot.snapshot_id, completed=bool(claims),
                    findings=tuple(findings), active_specialists=("Evidence Auditor", "LEAN Auditor", *active),
                    material_disagreement=disagreement,
                )

            @flows.listen(verify)
            def red_team(self, audit: CIOAuditReport) -> CIOResult:
                result = run_task("Red Team", facts + "\nINITIAL_AUDIT=" + audit.model_dump_json()
                    + "\nInvestigate only negative evidence, liquidity, dilution, debt, spread, "
                    "PIT leakage, source conflicts, common-source groupthink, stale catalysts, "
                    "overfit and unsupported bullish assumptions. Return PASS, WARN, or VETO. "
                    "No reassurance or consensus vote. Missing critical controls prevent PASS.",
                    RedTeamReport)
                assert isinstance(result, RedTeamReport)
                if fixed_findings and result.state == "PASS":
                    result = RedTeamReport(state="WARN", findings=(*result.findings,
                        "Deterministic verification found incomplete validation or evidence controls."))
                return CIOResult(audit=audit, red_team=result, provider=session.provider,
                                 model=session.model, usage=inference.usage(), runtime=runtime)

        try:
            output = MoneyCioFlow().kickoff()
        except Exception:
            if session.failure is not None:
                raise session.failure from None
            raise
        return CIOResult.model_validate(output)


class CrewAICioAdapter:
    """One native Flow per locked report set; red team cannot precede its audit."""

    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self._key: str | None = None
        self._result: CIOResult | None = None

    @property
    def last_result(self) -> CIOResult | None:
        return self._result

    def audit(self, snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...],
              lean: LeanValidationReport) -> CIOAuditReport:
        if self._runner is None:
            raise UpstreamUnavailable("CrewAI native workflow is not configured")
        deterministic_audit(snapshot, reports, lean)
        self._key, self._result = None, None
        result = CIOResult.model_validate_json(self._runner(snapshot, reports, lean).model_dump_json())
        if result.runtime != "live":
            raise InvalidUpstreamReport("unqualified CrewAI runtime cannot be admitted as live audit")
        if result.audit.snapshot_id != snapshot.snapshot_id:
            raise InvalidUpstreamReport("CrewAI returned another snapshot's audit")
        self._key = content_hash({"snapshot": snapshot.hash, "reports": [r.model_dump(mode="json") for r in reports]})
        self._result = result
        return result.audit

    def red_team(self, snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...]) -> RedTeamReport:
        key = content_hash({"snapshot": snapshot.hash, "reports": [r.model_dump(mode="json") for r in reports]})
        if self._key != key or self._result is None:
            raise InvalidUpstreamReport("red team is unavailable before matching native CIO audit")
        return self._result.red_team
