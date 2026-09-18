"""Invalidate universe classifications without discarding their source evidence.

Call under the qualification runner lock, before reading derived checkpoints.
The archive is recoverable audit history, never a qualification authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import uuid4

from money.qualification.core import QualificationContext

UNIVERSE_POLICY_VERSION = "money-t212-gbx-stock-universe-v3"
MAXIMUM_PROJECTION_BYTES = 64_000_000
MODE = "state/bulk-universe-mode.json"
JOURNAL = "state/universe-policy-migration.json"
REBUILD_SOURCE = "state/universe-rebuild-source.json"
RETIRED_BLOCKERS = frozenset(
    {"ACCOUNT_ISA_SCOPE_REVIEW_REQUIRED", "ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED"}
)

# Never derive deletion/move targets from operator input or persisted paths.
# The marker is last so interrupted old-policy migrations remain detectable.
DERIVED_PATHS = (
    "outputs/trading212-gbx-stock-universe.json",
    "outputs/trading212-gbx-stock-universe.csv",
    "outputs/uk-isa-stock-universe.json",
    "outputs/uk-isa-stock-universe.csv",
    "outputs/universe-provenance.json",
    "outputs/universe-review-queue.json",
    "outputs/universe-account-facts.json",
    "outputs/providers-result.json",
    "state/provider-stage.json",
    MODE,
)
_SOURCE_FIELDS = (
    "scope",
    "credential_binding_verified_this_run",
    "retrieved_at",
    "observed_at",
    "retrieval_environment",
    "credential_binding_sha256",
    "instrument_response_hash",
    "exchange_response_hash",
    "response_artifacts",
    "exchange_enrichment_status",
)


def _object(raw: bytes | None) -> dict[str, Any] | None:
    """Malformed derived JSON is stale, not permission to trust a checkpoint."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _preserve_source(ctx: QualificationContext, existing: dict[str, bytes]) -> None:
    provenance = _object(existing.get("outputs/universe-provenance.json"))
    if provenance is None:
        master = (
            _object(existing.get("outputs/trading212-gbx-stock-universe.json"))
            or _object(existing.get("outputs/uk-isa-stock-universe.json"))
            or {}
        )
        embedded = master.get("provenance")
        provenance = embedded if isinstance(embedded, dict) else None
    if provenance is None:
        # An interrupted migration may have already saved its source metadata.
        # Never replace it with an empty or fabricated response descriptor.
        return
    source = {key: provenance[key] for key in _SOURCE_FIELDS if key in provenance}
    if source:
        original_reference = provenance.get("source_provenance") or ctx.artifact(provenance)
        ctx.write_json(
            REBUILD_SOURCE,
            {
                "universe_policy_version": UNIVERSE_POLICY_VERSION,
                "source_policy_version": provenance.get("universe_policy_version"),
                "preserved_at": ctx.now.isoformat(),
                "source": source,
                "source_provenance": original_reference,
            },
        )


def ensure_universe_policy(ctx: QualificationContext, *, force: bool = False) -> dict[str, Any]:
    """Archive only stale derived universe files and install the current marker.

    Raw broker/provider evidence, review inputs, snapshots and production
    manifests are never targets. Source response references retain their
    original retrieval time and must still be hash/freshness validated by their
    consumer. No account, identity, ethics or provider approval is migrated.

    A journal records intent before any move. Interrupted forced rebuilds are
    therefore resumed even when remaining projections carry the current policy.
    """
    # Preflight the whole exact target family before changing any active file.
    # QualificationContext rejects symlinks, unsafe paths and secret values.
    existing = {}
    for relative in DERIVED_PATHS:
        raw = ctx.read_bytes(relative, MAXIMUM_PROJECTION_BYTES)
        if raw is not None:
            existing[relative] = raw
    journal_raw = ctx.read_bytes(JOURNAL)
    journal = _object(journal_raw)
    pending = journal_raw is not None and (journal is None or journal.get("status") != "COMPLETE")
    mismatches = [
        relative
        for relative, raw in existing.items()
        if relative.endswith(".json")
        and (_object(raw) or {}).get("universe_policy_version") != UNIVERSE_POLICY_VERSION
    ]
    # CSV has no version field; an orphan projection cannot be authoritative.
    orphan_csv = any(
        f"outputs/{stem}.csv" in existing and f"outputs/{stem}.json" not in existing
        for stem in ("trading212-gbx-stock-universe", "uk-isa-stock-universe")
    )
    rebuild = force or pending or bool(mismatches) or orphan_csv
    result: dict[str, Any] = {
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "rebuilt": rebuild,
        "reason": "FORCED"
        if force
        else "INTERRUPTED_MIGRATION"
        if pending
        else "POLICY_MISMATCH"
        if mismatches or orphan_csv
        else "CURRENT"
        if MODE in existing
        else "INITIALIZED",
        "archived_paths": [],
        "archive_directory": None,
    }
    if rebuild:
        archive = f"state/universe-policy-history/{uuid4().hex}"
        # Guard support paths before writing even the migration journal.
        ctx._path(REBUILD_SOURCE)
        ctx._path(f"{archive}/manifest.json")
        for relative in existing:
            ctx._path(f"{archive}/{relative}")
        _preserve_source(ctx, existing)
        receipt = {
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "status": "IN_PROGRESS",
            "started_at": ctx.now.isoformat(),
            "reason": result["reason"],
            "archive_directory": archive,
            "files": [
                {
                    "path": relative,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                }
                for relative, raw in existing.items()
            ],
        }
        ctx.write_json(JOURNAL, receipt)
        ctx.write_json(f"{archive}/manifest.json", receipt)
        for relative, raw in existing.items():
            source = ctx._path(relative)
            destination = ctx._path(f"{archive}/{relative}")
            # The lock handles cooperating writers; recheck content/path safety
            # immediately before moving to avoid archiving an unexpected file.
            if ctx.read_bytes(relative, MAXIMUM_PROJECTION_BYTES) != raw or destination.exists():
                raise ValueError("UNIVERSE_POLICY_MIGRATION_SOURCE_CHANGED")
            os.rename(source, destination)
            result["archived_paths"].append(relative)
        result["archive_directory"] = archive
    if rebuild or MODE not in existing:
        ctx.write_json(
            MODE,
            {"mode": "bulk-universe", "universe_policy_version": UNIVERSE_POLICY_VERSION},
        )
    if rebuild:
        receipt["status"] = "COMPLETE"
        ctx.write_json(JOURNAL, receipt)
        ctx.write_json(f"{archive}/manifest.json", receipt)
    return result
