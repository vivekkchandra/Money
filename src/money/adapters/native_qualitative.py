"""Executable native firm assemblies with a closed Money snapshot capability.

TradingAgents' analyst/research/risk nodes are retained; its transaction-planning
nodes and data tools are not assembled. AI-HF's native persona/predict lifecycle
uses an overridden Money snapshot and strict cited output instead of US TTM data
or an uncalibrated numeric confidence score.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import import_module
from typing import Any

from money.adapters.native import (
    POLICY,
    AnalysisOutput,
    InferenceSession,
    NativeInference,
    NativeRunSettings,
    parse_analysis,
    snapshot_payload,
)
from money.adapters.native_attestation import require_pinned_source
from money.adapters.upstream import UPSTREAM_SHAS, InvalidUpstreamReport, UpstreamUnavailable
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Claim,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
)


def _mandate_context(mandate: ResearchMandate) -> str:
    # Aspirational profit is deliberately absent from every inference prompt.
    return json.dumps({
        "maximum_assumed_capital_gbp": str(mandate.maximum_capital_gbp),
        "minimum_horizon_days": mandate.minimum_horizon_days,
        "maximum_horizon_days": mandate.maximum_horizon_days,
        "purpose": "research only; no actual capital, positions or broker capabilities",
    })


def _native_chat(session: InferenceSession, context: str) -> Any:
    try:
        runnables = import_module("langchain_core.runnables")
        messages = import_module("langchain_core.messages")
    except ImportError as exc:
        raise UpstreamUnavailable("TradingAgents' pinned native runtime is not installed") from exc

    class SnapshotChat(runnables.Runnable):  # type: ignore[name-defined]
        def bind_tools(self, tools: object, **kwargs: object) -> Any:
            # Never forward native data tools or their implementations to a model.
            return self

        def with_structured_output(self, schema: object, **kwargs: object) -> object:
            # Native schemas contain transaction ratings. Native supported free-text
            # fallback is used; only the final Money report is admitted as structured.
            raise NotImplementedError("native transaction schemas are outside Money research")

        def invoke(self, value: Any, config: object = None, **kwargs: object) -> Any:
            if hasattr(value, "to_messages"):
                value = value.to_messages()
            if isinstance(value, (tuple, list)):
                conversation = [
                    {"role": getattr(item, "type", "research"),
                     "content": getattr(item, "content", str(item))}
                    for item in value
                ]
            else:
                conversation = [{"role": "research", "content": str(value)}]
            response = session.complete(
                POLICY + "\nApply the native analytical role to frozen facts. Native tool "
                "requests are superseded by the complete Money snapshot already supplied. "
                "Use cited prose. No tools exist in this capability.",
                context + "\nNATIVE_RESEARCH_CONTEXT=" + json.dumps(conversation),
            )
            # No parsing into tool calls: model text can never become tool execution.
            return messages.AIMessage(content=response, tool_calls=[])

    return SnapshotChat()


class TradingAgentsNativeRunner:
    def __init__(
        self, inference: NativeInference, settings: NativeRunSettings | None = None
    ) -> None:
        self.inference, self.settings = inference, settings or NativeRunSettings()

    def __call__(
        self, mandate: ResearchMandate, snapshot: ResearchSnapshot
    ) -> TradingAgentsResearchReport:
        if self.settings.verify_source_pin:
            require_pinned_source("tradingagents")
        context = _mandate_context(mandate) + "\n" + snapshot_payload(snapshot)
        session = InferenceSession(self.inference, self.settings)
        chat = _native_chat(session, context)
        try:
            native = import_module("tradingagents.agents")
            propagation = import_module("tradingagents.graph.propagation")
        except ImportError as exc:
            raise UpstreamUnavailable("the pinned TradingAgents runtime is not installed") from exc
        state = propagation.Propagator().create_initial_state(
            snapshot.ticker, snapshot.price_cutoff.date().isoformat(),
            past_context="", instrument_context=f"Money frozen snapshot {snapshot.snapshot_id}",
        )
        notes: dict[str, str] = {}
        for name, key in (
            ("create_market_analyst", "market_report"),
            ("create_fundamentals_analyst", "fundamentals_report"),
            ("create_news_analyst", "news_report"),
            ("create_sentiment_analyst", "sentiment_report"),
        ):
            state.update(getattr(native, name)(chat)(state))
            notes[key] = state[key]
            state["messages"] = []
        # Blind firm-internal debate only; no report store or shared memory exists.
        for name in ("create_bull_researcher", "create_bear_researcher",
                     "create_research_manager"):
            state.update(getattr(native, name)(chat)(state))
        # Native risk nodes read a legacy-named field. Supply research context only;
        # never instantiate native transaction-planning or portfolio-manager nodes.
        state["trader_investment_plan"] = "Research hypothesis only: " + state["investment_plan"]
        for name in ("create_aggressive_debator", "create_conservative_debator",
                     "create_neutral_debator"):
            state.update(getattr(native, name)(chat)(state))
        notes["research_debate"] = state["investment_debate_state"]["history"]
        notes["risk_debate"] = state["risk_debate_state"]["history"]
        output = parse_analysis(session.complete(
            POLICY + "\nProduce the firm's cited research report. Preserve unsupported claims "
            "as limitations, never fabricate support. JSON schema: "
            + json.dumps(AnalysisOutput.model_json_schema()),
            context + "\nUNTRUSTED_NATIVE_DISCUSSION=" + json.dumps(notes),
        ), snapshot)
        return TradingAgentsResearchReport(
            snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash,
            conclusion=output.conclusion, claims=output.claims,
            model_version=session.model, prompt_version="money-tradingagents-snapshot-v1",
            upstream_sha=UPSTREAM_SHAS["tradingagents"], model_family=session.model,
            llm_provider_family=session.provider,
            feature_families=tuple(sorted({c.family for c in output.claims})),
            argument_families=("native-specialist-debate",),
            created_at=datetime.now(UTC), usage=self.inference.usage(),
            runtime="live" if self.settings.verify_source_pin else "demo",
        )


class _EphemeralPromptCache:
    """Firm-private cache; native prompts never reach a shared disk/memory store."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.values.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.values[key] = value


