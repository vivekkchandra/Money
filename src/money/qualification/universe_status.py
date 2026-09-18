"""Evidence-linked diagnostics; never an alternative eligibility authority."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from money.adapters.eligibility import ELIGIBILITY_MAXIMUM_AGE
from money.qualification.core import QualificationContext, fingerprint
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION


def live_metadata_state(ctx: QualificationContext, source: dict[str, Any]) -> dict[str, Any]:
    """Distinguish current retrieval from eligibility, verifying actual raw bytes.

    This verifies a recorded retrieval, not this process's credentials or the
    account's type. A standalone offline diagnosis cannot certify a new refresh.
    """
    from money.qualification.universe import _restore_response, credential_binding

    result: dict[str, Any] = {"current": False, "reason": "LIVE_METADATA_NOT_VERIFIED"}
    try:
        reference = source["universe_provenance"]
        provenance = json.loads(ctx.verify_artifact(*reference))
        if fingerprint(source.get("provenance", provenance)) != fingerprint(provenance):
            raise ValueError("PROVENANCE_MISMATCH")
        observed = datetime.fromisoformat(provenance["retrieved_at"])
        binding = provenance["credential_binding_sha256"]
        if (
            source.get("status") != "REFRESHED"
            or source.get("scope") != "LIVE_RETRIEVAL"
            or source.get("universe_policy_version") != UNIVERSE_POLICY_VERSION
            or provenance.get("universe_policy_version") != UNIVERSE_POLICY_VERSION
            or provenance.get("scope") != "LIVE_RETRIEVAL"
            or provenance.get("retrieval_environment") != "live"
            or provenance.get("credential_binding_verified_this_run") is not True
            or not isinstance(binding, str)
            or not re.fullmatch(r"[a-f0-9]{64}", binding)
            or observed.tzinfo is None
        ):
            raise ValueError("LIVE_RETRIEVAL_PROVENANCE_REQUIRED")
        current_binding = credential_binding(ctx.environ)
        if current_binding is not None and current_binding != binding:
            raise ValueError("CURRENT_CREDENTIAL_BINDING_MISMATCH")
        raw, instruments = _restore_response(ctx, provenance["response_artifacts"]["instruments"])
        if hashlib.sha256(raw).hexdigest() != provenance["instrument_response_hash"]:
            raise ValueError("RAW_RESPONSE_MISMATCH")
        gbx = sum(
            isinstance(item, dict)
            and item.get("type") == "STOCK"
            and item.get("currencyCode") == "GBX"
            for item in instruments
        )
        if (
            provenance.get("raw_instruments") != len(instruments)
            or provenance.get("gbx_stocks") != gbx
        ):
            raise ValueError("RAW_RESPONSE_COUNTS_MISMATCH")
        current = observed <= ctx.now < observed + ELIGIBILITY_MAXIMUM_AGE
        result.update(
            current=current,
            reason="CURRENT_LIVE_RETRIEVAL" if current else "LIVE_METADATA_EXPIRED_OR_FUTURE",
            observed_at=observed.isoformat(),
            valid_until=(observed + ELIGIBILITY_MAXIMUM_AGE).isoformat(),
            raw_instruments=len(instruments),
            gbx_stocks=gbx,
            source_provenance_sha256=reference[0],
            instrument_response_sha256=provenance["instrument_response_hash"],
            credential_binding_sha256=binding,
            current_process_binding_verified=current_binding == binding,
            account_type_verified=False,
        )
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        pass
    return result


def no_qualified_snapshot_action(current_metadata: bool) -> str:
    if current_metadata:
        return (
            "Live Trading 212 metadata is current, but zero current eligibility-qualified members "
            "are available. Resolve the global account/buy-scope and provider-rights reviews, "
            "then exact provider identity/data and ethical evidence for at least one member. "
            "A metadata refresh alone cannot satisfy this snapshot prerequisite."
        )
    return (
        "No current eligibility-qualified member is available. Verify/refresh live Trading 212 "
        "metadata and resolve account, identity, provider and ethical evidence; membership "
        "alone does not qualify a stock."
    )


def reconcile_universe_status(
    ctx: QualificationContext, master: dict[str, Any], *, live_refresh: bool
) -> None:
    """Invalidate stale stage status, clearing only a *proved* metadata failure.

    A refresh is not a rerun of snapshot/native/LEAN/CIO/acceptance. Their old
    outputs remain historical and cannot make this status complete.
    """
    previous = ctx.read_json("status.json")
    metadata = live_metadata_state(ctx, master)
    verified = (
        live_refresh and metadata["current"] and metadata.get("current_process_binding_verified")
    )
    if verified:
        ctx.blockers[:] = [
            item for item in ctx.blockers if item.get("code") != "TRADING212_LIVE_METADATA_REQUIRED"
        ]
    if not isinstance(previous, dict):
        return
    blockers = [dict(item) for item in previous.get("blockers", [])]
    if verified:
        blockers = [
            item for item in blockers if item.get("code") != "TRADING212_LIVE_METADATA_REQUIRED"
        ]
    elif master.get("status") == "REFRESH_FAILED":
        blockers = [
            item for item in blockers if item.get("code") != "TRADING212_LIVE_METADATA_REQUIRED"
        ]
        blockers.append(
            {
                "code": "TRADING212_LIVE_METADATA_REQUIRED",
                "action": "The latest live refresh failed; rerun with the ISA-bound credential. Saved raw responses are audit evidence, not current admission.",
            }
        )
    for item in blockers:
        if item.get("code") == "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED":
            item["action"] = no_qualified_snapshot_action(bool(metadata["current"]))
    if not any(item.get("code") == "QUALIFICATION_STAGE_RESULTS_STALE" for item in blockers):
        blockers.append(
            {
                "code": "QUALIFICATION_STAGE_RESULTS_STALE",
                "action": "Universe stage refreshed separately. Rerun build_live_qualification.py after actionable reviews; downstream stages have not been rerun or approved.",
            }
        )
    ctx.write_json(
        "status.json",
        {
            **previous,
            "status": "QUALIFICATION BLOCKED",
            "production_ready": False,
            "manifest_sha256": None,
            "updated_at": ctx.now.isoformat(),
            "last_update_stage": "universe",
            "universe_metadata": metadata,
            "downstream_stage_results_current": False,
            "blockers": blockers,
        },
    )
