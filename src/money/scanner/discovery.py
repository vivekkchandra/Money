"""Independent discovery channels form a union; none can veto another channel."""

from collections import defaultdict
from collections.abc import Iterable

from money.schemas.contracts import Candidate, DiscoveryReason, ResearchSnapshot


def union_candidates(channels: Iterable[Iterable[Candidate]]) -> tuple[Candidate, ...]:
    found: dict[str, list[DiscoveryReason]] = defaultdict(list)
    for candidates in channels:
        for candidate in candidates:
            for reason in candidate.discovery:
                if reason not in found[candidate.ticker]:
                    found[candidate.ticker].append(reason)
    return tuple(Candidate(ticker=t, discovery=tuple(found[t])) for t in sorted(found))


def discover_snapshot(snapshot: ResearchSnapshot) -> Candidate:
    """Conservative objective presence channels for the foundation, not a ranking model."""
    reasons: list[DiscoveryReason] = []
    for kind, channel in (
        ("news", "catalyst"),
        ("announcement", "catalyst"),
        ("financial", "fundamental"),
    ):
        ids = tuple(e.evidence_id for e in snapshot.evidence if e.payload.kind == kind)
        if ids:
            reasons.append(
                DiscoveryReason.model_validate(
                    {
                        "channel": channel,
                        "reason": f"Available {kind} evidence merits review",
                        "evidence_ids": ids,
                    }
                )
            )
    return Candidate(ticker=snapshot.ticker, discovery=tuple(reasons))