class _SnapshotView:
    def __init__(self, snapshot: ResearchSnapshot, content: str) -> None:
        self.ticker = snapshot.ticker
        self.as_of = snapshot.fundamental_cutoff.date().isoformat()
        self.content_hash = snapshot.hash
        self._content = content

    def render(self) -> str:
        return self._content


class AIHedgeFundNativeRunner:
    PERSONAS = (("buffett", "BuffettAgent"), ("lynch", "LynchAgent"))

    def __init__(
        self, inference: NativeInference, settings: NativeRunSettings | None = None
    ) -> None:
        self.inference, self.settings = inference, settings or NativeRunSettings()

    def __call__(
        self, mandate: ResearchMandate, snapshot: ResearchSnapshot
    ) -> AIHedgeFundResearchReport:
        if self.settings.verify_source_pin:
            require_pinned_source("hedge_fund")
        content = _mandate_context(mandate) + "\n" + snapshot_payload(snapshot)
        view = _SnapshotView(snapshot, content)
        session = InferenceSession(self.inference, self.settings)
        results: list[tuple[str, AnalysisOutput]] = []
        for module_name, class_name in self.PERSONAS:
            try:
                persona = getattr(import_module(f"hedge_fund.signals.{module_name}"), class_name)
            except ImportError as exc:
                raise UpstreamUnavailable("the pinned hedge_fund runtime is not installed") from exc
            native_prompt = persona.get_system_prompt

            class MoneyPersona(persona):  # type: ignore[misc, valid-type]
                _native_prompt = native_prompt

                def get_system_prompt(self) -> str:
                    # Preserve the native investment philosophy, replacing its
                    # response rules (ratings/confidence) with Money's cited schema.
                    philosophy = self._native_prompt().split("Signal rules:", 1)[0]
                    return POLICY + "\nResearch philosophy:\n" + philosophy + (
                        "\nIgnore any native confidence/rating conventions. "
                        "Return JSON matching: " + json.dumps(AnalysisOutput.model_json_schema())
                    )

                def build_snapshot(self, ticker: str, date: str, data_client: object) -> _SnapshotView:
                    if ticker != view.ticker or date != view.as_of:
                        raise InvalidUpstreamReport("native persona requested another snapshot")
                    return view

                def _parse(self, response: str) -> dict[str, Any]:
                    return parse_analysis(response, snapshot).model_dump(mode="json")

                def _to_signal(self, ticker: str, date: str, parsed: dict[str, Any],
                               key: str, snapshot: object, cached: bool) -> AnalysisOutput:
                    return AnalysisOutput.model_validate(parsed)

                def _abstain(self, ticker: str, date: str, reason: str) -> None:
                    if session.failure is not None:
                        raise session.failure
                    raise UpstreamUnavailable("AI-HF persona failed or abstained; report not admitted")

            agent = MoneyPersona(llm=session, cache=_EphemeralPromptCache())
            # The overridden snapshot builder consumes no native data client and
            # receives an inert object: no network, report store or peer capability.
            result = agent.predict(view.ticker, view.as_of, object())
            results.append((module_name, result))
        claims = tuple(
            Claim.model_validate({**claim.model_dump(), "claim_id": f"aihf:{name}:{claim.claim_id}"})
            for name, result in results for claim in result.claims
        )
        conclusion = "\n\n".join(f"{name}: {result.conclusion}" for name, result in results)
        if len(conclusion) > 8000:
            raise InvalidUpstreamReport("combined AI-HF report exceeds the report size limit")
        return AIHedgeFundResearchReport(
            snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash,
            conclusion=conclusion, claims=claims, model_version=session.model,
            prompt_version="money-aihf-native-personas-v1", upstream_sha=UPSTREAM_SHAS["ai_hedge_fund"],
            model_family=session.model, llm_provider_family=session.provider,
            feature_families=tuple(sorted({c.family for c in claims})),
            argument_families=("value", "growth"), created_at=datetime.now(UTC),
            usage=self.inference.usage(),
            runtime="live" if self.settings.verify_source_pin else "demo",
        )
