"""One rights-aware issuer screening, shared by all verified issuer share lines.

This module prepares evidence and persists machine results, not human approvals.
Live membership and all non-ethical qualification remain in their existing gates.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from money.adapters.eligibility import _UNKNOWN_ACTIVITIES, _activity
from money.data.identifiers import InstrumentIdentifiers
from money.qualification.core import QualificationContext, fingerprint
from money.qualification.universe_reviews import PROVIDERS, _rights, _source
from money.schemas.contracts import (
    EXCLUDED_ACTIVITIES,
    Contract,
    EthicalClearance,
    content_hash,
)


class EthicalDocumentInput(Contract):
    """A document index, not an approval or a claim of issuer-wide coverage."""

    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    provider: str
    dataset: str
    evidence_file: str
    published_at: AwareDatetime
    retrieved_at: AwareDatetime
    evidence_kind: Literal[
        "annual_report", "regulatory_filing", "business_profile", "business_description", "news"
    ] = "annual_report"


def validity_days(ctx: QualificationContext) -> int:
    value = int(ctx.environ.get("MONEY_ETHICAL_CLEARANCE_DAYS", "30"))
    if not 1 <= value <= 365:
        raise ValueError("ETHICAL_CLEARANCE_VALIDITY_INVALID")
    return value


def clearance_artifact(ctx: QualificationContext, clearance: EthicalClearance) -> tuple[str, str]:
    # Runtime binds the exact canonical clearance bytes, not a mutable filename.
    return ctx.artifact(
        json.dumps(
            clearance.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )


def legacy_clearance(
    ctx: QualificationContext,
    entry: Any,
    stamp: Any,
    refs: Sequence[tuple[str, str]],
    expires: datetime,
) -> tuple[EthicalClearance, tuple[str, str]]:
    from money.research.ethics import ethical_policy_hash

    activities = set(map(_activity, entry.business_activities))
    result = (
        "FAIL"
        if activities & set(EXCLUDED_ACTIVITIES)
        else ("UNKNOWN" if activities & _UNKNOWN_ACTIVITIES or not all(activities) else "PASS")
    )
    data: dict[str, Any] = {
        "result": result,
        "issuer_key": "isin:" + entry.isin,
        "isins": (entry.isin,),
        "screened_at": stamp.reviewed_at,
        "valid_until": expires,
        "policy_hash": ethical_policy_hash(),
        "evidence_hashes": tuple(sorted({r[0] for r in refs})),
        "assessed_exclusions": EXCLUDED_ACTIVITIES,
        "reasons": ("LEGACY_SINGLE_SCREENING_REUSED",)
        if result == "PASS"
        else (
            "PROHIBITED_MATERIAL_ACTIVITY"
            if result == "FAIL"
            else "ISSUER_ETHICAL_EVIDENCE_INSUFFICIENT",
        ),
        "business_activities": tuple(entry.business_activities),
    }
    unsigned = EthicalClearance.model_construct(**data, screening_hash="")
    digest = content_hash(unsigned.model_dump(mode="json", exclude={"screening_hash"}))
    clearance = EthicalClearance.model_validate({**data, "screening_hash": digest})
    return clearance, clearance_artifact(ctx, clearance)


def _inputs(ctx: QualificationContext) -> tuple[list[EthicalDocumentInput], set[str]]:
    result = []
    invalid = set()
    try:
        index = ctx.read_json("inputs/universe/ethical-evidence.json") or {}
        for value in index.get("documents", []):
            try:
                result.append(EthicalDocumentInput.model_validate(value))
            except ValueError:
                if isinstance(value, dict) and isinstance(value.get("isin"), str):
                    invalid.add(value["isin"])
                continue
    except (ValueError, OSError, TypeError, AttributeError):
        pass
    ctx.write_json(
        "inputs/universe/ethical-document.schema.json", EthicalDocumentInput.model_json_schema()
    )
    return result, invalid


def _evaluator(ctx: QualificationContext) -> Callable[[str, str], str] | None:
    from money.research.inference_config import load_inference_selections

    try:
        selection = load_inference_selections(ctx.repo, ctx.environ)["crewai"]
        # This bounded preprocessing call does not qualify any native CIO runtime
        # or hosted inference. Local configuration remains local-only evidence.
        return selection.inference(ctx.environ).complete
    except (ValueError, OSError, KeyError):
        return None


def screen_universe(
    ctx: QualificationContext,
    rows: list[dict[str, Any]],
    *,
    legacy: dict[str, dict[str, Any]] | None = None,
    offline: bool = False,
) -> dict[str, dict[str, Any]]:
    from money.research.ethics import (
        ETHICAL_POLICY_VERSION,
        EthicalEvidence,
        GlobalSourceApproval,
        IssuerIdentity,
        IssuerScreening,
        screen_issuer,
    )

    days = validity_days(ctx)
    maximum = int(ctx.environ.get("MONEY_ETHICAL_SCREENINGS_PER_RUN", "20"))
    if not 0 <= maximum <= 100000:
        raise ValueError("ETHICAL_SCREENING_BUDGET_INVALID")
    global_rights = {p: _rights(ctx, p) for p in PROVIDERS}
    inputs, invalid_inputs = _inputs(ctx)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("universe_member") or not row.get("identity_valid"):
            continue
        try:
            identifiers = InstrumentIdentifiers.model_validate(row.get("identifiers"))
            identifiers.require_current(ctx.now)
        except (ValueError, TypeError):
            continue
        key = "isin:" + identifiers.isin
        facts = []
        for ref in row.get("provider_evidence", []):
            try:
                source = _source(ctx, ref, row)
                if source is not None:
                    fact, body = source
                    if fact["dataset"] == "company" and fact["fresh"]:
                        key = "companies-house:" + body["company_number"]
                    facts.append(source)
            except (ValueError, OSError, TypeError, KeyError):
                continue
        group = groups.setdefault(
            key,
            {
                "members": [],
                "name": identifiers.company_name,
                "facts": [],
                "documents": [],
            },
        )
        group["members"].append(row)
        group["facts"].extend(facts)
    evaluator = None if offline else _evaluator(ctx)
    inference = getattr(evaluator, "__self__", None)
    evaluator_identity = (
        f"{inference.provider}/{inference.model}"
        if inference is not None and hasattr(inference, "provider") and hasattr(inference, "model")
        else None
    )
    calls = 0
    result = dict(legacy or {})
    output = []
    ordered = sorted(groups)
    cursor_path = "state/ethical-screening-cursor.json"
    try:
        cursor = (ctx.read_json(cursor_path) or {}).get("last_attempted_issuer")
        if cursor in ordered:
            offset = ordered.index(cursor) + 1
            ordered = ordered[offset:] + ordered[:offset]
    except (ValueError, OSError, TypeError, AttributeError):
        pass
    for key in ordered:
        group = groups[key]
        isins = sorted({r["isin"] for r in group["members"]})
        identity = IssuerIdentity(
            issuer_key=key,
            legal_name=group["name"],
            isins=tuple(isins),
            identity_evidence_hashes=(ctx.artifact({"issuer_key": key, "name": group["name"]})[0],),
        )
        documents: dict[str, EthicalEvidence] = {}
        references = []
        approvals = []
        invalid_evidence = bool(set(isins) & invalid_inputs)
        for provider, rights in global_rights.items():
            if rights.get("approved_datasets") and rights.get("valid_until"):
                approvals.append(
                    GlobalSourceApproval(
                        provider=provider,
                        evidence_hashes=tuple(rights["approval_evidence_hashes"]),
                        permitted_use="ethical-research",
                        valid_until=rights["valid_until"],
                    )
                )
        for source, body in group["facts"]:
            rights = global_rights[source["provider"]]
            if source["dataset"] not in rights["approved_datasets"]:
                continue
            # A current profile is admissible evidence, NOT a complete-business
            # assertion. The evaluator must support coverage from actual quotes.
            # Retrieval/update dates are audit facts, not changed business activity.
            material = {field: value for field, value in body.items() if field != "UpdatedAt"}
            raw = json.dumps(material, sort_keys=True, ensure_ascii=False)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            evidence_kind: Literal["business_profile", "business_description"] = (
                "business_profile"
                if source["dataset"] == "issuer-profile"
                else "business_description"
            )
            try:
                documents[digest] = EthicalEvidence(
                    source_id=digest,
                    provider=source["provider"],
                    issuer_key=key,
                    content=raw,
                    content_sha256=digest,
                    published_at=None,
                    retrieved_at=source["observed_at"],
                    evidence_kind=evidence_kind,
                )
            except ValueError:
                invalid_evidence = True
                continue
            ctx.artifact(raw.encode())
            references.append(source)
        for item in inputs:
            if item.isin not in isins:
                continue
            if (
                item.provider not in global_rights
                or item.dataset not in global_rights[item.provider]["approved_datasets"]
            ):
                invalid_evidence = True
                continue
            try:
                if not item.evidence_file.startswith("inputs/"):
                    invalid_evidence = True
                    continue
                raw_bytes = ctx.read_bytes(item.evidence_file)
                if not raw_bytes or not item.published_at <= item.retrieved_at <= ctx.now:
                    invalid_evidence = True
                    continue
                content = raw_bytes.decode("utf-8")
                # A short provider name is not a legal issuer identifier. Demand
                # an actual security ID or verified registration plus legal name.
                company_bound = key.startswith("companies-house:") and (
                    key.split(":", 1)[1] in content
                    and group["name"].casefold() in content.casefold()
                )
                if not any(isin in content for isin in isins) and not company_bound:
                    invalid_evidence = True
                    continue
                digest, path = ctx.artifact(raw_bytes)
                documents[digest] = EthicalEvidence(
                    source_id=digest,
                    provider=item.provider,
                    issuer_key=key,
                    content=content,
                    content_sha256=digest,
                    published_at=item.published_at,
                    retrieved_at=item.retrieved_at,
                    evidence_kind=item.evidence_kind,
                )
                references.append({"sha256": digest, "path": path, "source": item.evidence_file})
            except (ValueError, OSError, UnicodeError):
                invalid_evidence = True
                continue
        path = f"state/ethical-screenings/{fingerprint(key)}.json"
        previous = None
        try:
            cache = ctx.read_json(path)
            if cache:
                previous = IssuerScreening.model_validate(
                    json.loads(ctx.verify_artifact(cache["sha256"], cache["path"]))
                )
        except (ValueError, OSError, KeyError, TypeError):
            pass

        # Count actual inference calls, not cache hits or missing-evidence UNKNOWN.
        def bounded_evaluate(system: str, user: str, issuer_key: str = key) -> str:
            nonlocal calls
            if evaluator is None or calls >= maximum:
                raise ValueError("ETHICAL_INFERENCE_DEFERRED")
            calls += 1
            ctx.write_json(cursor_path, {"last_attempted_issuer": issuer_key})
            return evaluator(system, user)

        screening = screen_issuer(
            identity,
            tuple(documents.values()),
            now=ctx.now,
            evaluator=bounded_evaluate,
            previous=previous,
            validity_days=days,
            source_approvals=tuple(approvals),
            evidence_integrity_failed=invalid_evidence,
            evaluator_identity=evaluator_identity,
        )
        digest, artifact = ctx.artifact(screening.model_dump(mode="json"))
        ctx.write_json(path, {"sha256": digest, "path": artifact})
        clearance = screening.clearance
        proof = clearance_artifact(ctx, clearance)
        for isin in isins:
            # A machine FAIL/contradiction takes precedence over historical PASS.
            if isin not in result or clearance.result == "FAIL" or documents or invalid_evidence:
                result[isin] = {
                    "clearance": clearance,
                    "proof": proof,
                    "valid_until": min(
                        clearance.valid_until,
                        *(
                            a.valid_until
                            for a in approvals
                            if a.provider in {d.provider for d in documents.values()}
                        ),
                    )
                    if documents
                    else clearance.valid_until,
                }
        effective = result.get(isins[0], {}).get("clearance", clearance)
        output.append(
            {
                "issuer_key": key,
                "isins": isins,
                "result": effective.result,
                "clearance": effective.model_dump(mode="json"),
                "screening_artifact": [digest, artifact],
                "source_references": references,
                "human_review_required": effective.result == "UNKNOWN",
                "legacy_screening_reused": effective != clearance,
                "missing_evidence": "An admissible issuer-wide business/activity disclosure supporting all configured exclusion assessments."
                if effective.result == "UNKNOWN"
                else None,
            }
        )
    counts = {state: 0 for state in ("PASS", "FAIL", "UNKNOWN", "NOT_YET_SCREENED")}
    for row in rows:
        if row.get("universe_member"):
            assessment = result.get(row.get("isin", ""))
            counts[assessment["clearance"].result if assessment else "NOT_YET_SCREENED"] += 1
    ctx.write_json(
        "outputs/ethical-screenings.json",
        {
            "policy": ETHICAL_POLICY_VERSION,
            "generated_at": ctx.now.isoformat(),
            "validity_days": days,
            "inference_calls_this_run": calls,
            "counts": counts,
            "issuer_counts": {
                state: sum(item["result"] == state for item in output)
                for state in ("PASS", "FAIL", "UNKNOWN")
            },
            "issuer_count": len(output),
            "issuers": output,
            "scope": "EVIDENCE_SCREENING_ONLY",
            "production_qualified": False,
        },
    )
    return result
