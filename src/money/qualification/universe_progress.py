"""Fair resumable work ordering; cursors never carry qualification decisions."""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

from money.qualification.core import QualificationContext
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION

CURSOR = "state/universe-enrichment-cursor.json"
SCHEDULER_VERSION = "money-bulk-round-robin-v1"


def _key(row: dict[str, Any]) -> str:
    return f"{row['trading212_id']}:{row['isin']}"


def enrichment_order(
    ctx: QualificationContext,
    rows: list[dict[str, Any]],
    binding: str | None,
    *,
    replay: bool,
) -> list[int]:
    """Rotate only the network-work order, not membership or qualification.

    A persistent cursor advances only after actual network work. Thus repeated
    failures near the start cannot consume every run's request budget forever.
    Membership churn uses the next surviving identity, not an old array index.
    Offline replay neither consumes nor changes the live scheduling cursor.
    """
    candidates = sorted(
        (i for i, row in enumerate(rows) if row.get("provider_enrichment_input") is True),
        key=lambda index: _key(rows[index]),
    )
    if replay or not candidates:
        return candidates
    try:
        state = ctx.read_json(CURSOR)
    except (ValueError, OSError):
        # A corrupt optimization cannot confer admission or omit any stock.
        return candidates
    if (
        not isinstance(state, dict)
        or state.get("version") != SCHEDULER_VERSION
        or state.get("universe_policy_version") != UNIVERSE_POLICY_VERSION
        or state.get("credential_binding_sha256") != binding
        or not isinstance(state.get("resume_after"), str)
    ):
        return candidates
    start = bisect_right([_key(rows[i]) for i in candidates], state["resume_after"])
    return candidates[start:] + candidates[:start]


def record_network_progress(
    ctx: QualificationContext, row: dict[str, Any], binding: str | None
) -> None:
    """Persist no provider success or approval, only the last serviced identity."""
    ctx.write_json(
        CURSOR,
        {
            "version": SCHEDULER_VERSION,
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "credential_binding_sha256": binding,
            "resume_after": _key(row),
            "updated_at": ctx.now.isoformat(),
        },
    )
