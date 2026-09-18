"""Evidence-linked diagnostics; never an alternative eligibility authority."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from money.adapters.eligibility import ELIGIBILITY_MAXIMUM_AGE
from money.qualification.core import QualificationContext, fingerprint
from money.qualification.universe_policy import RETIRED_BLOCKERS, UNIVERSE_POLICY_VERSION
from money.usage_policy import UsageMode, usage_mode


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
        saved_reclassification = (
            source.get("scope") == "SAVED_LIVE_DERIVED_RECLASSIFICATION"
            and source.get("status") == "RECLASSIFIED"
        )
        if (
            source.get("universe_policy_version") != UNIVERSE_POLICY_VERSION
            or provenance.get("universe_policy_version") != UNIVERSE_POLICY_VERSION
        ):
            raise ValueError("CURRENT_POLICY_REQUIRED")
        recorded = provenance
        if saved_reclassification:
            recorded = json.loads(ctx.verify_artifact(*provenance["source_provenance"]))
            if any(
                provenance.get(key) != recorded.get(key)
                for key in (
                    "retrieved_at", "credential_binding_sha256", "instrument_response_hash",
                    "raw_instruments",
                )
            ):
                raise ValueError("RECORDED_SOURCE_MISMATCH")
        observed = datetime.fromisoformat(provenance["retrieved_at"])
        binding = provenance["credential_binding_sha256"]
        if (
            (not saved_reclassification and source.get("status") != "REFRESHED")
            or (not saved_reclassification and source.get("scope") != "LIVE_RETRIEVAL")
            or recorded.get("scope") != "LIVE_RETRIEVAL"
            or recorded.get("retrieval_environment") != "live"
            or recorded.get("credential_binding_verified_this_run") is not True
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
        gbp = sum(
            isinstance(item, dict) and item.get("type") == "STOCK"
            and item.get("currencyCode") == "GBP" for item in instruments
        )
        if (
            provenance.get("raw_instruments") != len(instruments)
            or provenance.get("gbx_stocks") != gbx
            or provenance.get("gbp_stocks", 0) != gbp
            or provenance.get("gbp_gbx_stocks", gbx + gbp) != gbx + gbp
        ):
            raise ValueError("RAW_RESPONSE_COUNTS_MISMATCH")
        current = observed <= ctx.now < observed + ELIGIBILITY_MAXIMUM_AGE
        result.update(
            current=current and not saved_reclassification,
            source_current=current,
            reason="SAVED_LIVE_RECLASSIFICATION_NOT_AUTHENTICATED_REFRESH"
            if saved_reclassification and current
            else "CURRENT_LIVE_RETRIEVAL" if current else "LIVE_METADATA_EXPIRED_OR_FUTURE",
            observed_at=observed.isoformat(),
            valid_until=(observed + ELIGIBILITY_MAXIMUM_AGE).isoformat(),
            raw_instruments=len(instruments),
            gbx_stocks=gbx,
            gbp_stocks=gbp,
            gbp_gbx_stocks=gbx + gbp,
            source_provenance_sha256=reference[0],
            instrument_response_sha256=provenance["instrument_response_hash"],
            credential_binding_sha256=binding,
            current_process_binding_verified=current_binding == binding and not saved_reclassification,
        )
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        pass
    return result


def no_qualified_snapshot_action(current_metadata: bool) -> str:
    if current_metadata:
        return (
            "Live Trading 212 metadata is current, but zero current eligibility-qualified members "
            "are available. Resolve provider-rights reviews, "
            "then exact provider identity/data and ethical evidence for at least one member. "
            "A metadata refresh alone cannot satisfy this snapshot prerequisite."
        )
    return (
        "No current eligibility-qualified member is available. Verify/refresh live Trading 212 "
        "metadata and resolve identity, provider and ethical evidence; membership "
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
    if usage_mode(ctx.environ) == UsageMode.PERSONAL_RESEARCH:
        # This is a different stage model, not approval or message suppression.
        # Strict production diagnostics remain available in historical artifacts;
        # research admission is recomputed from authenticated raw broker facts.
        eligible = (master.get("summary") or {}).get("research_eligible", 0)
        blockers = []
        if not verified:
            blockers.append({
                "code": "CURRENT_AUTHENTICATED_BROKER_UNIVERSE_REQUIRED",
                "action": "Refresh genuine live Trading 212 metadata with the bound credentials; saved replay is not current admission.",
            })
        elif not eligible:
            blockers.append({
                "code": "RESEARCH_BASIC_IDENTITY_REQUIRED",
                "action": "No current STOCK quoted GBP/GBX has non-conflicting basic identity. Resolve broker identity conflicts, not company enrichment reviews.",
            })
        ctx.write_json("status.json", {
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "status": "RESEARCH ADMISSION READY" if not blockers else "RESEARCH BLOCKED",
            "production_ready": False,
            "manifest_sha256": None,
            "updated_at": ctx.now.isoformat(),
            "last_update_stage": "research-admission",
            "universe_metadata": metadata,
            "research_eligible": eligible if verified else 0,
            "downstream_stage_results_current": False,
            "blockers": blockers,
        })
        return
    if verified:
        ctx.blockers[:] = [
            item for item in ctx.blockers if item.get("code") != "TRADING212_LIVE_METADATA_REQUIRED"
        ]
    if not isinstance(previous, dict):
        return
    blockers = [
        dict(item) for item in previous.get("blockers", [])
        if item.get("code") not in RETIRED_BLOCKERS
    ]
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
                "action": "The latest live refresh failed; rerun with the configured Trading 212 credentials. Saved raw responses are audit evidence, not current admission.",
            }
        )
    for item in blockers:
        if item.get("code") == "BULK_UNIVERSE_NO_QUALIFIED_MEMBERS":
            item["action"] = (
                "Resolve exact provider and issuer identity, provider-rights and ethical "
                "evidence for at least one current GBX member. Then supply approved "
                "supplemental evidence before snapshot creation."
            )
        elif item.get("code") == "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED":
            item["action"] = no_qualified_snapshot_action(
                bool(metadata["current"] or metadata.get("source_current"))
            )
            if metadata.get("source_current") and not metadata["current"]:
                item["action"] = (
                    "The recorded live Trading 212 retrieval remains within its original "
                    "freshness window, but these classifications were rebuilt offline, "
                    "not authenticated by a new live refresh. Zero current qualified members "
                    "are available. Resolve provider/issuer identity, rights and ethical "
                    "evidence, then resume the normal live finalizer before snapshot admission."
                )
        elif (
            item.get("code") == "TRADING212_LIVE_METADATA_REQUIRED"
            and metadata.get("source_current")
            and master.get("scope") == "SAVED_LIVE_DERIVED_RECLASSIFICATION"
        ):
            item["action"] = (
                "Saved genuine Trading 212 retrieval remains within its original freshness "
                "window; policy classifications were rebuilt offline. This process did not "
                "authenticate a new live refresh. Resume the normal credential-bound finalizer "
                "before admitting research; no account-type attestation is required."
            )
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
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
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
