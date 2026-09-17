"""Durable downstream correspondence; no repository is passed to a native firm."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import TypeAdapter

from money.adapters.native import NativeRunSettings
from money.adapters.native_process import BoundedNativeRunner, NativeProcessPolicy
from money.adapters.native_qlib import QualifiedLinearModel
from money.crews.cross_examination import (
    Challenge,
    ChallengeResponse,
    ChallengeVerification,
    CrossExaminationPacket,
    ResponseCapability,
    run_cross_examination,
)
from money.product.metering import CallMeter
from money.research.budgets import TokenBudgetManager
from money.schemas.contracts import (
    CIOAuditReport,
    Contract,
    FirmReport,
    LeanValidationReport,
    ResearchMandate,
    ResearchSnapshot,
    Usage,
    content_hash,
)
from money.storage import ResearchStore

if TYPE_CHECKING:
    from money.research.live import InferenceSelection, LiveManifest


class CorrespondenceCheckpoint(Contract):
    input_hash: str
    reservation_id: str
    result: dict[str, Any]
    usage: Usage


class LiveCorrespondence:
    """Own persistence/budgets outside restricted native response capabilities."""

    def __init__(self, store: ResearchStore, manifest: LiveManifest,
                 model: QualifiedLinearModel | None) -> None:
        self.store, self.manifest, self.model = store, manifest, model
        self.budget = TokenBudgetManager(store, manifest.budgets)

    def invoke[T: Contract](
        self, job_id: str, component: str, selection: InferenceSelection,
        maximum_calls: int, inputs: tuple[Any, ...], schema: type[T],
        operation: Callable[[], T],
    ) -> T:
        """Resume a sealed call, or reserve before calling and seal before settling."""
        checkpoint = self.store.get_checkpoint(job_id)
        artifacts = checkpoint["artifacts"]
        if (checkpoint["stage"] != "CROSS_EXAMINATION"
                or not artifacts.get("audit", {}).get("completed") or "lean" not in artifacts):
            raise ValueError("CORRESPONDENCE_BARRIER_INCOMPLETE")
        # This also checks the durable lock. Its contents stay in this orchestrator.
        self.store.get_reports(job_id)
        identity = content_hash(TypeAdapter(dict[str, Any]).dump_python({
            "component": component, "inputs": inputs,
            "selection": selection, "schema": schema.__name__,
            "prompt_version": "money-native-correspondence-v1",
        }, mode="json"))
        kind = f"cross_call_{identity[:24]}"
        if kind in artifacts:
            saved = CorrespondenceCheckpoint.model_validate(artifacts[kind])
            if saved.input_hash != identity:
                raise ValueError("CORRESPONDENCE_CHECKPOINT_MISMATCH")
            result = schema.model_validate(saved.result)
            self.budget.settle(saved.reservation_id, saved.usage)
            return result
        job = self.store.get_job(job_id)
        if job is None:
            raise ValueError("RESOURCE_NOT_FOUND")
        reservation = f"{job_id}:{job['attempt_count']}:{identity[:24]}"
        self.budget.reserve(
            reservation_id=reservation, job_id=job_id, stage="CROSS_EXAMINATION",
            agent=component, provider=selection.provider, model=selection.model,
            maximum_tokens=maximum_calls * (
                selection.maximum_prompt_bytes + selection.max_output_tokens + 1024
            ), prompt_version="money-native-correspondence-v1",
        )
        result = schema.model_validate_json(
            CallMeter(self.store).invoke_reserved(reservation, operation).model_dump_json()
        )
        invocation = getattr(result, "invocation", None)
        if (invocation is None or invocation.runtime == "demo"
                or invocation.component != component or invocation.provider != selection.provider
                or invocation.model != selection.model or invocation.calls > maximum_calls
                or (invocation.runtime == "deterministic" and (
                    component != "crewai" or invocation.calls != 0
                    or invocation.usage.input_tokens != 0 or invocation.usage.output_tokens != 0
                ))):
            raise ValueError("CORRESPONDENCE_INVOCATION_MISMATCH")
        saved = CorrespondenceCheckpoint(
            input_hash=identity, reservation_id=reservation,
            result=result.model_dump(mode="json"), usage=invocation.usage,
        )
        self.store.save_artifact(job_id, kind, saved)
        self.budget.settle(reservation, saved.usage)
        return result

    def examine(
        self, job_id: str, mandate: ResearchMandate, snapshot: ResearchSnapshot,
        reports: tuple[FirmReport, ...], lean: LeanValidationReport, audit: CIOAuditReport,
    ) -> CrossExaminationPacket:
        from money.crews.native_cross_examination import (
            CrewAIChallengeVerifier,
            DeterministicChallengeResponder,
            NativeFirmChallengeRunner,
        )

        if not self.manifest.enable_native_cross_examination:
            raise ValueError("NATIVE_CORRESPONDENCE_DISABLED")
        if snapshot.qlib_enabled != self.manifest.qlib_enabled:
            raise ValueError("RESEARCH_QLIB_POLICY_CHANGED")
        checkpoint = self.store.get_checkpoint(job_id)
        saved = checkpoint["artifacts"]
        if (checkpoint["stage"] != "CROSS_EXAMINATION"
                or content_hash(saved.get("lean")) != content_hash(lean)
                or content_hash(saved.get("audit")) != content_hash(audit)
                or content_hash(checkpoint["snapshot"]) != content_hash(snapshot)
                or {firm: content_hash(report) for firm, report in self.store.get_reports(job_id).items()}
                != {report.firm: content_hash(report) for report in reports}):
            raise ValueError("CORRESPONDENCE_INPUTS_NOT_SEALED")
        settings = NativeRunSettings(timeout_seconds=self.manifest.native_timeout_seconds, max_calls=2)

        def bounded(runner: Any, schema: Any, selection: InferenceSelection) -> BoundedNativeRunner:
            inference = selection.inference()
            return BoundedNativeRunner(runner, schema, NativeProcessPolicy(
                timeout_seconds=self.manifest.native_timeout_seconds,
                gateway_hosts=inference.allowed_network_hosts,
                gateway_port=inference.allowed_network_port,
            ))

        def response_for(firm: str, selection: InferenceSelection) -> ResponseCapability:
            runner = bounded(NativeFirmChallengeRunner(
                cast(Literal["tradingagents", "ai_hedge_fund"], firm),
                selection.inference(), settings,
            ), ChallengeResponse, selection)

            def respond(snapshot: ResearchSnapshot, own_report: FirmReport | None,
                        challenge: Challenge, round_number: int) -> ChallengeResponse:
                return self.invoke(
                    job_id, firm, selection, 2 if firm == "tradingagents" else 1,
                    (snapshot, own_report, challenge, round_number), ChallengeResponse,
                    lambda: cast(ChallengeResponse, runner(snapshot, own_report, challenge, round_number)),
                )
            return respond

        deterministic = DeterministicChallengeResponder(self.model, lean)
        responders: dict[str, ResponseCapability] = {
            "tradingagents": response_for("tradingagents", self.manifest.tradingagents),
            "ai_hedge_fund": response_for("ai_hedge_fund", self.manifest.ai_hedge_fund),
            "lean": deterministic,
        }
        if snapshot.qlib_enabled:
            responders["qlib"] = deterministic
        selection = self.manifest.crewai
        runner = bounded(CrewAIChallengeVerifier(
            selection.inference(), settings, self.model, reports, lean,
        ), ChallengeVerification, selection)

        def verify(snapshot: ResearchSnapshot, challenge: Challenge,
                   response: ChallengeResponse) -> ChallengeVerification:
            return self.invoke(
                job_id, "crewai", selection, 1, (snapshot, reports, lean, challenge, response),
                ChallengeVerification,
                lambda: cast(ChallengeVerification, runner(snapshot, challenge, response)),
            )

        return run_cross_examination(
            snapshot, reports, lean, audit, first_pass_locked=True,
            responders=responders, verifier=verify, maximum_rounds=2,
            maximum_challenges=self.manifest.cross_examination_maximum_challenges,
        )
