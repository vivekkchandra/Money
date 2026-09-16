"""Post-lock native correspondence and separately evidenced verification.

Each respondent receives its own report only. The independent verifier is a
downstream capability and may receive the sealed set, never a first-pass tool.
All network-bearing callables must run through BoundedNativeRunner in production.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
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
    text_only,
)
from money.adapters.native_attestation import require_pinned_source
from money.adapters.native_qlib import QualifiedLinearModel, feature_values
from money.adapters.native_qualitative import _EphemeralPromptCache, _native_chat
from money.adapters.upstream import (
    UPSTREAM_SHAS,
    InvalidUpstreamReport,
    QuantBar,
    QuantResearchInput,
    UpstreamUnavailable,
)
from money.backtest.lean import LEAN_SHA
from money.crews.cio import CIOResult, _crewai_llm, deterministic_audit
from money.crews.cross_examination import (
    Challenge,
    ChallengeInvocation,
    ChallengeResponse,
    ChallengeSourceCheck,
    ChallengeVerification,
)
from money.schemas.contracts import (
    AuditFinding,
    Contract,
    DocumentFact,
    FinancialFact,
    FirmReport,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    ResearchSnapshot,
    Usage,
    content_hash,
)

CIO_SHA = str(CIOResult.model_fields["upstream_sha"].default)


class ResponseContent(Contract):
    position: Literal["MAINTAIN", "WITHDRAW", "INSUFFICIENT_EVIDENCE"]
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...] = Field(max_length=100)


class SourceCheck(Contract):
    evidence_id: str
    quoted_fact: str = Field(min_length=5, max_length=500)


class VerificationContent(Contract):
    state: Literal["VERIFIED", "UNSUPPORTED", "CONTRADICTED", "WARN"]
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...] = Field(max_length=100)
    source_checks: tuple[SourceCheck, ...] = Field(max_length=24)


def _validate_request(snapshot: ResearchSnapshot, report: FirmReport | None,
                      challenge: Challenge, round_number: int, firm: str) -> None:
    ResearchSnapshot.model_validate_json(snapshot.model_dump_json())
    if not 1 <= round_number <= 2 or challenge.respondent != firm:
        raise InvalidUpstreamReport("challenge respondent or round differs")
    qualitative_evidence(snapshot)  # Validate the complete snapshot before context selection.
    if (report is None or report.firm != firm or report.snapshot_id != snapshot.snapshot_id
            or report.snapshot_hash != snapshot.hash or content_hash(report) != challenge.original_report_hash):
        raise InvalidUpstreamReport("challenge must reference the respondent's own sealed report")
    original = next((claim for claim in report.claims if claim.claim_id == challenge.claim_id), None)
    if original is None or challenge.original_claim != original:
        raise InvalidUpstreamReport("challenge original claim is absent or differs from the sealed report")


def _context(snapshot: ResearchSnapshot, own_report: FirmReport, challenge: Challenge, round_number: int) -> str:
    return snapshot_payload(snapshot) + "\nUNTRUSTED_POST_LOCK_CORRESPONDENCE=" + json.dumps({
        "round": round_number, "own_sealed_report": own_report.model_dump(mode="json"),
        "own_report_hash": content_hash(own_report), "challenge": challenge.model_dump(mode="json"),
        "original_claim_hash": content_hash(challenge.original_claim),
        "policy": "Address this challenge only. Do not replace the original sealed report.",
    })


def _parse_response(raw: str, snapshot: ResearchSnapshot, permitted_evidence_ids: tuple[str, ...]) -> ResponseContent:
    try:
        output = ResponseContent.model_validate_json(raw)
    except ValueError as exc:
        raise InvalidUpstreamReport("native challenge response is malformed") from exc
    admitted = {record.evidence_id for record in qualitative_evidence(snapshot)}.intersection(permitted_evidence_ids)
    if (not set(output.evidence_ids) <= admitted or len(set(output.evidence_ids)) != len(output.evidence_ids)
            or output.position == "MAINTAIN" and not output.evidence_ids):
        raise InvalidUpstreamReport("challenge response lacks admitted supporting evidence")
    if re.search(r"\b(?:buy|sell|execute|submit)\s+(?:now|order)|\b\d+(?:\.\d+)?%\s+confiden",
                 output.explanation, re.I):
        raise InvalidUpstreamReport("challenge response contains prohibited instructions or confidence")
    return output.model_copy(update={"explanation": text_only(output.explanation, 4000)})


def _response(snapshot: ResearchSnapshot, challenge: Challenge, output: ResponseContent,
              invocation: ChallengeInvocation) -> ChallengeResponse:
    return ChallengeResponse(challenge_id=challenge.challenge_id, respondent=challenge.respondent,
        snapshot_hash=snapshot.hash, original_report_hash=challenge.original_report_hash,
        position=output.position, explanation=output.explanation, evidence_ids=output.evidence_ids,
        invocation=invocation)


class NativeFirmChallengeRunner:
    """One native research-role reconsideration, with a strict Money-only response."""

    def __init__(self, firm: Literal["tradingagents", "ai_hedge_fund"],
                 inference: NativeInference, settings: NativeRunSettings | None = None) -> None:
        self.firm, self.inference = firm, inference
        supplied = settings or NativeRunSettings()
        self.settings = replace(supplied, max_calls=min(supplied.max_calls, 2 if firm == "tradingagents" else 1))

    def __call__(self, snapshot: ResearchSnapshot, own_report: FirmReport | None,
                 challenge: Challenge, round_number: int) -> ChallengeResponse:
        _validate_request(snapshot, own_report, challenge, round_number, self.firm)
        assert own_report is not None
        package = "tradingagents" if self.firm == "tradingagents" else "hedge_fund"
        if self.settings.verify_source_pin:
            require_pinned_source(package)
        session = InferenceSession(self.inference, self.settings)
        context = _context(snapshot, own_report, challenge, round_number)
        schema_instruction = ("\nRespond to the supplied challenge, not a trading decision. "
            "MAINTAIN requires evidence supporting the unchanged original claim; otherwise withdraw "
            "or explicitly record insufficient evidence. Cite only the challenge's explicit evidence_ids; "
            "an empty set grants no evidence permission. No confidence number. Return strict JSON: "
            + json.dumps(ResponseContent.model_json_schema()))
        if self.firm == "tradingagents":
            try:
                native = import_module("tradingagents.agents.researchers.bear_researcher")
            except ImportError as exc:
                raise UpstreamUnavailable("pinned TradingAgents challenge runtime is unavailable") from exc
            chat = _native_chat(session, context)
            state = {
                "company_of_interest": snapshot.ticker, "asset_type": "stock",
                "market_report": own_report.conclusion, "sentiment_report": "No separate evidence.",
                "news_report": "Use the Money snapshot only.", "fundamentals_report": "Use Money facts only.",
                "investment_debate_state": {"history": "", "bear_history": "", "bull_history": "",
                    "current_response": challenge.question, "count": 0},
            }
            reconsidered = native.create_bear_researcher(chat)(state)
            notes = reconsidered["investment_debate_state"]["current_response"]
            output = _parse_response(session.complete(POLICY + schema_instruction,
                context + "\nUNTRUSTED_NATIVE_RECONSIDERATION=" + json.dumps(notes)), snapshot, challenge.evidence_ids)
        else:
            output = self._aihf(session, context, schema_instruction, snapshot, challenge.evidence_ids)
        return _response(snapshot, challenge, output, ChallengeInvocation(component=self.firm,
            provider=session.provider, model=session.model, calls=session.calls,
            prompt_version=f"money-{self.firm}-challenge-v1", upstream_sha=UPSTREAM_SHAS[self.firm],
            usage=self.inference.usage(), runtime="live" if self.settings.verify_source_pin else "demo"))

    @staticmethod
    def _aihf(session: InferenceSession, context: str, instruction: str,
              snapshot: ResearchSnapshot, permitted_evidence_ids: tuple[str, ...]) -> ResponseContent:
        try:
            persona = import_module("hedge_fund.signals.buffett").BuffettAgent
        except ImportError as exc:
            raise UpstreamUnavailable("pinned AI-HF challenge runtime is unavailable") from exc
        native_prompt = persona.get_system_prompt

        class SnapshotView:
            ticker = snapshot.ticker
            as_of = snapshot.fundamental_cutoff.date().isoformat()
            content_hash = snapshot.hash

            def render(self) -> str:
                return context

        view = SnapshotView()

        class MoneyPersona(persona):  # type: ignore[misc, valid-type]
            def get_system_prompt(self) -> str:
                return POLICY + "\nNative research perspective:\n" + native_prompt(self).split("Signal rules:", 1)[0] + instruction

            def build_snapshot(self, ticker: str, date: str, data_client: object) -> SnapshotView:
                if ticker != view.ticker or date != view.as_of:
                    raise InvalidUpstreamReport("native challenge tried to leave its snapshot")
                return view

            def _parse(self, raw: str) -> dict[str, Any]:
                return _parse_response(raw, snapshot, permitted_evidence_ids).model_dump(mode="json")

            def _to_signal(self, ticker: str, date: str, parsed: dict[str, Any],
                           key: str, snapshot: object, cached: bool) -> ResponseContent:
                return ResponseContent.model_validate(parsed)

            def _abstain(self, ticker: str, date: str, reason: str) -> None:
                if session.failure is not None:
                    raise session.failure
                raise InvalidUpstreamReport("AI-HF challenge output could not be admitted")

        return MoneyPersona(llm=session, cache=_EphemeralPromptCache()).predict(view.ticker, view.as_of, object())


def _quant_finding(snapshot: ResearchSnapshot, report: QlibQuantResearchReport,
                   model: QualifiedLinearModel | None, claim_id: str | None) -> AuditFinding:
    state: Literal["VERIFIED", "UNSUPPORTED", "CONTRADICTED"] = "UNSUPPORTED"
    explanation = "A separately qualified model and exact prediction claim are required."
    cited: tuple[str, ...] = ()
    if model is not None and claim_id == "qlib:prediction" and report.rank is None:
        model.require_qualified(snapshot.price_cutoff)
        bars = []
        for record in sorted(snapshot.evidence, key=lambda record: record.observation_time):
            if isinstance(record.payload, PriceBar):
                payload = record.payload
                scale = 100 if payload.currency == "GBX" else 1
                bars.append(QuantBar(evidence_id=record.evidence_id, observed_at=record.observation_time,
                    open_gbp=payload.open / scale, high_gbp=payload.high / scale,
                    low_gbp=payload.low / scale, close_gbp=payload.close / scale, volume=payload.volume))
        values = feature_values(QuantResearchInput(snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash,
            ticker=snapshot.ticker, cutoff=snapshot.price_cutoff, minimum_horizon_days=1,
            maximum_horizon_days=30, bars=tuple(bars)))
        expected = sum(a * b for a, b in zip(values, model.artifact.coefficients, strict=True)) + model.artifact.intercept
        matched = report.prediction_score is not None and math.isclose(expected, report.prediction_score,
            rel_tol=1e-9, abs_tol=1e-12) and report.model_version == model.artifact.model_version
        state = "VERIFIED" if matched else "CONTRADICTED"
        cited = tuple(bar.evidence_id for bar in bars[-21:])
        explanation = "Recomputed Money PIT features and qualified linear prediction; no cross-sectional rank or native rerun claimed."
    return AuditFinding(auditor="Qlib Quant Auditor", claim_id=claim_id, state=state,
                        explanation=explanation, evidence_ids=cited)


def _permitted_finding(finding: AuditFinding, challenge: Challenge) -> AuditFinding:
    if not set(finding.evidence_ids) <= set(challenge.evidence_ids):
        return AuditFinding(auditor=finding.auditor, claim_id=finding.claim_id, state="UNSUPPORTED",
            explanation="Complete independent verification requires evidence outside this challenge's permitted set.",
            evidence_ids=tuple(identity for identity in finding.evidence_ids if identity in challenge.evidence_ids))
    return finding


class DeterministicChallengeResponder:
    """Quantitative fact rechecks only; this object never contains peer reports."""

    def __init__(self, qualified_model: QualifiedLinearModel | None, lean: LeanValidationReport) -> None:
        self.model, self.lean = qualified_model, lean

    def __call__(self, snapshot: ResearchSnapshot, own_report: FirmReport | None,
                 challenge: Challenge, round_number: int) -> ChallengeResponse:
        if challenge.respondent == "qlib":
            _validate_request(snapshot, own_report, challenge, round_number, "qlib")
            if not isinstance(own_report, QlibQuantResearchReport):
                raise InvalidUpstreamReport("Qlib correspondence requires its numeric report")
            finding = _permitted_finding(_quant_finding(snapshot, own_report, self.model, challenge.claim_id), challenge)
            position: Literal["MAINTAIN", "INSUFFICIENT_EVIDENCE"] = (
                "MAINTAIN" if finding.state == "VERIFIED" else "INSUFFICIENT_EVIDENCE")
            component: Literal["qlib", "lean"] = "qlib"
        elif challenge.respondent == "lean":
            qualitative_evidence(snapshot)
            if (not 1 <= round_number <= 2 or own_report is not None
                    or self.lean.snapshot_id != snapshot.snapshot_id
                    or content_hash(self.lean) != challenge.original_report_hash):
                raise InvalidUpstreamReport("LEAN challenge differs from its completed artifact")
            # A frozen engine report cannot acquire missing controls through debate.
            finding = AuditFinding(auditor="LEAN Auditor", state="UNSUPPORTED",
                explanation=f"Frozen LEAN state is {self.lean.state}; {self.lean.observations} observations. "
                "Correspondence cannot supply absent historical controls or replace engine validation. "
                "An independently qualified new validation artifact is required.")
            position, component = "INSUFFICIENT_EVIDENCE", "lean"
        else:
            raise InvalidUpstreamReport("deterministic respondent is not Qlib or LEAN")
        return _response(snapshot, challenge, ResponseContent(position=position,
            explanation=finding.explanation, evidence_ids=finding.evidence_ids), ChallengeInvocation(
                component=component, provider="money-deterministic", model="frozen-artifact-recheck",
                prompt_version="not-applicable", upstream_sha=UPSTREAM_SHAS["qlib"] if component == "qlib" else LEAN_SHA,
                calls=0, usage=Usage(input_tokens=0, output_tokens=0), runtime="deterministic"))


class CrewAIChallengeVerifier:
    """Independent source checks plus deterministic vetoes, never agent agreement."""

    def __init__(self, inference: NativeInference, settings: NativeRunSettings | None,
                 qualified_model: QualifiedLinearModel | None, reports: tuple[FirmReport, ...],
                 lean: LeanValidationReport) -> None:
        self.inference = inference
        supplied = settings or NativeRunSettings()
        self.settings = replace(supplied, max_calls=1)
        self.model, self.reports, self.lean = qualified_model, reports, lean

    def _deterministic(self, finding: AuditFinding) -> ChallengeVerification:
        return ChallengeVerification(finding=finding, invocation=ChallengeInvocation(
            component="crewai", provider=self.inference.provider, model=self.inference.model,
            prompt_version="money-deterministic-challenge-verification-v1",
            upstream_sha=CIO_SHA, calls=0, usage=Usage(input_tokens=0, output_tokens=0), runtime="deterministic"))

    def __call__(self, snapshot: ResearchSnapshot, challenge: Challenge,
                 response: ChallengeResponse) -> ChallengeVerification:
        owner = next((report for report in self.reports if report.firm == challenge.respondent), None)
        if owner is None:
            return self._deterministic(AuditFinding(auditor="CIO Contradiction Analyst",
                claim_id=challenge.claim_id, state="UNSUPPORTED",
                explanation="This global or validation challenge has no safely verifiable original firm claim."))
        _validate_request(snapshot, owner, challenge, 1, owner.firm)
        if (response.challenge_id != challenge.challenge_id or response.respondent != challenge.respondent
                or response.snapshot_hash != snapshot.hash or response.original_report_hash != challenge.original_report_hash
                or response.position != "MAINTAIN" or not set(response.evidence_ids) <= set(challenge.evidence_ids)):
            raise InvalidUpstreamReport("independent verifier received mismatched correspondence")
        fixed = [finding for finding in deterministic_audit(snapshot, self.reports, self.lean, self.model)
                 if finding.claim_id == challenge.claim_id]
        adverse = next((finding for finding in fixed if finding.state in {"CONTRADICTED", "UNSUPPORTED"}), None)
        if adverse is not None:
            return self._deterministic(_permitted_finding(adverse, challenge))
        if isinstance(owner, QlibQuantResearchReport):
            return self._deterministic(_permitted_finding(_quant_finding(snapshot, owner, self.model, challenge.claim_id), challenge))
        assert challenge.original_claim is not None
        original = challenge.original_claim
        # Only a narrowly complete numeric assertion is resolved by arithmetic.
        measured_only = bool(re.fullmatch(
            r"relative\s+volume(?:\s+is|\s+of|\s*[:=])?\s*\d+(?:\.\d+)?\.?|"
            r"cash(?:\s+position)?\s+(?:has\s+)?(?:strengthened|increased|improved)\.?",
            original.statement.strip(), re.I))
        if measured_only and fixed and all(finding.state == "VERIFIED" for finding in fixed):
            return self._deterministic(_permitted_finding(fixed[0], challenge))
        return self._native(snapshot, challenge, response)

    def _native(self, snapshot: ResearchSnapshot, challenge: Challenge,
                response: ChallengeResponse) -> ChallengeVerification:
        if self.settings.verify_source_pin:
            require_pinned_source("crewai")
        try:
            auth: Any = import_module("crewai_core.auth.token")

            def no_platform_auth() -> str:
                raise auth.AuthError("Money does not enable CrewAI Platform authentication")

            original_auth = auth.get_auth_token
            auth.get_auth_token = no_platform_auth
            try:
                native = import_module("crewai")
            finally:
                auth.get_auth_token = original_auth
        except ImportError as exc:
            raise UpstreamUnavailable("pinned CrewAI challenge-verification runtime is unavailable") from exc
        session = InferenceSession(self.inference, self.settings)
        role = "Independent Correspondence Evidence Auditor"
        description = (snapshot_payload(snapshot) + "\nUNTRUSTED_CHALLENGE=" + challenge.model_dump_json()
            + "\nUNTRUSTED_RESPONSE=" + response.model_dump_json()
            + "\nVerify the entire ORIGINAL claim from admitted facts, not agreement or the response's confidence. "
            "For VERIFIED provide exact quoted facts from each source supporting the original factual claim. "
            "Cite and quote only the challenge's explicit evidence_ids. Empty permission permits nothing. "
            "Unverified inference, omitted evidence, unsupported arithmetic, and future outcomes stay UNSUPPORTED. "
            "The original claim cannot be replaced by a weaker corrected claim. No tools exist.")
        agent = native.Agent(role=role, goal="Independently test a disputed sealed claim against source facts",
            backstory=POLICY, llm=_crewai_llm(session), tools=[], allow_delegation=False,
            allow_code_execution=False, max_iter=1, max_retry_limit=0, verbose=False,
            memory=False, cache=False, max_execution_time=max(1, int(self.settings.timeout_seconds)))
        task = native.Task(description=description, expected_output="Strict JSON evidence verification",
                           agent=agent, output_pydantic=VerificationContent, guardrail_max_retries=0)
        try:
            result = native.Crew(agents=[agent], tasks=[task], memory=False, cache=False,
                                 verbose=False, share_crew=False, tracing=False).kickoff()
        except Exception as exc:
            if session.failure is not None:
                raise session.failure from exc
            raise
        parsed = getattr(result, "pydantic", None)
        if parsed is None:
            raise InvalidUpstreamReport("CrewAI challenge verifier did not return structured evidence")
        output = VerificationContent.model_validate_json(parsed.model_dump_json())
        admitted = {record.evidence_id: record for record in qualitative_evidence(snapshot)
                    if record.evidence_id in challenge.evidence_ids}
        if not set(output.evidence_ids) <= admitted.keys() or len(set(output.evidence_ids)) != len(output.evidence_ids):
            raise InvalidUpstreamReport("challenge verifier cited non-admitted evidence")
        checked_ids = set()
        checked_quotations = set()
        source_checks = []
        for check in output.source_checks:
            record = admitted.get(check.evidence_id)
            if record is None:
                raise InvalidUpstreamReport("challenge verifier quoted a non-admitted source")
            payload = record.payload
            if isinstance(payload, DocumentFact):
                source = text_only(payload.title) + " " + text_only(payload.excerpt)
            elif isinstance(payload, FinancialFact):
                source = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
            else:
                source = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
            quotation = text_only(check.quoted_fact, 500)
            if len(quotation) < 5 or quotation.casefold() not in source.casefold():
                raise InvalidUpstreamReport("challenge verification source quotation is not in frozen evidence")
            checked_ids.add(check.evidence_id)
            checked_quotations.add(" ".join(quotation.split()).rstrip(".").casefold())
            source_checks.append(ChallengeSourceCheck(evidence_id=check.evidence_id,
                record_hash=record.hash, quoted_fact=quotation))
        state = output.state
        explanation = output.explanation
        original = challenge.original_claim
        if state == "VERIFIED" and (original is None or original.classification != "INFERENCE"
                or " ".join(original.statement.split()).rstrip(".").casefold() not in checked_quotations
                or not output.evidence_ids or not set(output.evidence_ids) <= checked_ids
                or not set(original.evidence_ids) <= checked_ids):
            state = "UNSUPPORTED"
            explanation = "Source checks do not independently support the complete original factual claim; " \
                          "unmeasured inference cannot be certified by model agreement."
        return ChallengeVerification(finding=AuditFinding(auditor=role, claim_id=challenge.claim_id,
            state=state, explanation=text_only(explanation, 4000), evidence_ids=output.evidence_ids),
            invocation=ChallengeInvocation(component="crewai", provider=session.provider, model=session.model,
                prompt_version="money-crewai-challenge-verification-v1", upstream_sha=CIO_SHA,
                calls=session.calls, usage=self.inference.usage(),
                runtime="live" if self.settings.verify_source_pin else "demo"), evidence_checks=tuple(source_checks))
