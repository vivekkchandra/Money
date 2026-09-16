"""Independent discovery channels form a union; none can veto another channel."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from pydantic import Field

from money.scanner.technical import calculate_technical
from money.schemas.contracts import (
    Candidate,
    Contract,
    DiscoveryReason,
    DocumentFact,
    EvidenceRecord,
    FinancialFact,
    QlibQuantResearchReport,
    ResearchSnapshot,
)


class DiscoveryPolicy(Contract):
    version: str = "discovery-v2"
    catalyst_age_days: int = Field(default=7, ge=1, le=30)
    financial_change_fraction: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    minimum_quant_score: float = Field(default=0, ge=0)
    # Attention triggers, not bullish sentiment or validated trading edges.
    catalyst_terms: tuple[str, ...] = (
        "results",
        "trading update",
        "profit warning",
        "acquisition",
        "disposal",
        "dividend",
        "rights issue",
        "refinancing",
        "contract",
        "guidance",
    )


def documentary_discovery(
    snapshot: ResearchSnapshot, policy: DiscoveryPolicy
) -> tuple[DiscoveryReason, ...]:
    reasons: list[DiscoveryReason] = []
    financial: dict[tuple[str, str], list[EvidenceRecord]] = defaultdict(list)
    seen_documents: set[str] = set()
    for record in snapshot.evidence:
        if (
            record.conflicting
            or record.fresh_until <= snapshot.created_at
            or not record.available_at(snapshot.cutoff_for(record))
        ):
            continue
        value = record.payload
        if isinstance(value, DocumentFact) and value.kind in {"announcement", "news"}:
            assert record.publication_time is not None
            if record.publication_time < snapshot.news_cutoff - timedelta(
                days=policy.catalyst_age_days
            ):
                continue
            matched = tuple(
                term for term in policy.catalyst_terms if term.casefold() in value.title.casefold()
            )
            if matched and record.canonical_source_id not in seen_documents:
                seen_documents.add(record.canonical_source_id)
                reasons.append(
                    DiscoveryReason(
                        channel="catalyst",
                        reason=f"{policy.version}: recent title matched {', '.join(matched)}; relevance only, no sentiment conclusion.",
                        evidence_ids=(record.evidence_id,),
                    )
                )
        elif isinstance(value, FinancialFact) and value.metric in {
            "cash",
            "revenue",
            "net_income",
            "debt",
            "operating_cash_flow",
        }:
            financial[(value.metric, value.unit)].append(record)
    for (metric, unit), records in sorted(financial.items()):
        records.sort(
            key=lambda record: (
                record.payload.period_end
                if isinstance(record.payload, FinancialFact)
                else record.observation_time
            )
        )
        if len(records) < 2:
            continue
        prior, current = records[-2:]
        assert isinstance(prior.payload, FinancialFact) and isinstance(
            current.payload, FinancialFact
        )
        if (
            prior.payload.period_end == current.payload.period_end
            or prior.payload.value == 0
            or not prior.payload.value.is_finite()
            or not current.payload.value.is_finite()
        ):
            continue
        change = (current.payload.value - prior.payload.value) / abs(prior.payload.value)
        if abs(change) >= policy.financial_change_fraction:
            reasons.append(
                DiscoveryReason(
                    channel="fundamental",
                    reason=f"{policy.version}: {metric} changed {change:.2%} between cited period-end facts ({unit}); accounting-period comparability requires audit.",
                    evidence_ids=(prior.evidence_id, current.evidence_id),
                )
            )
    return tuple(reasons)


def union_candidates(channels: Iterable[Iterable[Candidate]]) -> tuple[Candidate, ...]:
    found: dict[str, list[DiscoveryReason]] = defaultdict(list)
    for candidates in channels:
        for candidate in candidates:
            for reason in candidate.discovery:
                if reason not in found[candidate.ticker]:
                    found[candidate.ticker].append(reason)
    return tuple(Candidate(ticker=t, discovery=tuple(found[t])) for t in sorted(found))


def discover_snapshot(
    snapshot: ResearchSnapshot,
    *,
    quant: QlibQuantResearchReport | None = None,
    policy: DiscoveryPolicy | None = None,
) -> Candidate:
    """Independent technical and documentary channels; no requirement to agree."""
    policy = policy or DiscoveryPolicy()
    reasons: list[DiscoveryReason] = [
        *calculate_technical(snapshot).discoveries,
        *documentary_discovery(snapshot, policy),
    ]
    if quant is not None:
        if quant.snapshot_id != snapshot.snapshot_id or quant.snapshot_hash != snapshot.hash:
            raise ValueError("QLIB_DISCOVERY_SNAPSHOT_MISMATCH")
        if (
            quant.runtime == "live"
            and quant.prediction_score is not None
            and quant.prediction_score > policy.minimum_quant_score
        ):
            ids = tuple(
                dict.fromkeys(
                    evidence_id for claim in quant.claims for evidence_id in claim.evidence_ids
                )
            )
            if not ids or not set(ids) <= {record.evidence_id for record in snapshot.evidence}:
                raise ValueError("QLIB_DISCOVERY_EVIDENCE_INVALID")
            reasons.append(
                DiscoveryReason(
                    channel="quantitative",
                    reason=f"{policy.version}: qualified Qlib model {quant.model_version} score {quant.prediction_score:.6g} > {policy.minimum_quant_score}; not a probability or cross-sectional rank.",
                    evidence_ids=ids,
                )
            )
    return Candidate(ticker=snapshot.ticker, discovery=tuple(reasons))
