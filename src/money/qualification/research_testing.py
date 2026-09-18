"""Bounded personal research workflow, separate from commercial qualification."""

from __future__ import annotations

from decimal import Decimal
from functools import partial
from typing import Any

from pydantic import Field

from money.adapters.native import NativeRunSettings
from money.qualification.core import QualificationContext
from money.schemas.contracts import Contract, PriceBar, utc_now
from money.usage_policy import UsageMode, usage_mode


class TestingSettings(Contract):
    shortlist_limit: int = Field(default=1, ge=1, le=20)
    enrichment_request_budget: int = Field(default=20, ge=0, le=300)
    # Whole local firm, including startup and its sequence of model requests.
    # Not an HTTP timeout or an override of hosted manifest execution limits.
    agent_timeout_seconds: int = Field(default=900, ge=1, le=1800)


def _selection(ctx: QualificationContext, rows: list[dict[str, Any]] | None = None) -> str | None:
    isin = ctx.environ.get("MONEY_RESEARCH_TEST_ISIN")
    if isin:
        matches = [row["trading212_id"] for row in rows or [] if row.get("isin") == isin]
        return matches[0] if len(matches) == 1 else "UNRESOLVED_ISIN_SELECTION"
    selected = ctx.environ.get("MONEY_RESEARCH_TEST_INSTRUMENT")
    if selected:
        return selected
    saved = ctx.read_json("inputs/first-qualification-selection.json") or {}
    return saved.get("trading212_id")


def write_research_diagnostics(
    ctx: QualificationContext, master: dict[str, Any], report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Only causal research prerequisites; old commercial blockers stay historical."""
    from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION
    from money.qualification.universe_status import live_metadata_state

    selected = _selection(ctx, master.get("stocks", []))
    row = next((r for r in master.get("stocks", []) if r.get("trading212_id") == selected), None)
    report = report or {"blockers": ctx.blockers, "state": "DISCOVERED"}
    stages = {
        "live_membership": (), "basic_identity": ("live_membership",),
        "snapshot": ("basic_identity",), "native_source_security": (), "local_inference": (),
        "independent_reports": ("snapshot", "native_source_security", "local_inference"),
        "first_pass_locked": ("independent_reports",), "debate": ("first_pass_locked",),
        "market_backtest_inputs": (),
        "lean": ("first_pass_locked", "market_backtest_inputs"),
        "cio_red_team": ("first_pass_locked", "lean"),
    }
    # Historical commercial diagnostics are not current research prerequisites.
    # Keep them as non-research metadata instead of auto-approving or losing them.
    optional_codes = {
        "CH_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED",
        "OFFICIAL_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED",
        "PROVIDER_RIGHTS_AND_DATASET_QUALIFICATION_REQUIRED",
        "PROVIDER_DATASET_QUALIFICATION_REQUIRED", "VERIFIED_ISSUER_JURISDICTION_REQUIRED",
        "AUTHORITATIVE_ISSUER_IDENTITY_AND_JURISDICTION_REQUIRED",
        "ADMISSIBLE_ISSUER_BUSINESS_EVIDENCE_REQUIRED", "ISSUER_ETHICAL_SCREENING_REQUIRED",
        "QUALIFIED_INSTRUMENT_EVIDENCE_REQUIRED", "BULK_UNIVERSE_NO_QUALIFIED_MEMBERS",
    }
    legacy = [item for item in report.get("blockers", []) if (
        item["code"] in optional_codes
        or item["code"].startswith(("PROVIDER_RIGHTS_REVIEW_REQUIRED", "ETHICAL_SOURCE_RIGHTS_REVIEW_REQUIRED"))
    )]
    blockers = [item for item in report.get("blockers", []) if item not in legacy]
    metadata = live_metadata_state(ctx, master)
    if not metadata.get("current") and not any(b["code"] == "TRADING212_LIVE_METADATA_REQUIRED" for b in blockers):
        blockers.append({
            "code": "TRADING212_LIVE_METADATA_REQUIRED",
            "action": "Authenticate a current live Trading 212 refresh. Saved-response counts are diagnostic only and cannot admit research.",
        })
    dag = {
        "purpose": "RESEARCH_TESTING", "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "qualification_counts_as_recorded": master.get("summary", {}),
        "stages": [{"id": key, "requires": value} for key, value in stages.items()],
        "nodes": [
            {"id": key, "depends_on": list(value), "scope": "RESEARCH_TESTING"}
            for key, value in stages.items()
        ] + [
            {"id": "provider_rights", "depends_on": [], "scope": "COMMERCIAL_RELEASE_ONLY",
             "blocking_for_personal_research": False, "commercial_public_release_blocked": True},
            {"id": "release", "depends_on": ["provider_rights", "cio_red_team"],
             "scope": "COMMERCIAL_RELEASE_ONLY", "blocking_for_personal_research": False},
        ],
        "legacy_non_research_requirements": legacy,
        "blockers": blockers,
        "broker_metadata": metadata,
        "optional_enrichment_not_prerequisites": [
            "companies_house", "issuer_jurisdiction", "fundamentals", "financial_filings",
            "ethical_screening", "provider_rights_manual_review", "supplemental_review",
        ],
        "production_qualified": False, "qlib_enabled": False, "lean_mandatory": True,
    }
    ctx.write_json("outputs/qualification-blocker-dag.json", dag)
    candidate = {
        "purpose": "RESEARCH_TESTING", "selection_basis": "Explicit operator pipeline test; no expected-return ranking",
        "trading212_id": selected, "isin": row.get("isin") if row else None,
        "provider_symbol": row.get("eodhd_symbol") if row else None,
        "research_state": row.get("research_state", "DISCOVERED") if row else "DISCOVERED",
        "ethical_status": row.get("ethical_status", "NOT_SCREENED") if row else "NOT_SCREENED",
        "enrichment": row.get("enrichment_status", {}) if row else {},
        "production_qualified": False, "blockers": blockers,
        "next_genuine_blocker": blockers[0] if blockers else None,
    }
    ctx.write_json("outputs/first-qualification-candidate.json", candidate)
    steps = [
        "# Personal research next actions", "",
        "Research eligibility is not ethical, licensing, buyability or production approval.", "",
        "1. Refresh authenticated Trading 212 STOCK membership in GBP/GBX; validate basic identity.",
        "2. Freeze the complete admitted universe, then select a bounded shortlist (default one).",
        "3. Run pinned TradingAgents and AI Hedge Fund independently using the same snapshot and explicit data gaps.",
        "4. Preserve both reports and lock first pass; compare cited agreements/disagreements.",
        "5. Supply real market history, mapping, required actions/PIT, assumed costs and OOS/walk-forward; run genuine LEAN.",
        "6. CIO/Red Team readiness follows locked reports and LEAN; no manifest/release is granted here.", "",
        "Companies House, fundamentals, ethics and manual company/provider reviews do not block steps 1–5.",
        "Market data is mandatory for calculations that use it; missing bars are never fabricated.", "",
        "## Current blockers", "",
        *(f"- {b['code']}: {b['action']}" for b in blockers),
    ]
    if not blockers:
        steps.append("No stage has been certified by this diagnostic alone. Run the research workflow.")
    for path in ("outputs/NEXT_ACTIONS.md", "outputs/FIRST_STOCK_NEXT.md"):
        ctx.write_bytes(path, ("\n".join(steps) + "\n").encode())
    ctx.write_bytes("outputs/REVIEW_TASKS.md", (
        b"# Research/testing reviews\n\n"
        b"No company, ISA, ethical or manual provider-rights review is required for research admission. "
        b"Existing reviews remain historical evidence, not automatic approvals.\n\n"
        b"Configure documented cost/slippage scenarios for backtests; do not label assumptions as broker observations. "
        b"Real market data, mapping, strategy-required actions/PIT, native security and LEAN execution remain necessary. "
        b"Commercial/public release retains its independent strict reviews. See NEXT_ACTIONS.md for current blockers.\n"
    ))
    return dag


def screen_research_rows(
    ctx: QualificationContext, rows: list[dict[str, Any]], settings: TestingSettings,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Cache-only numeric triage. Missing data never revokes basic admission."""
    from money.research.market_data import load_research_market_data

    markets: dict[str, Any] = {}
    scores: list[dict[str, Any]] = []
    for row in rows:
        result = load_research_market_data(ctx, row)
        dataset = result.dataset
        turnover = None
        if dataset is not None:
            markets[row["trading212_id"]] = dataset
            # Values are computed by the dataset's currency-aware validated bars.
            records = dataset.evidence_records("screen-only")[-20:]
            values = sorted(
                record.payload.close * record.payload.volume
                / (Decimal(100) if record.payload.currency == "GBX" else Decimal(1))
                for record in records if isinstance(record.payload, PriceBar)
            )
            if values:
                turnover = values[len(values) // 2]
        scores.append({
            "trading212_id": row["trading212_id"],
            "market_data_available": dataset is not None,
            "median_recent_turnover_gbp": str(turnover) if turnover is not None else None,
        })
    scores.sort(key=lambda item: (
        item["median_recent_turnover_gbp"] is None,
        -Decimal(item["median_recent_turnover_gbp"] or "0"), item["trading212_id"],
    ))
    requested = _selection(ctx, rows)
    if requested:
        if requested not in {row["trading212_id"] for row in rows}:
            ctx.block("REQUESTED_INSTRUMENT_NOT_RESEARCH_ELIGIBLE", "The selected broker instrument must pass current live membership and basic identity; no stale or conflicting stock is substituted.")
            selected: list[dict[str, Any]] = []
        else:
            selected = [row for row in rows if row["trading212_id"] == requested]
    else:
        ids = {item["trading212_id"] for item in scores[:settings.shortlist_limit]}
        selected = [row for row in rows if row["trading212_id"] in ids]
    ctx.write_json("outputs/research-screen.json", {
        "methodology": "money-research-liquidity-triage-v1",
        "features": "Median of up to 20 latest genuine close*volume observations in GBP. Unscored identities remain eligible; stable broker-ID tie break.",
        "purpose": "Cost-bounded work prioritisation, not a return prediction or recommendation",
        "explicit_pipeline_test": requested, "universe_count": len(rows),
        "shortlist_limit": settings.shortlist_limit,
        "shortlist": [row["trading212_id"] for row in selected], "scores": scores,
    })
    return selected, markets


def run_research_testing(ctx: QualificationContext) -> dict[str, Any]:
    from money.qualification.runner import _execute
    from money.qualification.universe import finalize_universe
    from money.research.independent import run_local_independent_research
    from money.research.inference_config import load_inference_selections
    from money.research.testing_snapshot import build_testing_snapshot, freeze_research_universe

    if usage_mode(ctx.environ) != UsageMode.PERSONAL_RESEARCH:
        raise ValueError("RESEARCH_TESTING_REQUIRES_EXPLICIT_PERSONAL_USE")
    if ctx.environ.get("MONEY_QLIB_ENABLED", "").lower() != "false":
        ctx.block("RESEARCH_TESTING_QLIB_DISABLED_REQUIRED", "Set MONEY_QLIB_ENABLED=false explicitly; the two-firm research path never silently falls back from Qlib.")
    settings = TestingSettings(
        shortlist_limit=int(ctx.environ.get("MONEY_RESEARCH_SHORTLIST_LIMIT", "1")),
        enrichment_request_budget=int(ctx.environ.get("MONEY_RESEARCH_ENRICHMENT_REQUESTS", "20")),
        agent_timeout_seconds=int(ctx.environ.get("MONEY_RESEARCH_AGENT_TIMEOUT_SECONDS", "900")),
    )
    native_settings = NativeRunSettings(timeout_seconds=settings.agent_timeout_seconds)
    ctx.write_json("outputs/research-mode.json", {
        "purpose": "RESEARCH_TESTING", "usage_mode": "PERSONAL_RESEARCH",
        "qlib_enabled": False, "lean_mandatory": True, "production_qualified": False,
        "commercial_release_permitted": False, "raw_data_redistribution_permitted": False,
        "agent_timeout_seconds": native_settings.timeout_seconds,
        "native_max_calls": native_settings.max_calls,
    })
    master = _execute(ctx, "research-universe", lambda: finalize_universe(ctx, max_requests=0, capture_documents=False))
    rows: list[dict[str, Any]] = []
    frozen_ref = None
    try:
        rows, frozen_ref = freeze_research_universe(ctx, master)
        if not rows:
            ctx.block("NO_RESEARCH_ELIGIBLE_MEMBERS", "Current authenticated broker metadata contains no non-conflicting STOCKs in GBP/GBX. Review basic identity conflicts; company enrichment is not the cause.")
    except (ValueError, KeyError, TypeError):
        if not any(item["code"] == "TRADING212_LIVE_METADATA_REQUIRED" for item in ctx.blockers):
            ctx.block("TRADING212_LIVE_METADATA_REQUIRED", "Run this command with authenticated Trading 212 metadata access. Saved replay is audit evidence, never current research admission.")
    state = "RESEARCH_ELIGIBLE" if rows else "DISCOVERED"
    results = []
    if rows and frozen_ref is not None and not ctx.blockers:
        shortlist, markets = screen_research_rows(ctx, rows, settings)
        for row in shortlist:
            # Enrich ONLY selected candidates; company failures remain optional.
            from money.qualification.universe_providers import BulkProviderEnricher
            from money.research.market_data import load_research_market_data

            try:
                enriched = BulkProviderEnricher(ctx, max_requests=settings.enrichment_request_budget).enrich(row)
            except Exception:
                # An optional acquisition failure must not revoke basic broker
                # admission. Never retain exception text or partially changed data.
                enriched = {**row, "research_enrichment_warning": "OPTIONAL_ENRICHMENT_UNAVAILABLE"}
            acquisition = load_research_market_data(ctx, enriched, allow_network=True)
            market = acquisition.dataset or markets.get(row["trading212_id"])
            ctx.write_json("outputs/research-market-attempts.json", {
                "trading212_id": row["trading212_id"],
                "attempts": [attempt.model_dump(mode="json") for attempt in acquisition.attempts],
            })
            ctx.now = utc_now()
            snapshot = build_testing_snapshot(
                ctx, enriched, frozen_ref,
                market.evidence_records("pending-snapshot") if market else (),
            )
            reference = ctx.artifact(snapshot.model_dump(mode="json"))
            ctx.write_json("outputs/research-snapshot.json", {"artifact": reference, "snapshot_hash": snapshot.hash})
            selections = load_inference_selections(ctx.repo, ctx.environ)
            firms = _execute(ctx, "research-first-pass", partial(
                run_local_independent_research, ctx, snapshot, selections, native_settings,
            ))
            result = {"trading212_id": row["trading212_id"], "state": "RESEARCH_ELIGIBLE", "snapshot_artifact": reference, "first_pass": firms, "lean": {"executed": False}}
            if firms.get("complete"):
                result["state"] = "RESEARCHED"
                # Actual backtest readiness/execution is handled separately;
                # neither report agreement nor eligibility is LEAN validation.
                result["lean"] = _execute(ctx, "research-lean", partial(_run_lean, ctx, snapshot, firms, market))
                if result["lean"].get("readiness", {}).get("state") == "BACKTEST_READY":
                    result["state"] = "BACKTEST_READY"
                if result["lean"].get("state") == "LEAN_VALIDATED":
                    result["state"] = "LEAN_VALIDATED"
            results.append(result)
        if results:
            order = ("RESEARCH_ELIGIBLE", "RESEARCHED", "BACKTEST_READY", "LEAN_VALIDATED")
            state = min((result["state"] for result in results), key=order.index)
    report = {
        "status": "RESEARCH RUN COMPLETE" if state == "LEAN_VALIDATED" else "RESEARCH RUN BLOCKED",
        "purpose": "RESEARCH_TESTING", "state": state, "blockers": ctx.blockers,
        "research_eligible": len(rows), "summary": master.get("summary", {}),
        "results": results, "production_ready": False, "manifest_sha256": None,
        "qlib_enabled": False, "lean_mandatory": True, "updated_at": utc_now().isoformat(),
    }
    ctx.write_json("outputs/research-testing-result.json", report)
    ctx.write_json("status.json", report)
    write_research_diagnostics(ctx, master, report)
    return report


def _run_lean(ctx: QualificationContext, snapshot: Any, firms: dict[str, Any], market: Any) -> dict[str, Any]:
    """Configured research backtest inputs only; no missing assumptions invented."""
    # The input template and genuine runtime are provided by the backtesting
    # boundary. An absent configuration cannot be promoted to a passed study.
    from money.research.backtesting import run_configured_research_lean

    return run_configured_research_lean(ctx, snapshot, firms, market)
