"""Issuer evidence preparation and global rights diagnostics, never approval.

Source references may be indexed without asserting permission to reuse their
contents. A provider-wide rights approval can cover ethical use explicitly;
there is no per-issuer licensing review. Names, descriptions and SIC codes never
establish absence of excluded exposure. Screening outcomes are recorded once by
the screening engine, not approved by this preparation layer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from money.data.provider_probes import ProviderAdmissionReview
from money.data.security import ProviderFailure, SafeFetcher, SourceSecurityError
from money.qualification.core import QualificationContext, fingerprint
from money.qualification.providers import IndependentReview, _rights_template
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION
from money.schemas.contracts import EXCLUDED_ACTIVITIES, utc_now

DOCUMENTS = {
    "trading212-instruments": "https://docs.trading212.com/api/instruments/instruments",
    "eodhd-terms": "https://eodhd.com/financial-apis/terms-conditions",
    "eodhd-licensing": "https://eodhd.com/financial-apis/commercial-vs-personal-license-use",
    "companies-house-guidelines": (
        "https://developer.company-information.service.gov.uk/developer-guidelines"
    ),
}
PROVIDERS = ("eodhd", "companies-house")
ETHICAL_DATASETS = {
    "eodhd": ["issuer-profile", "financial", "news"],
    "companies-house": ["company", "filing", "financial"],
}


def _read(ctx: QualificationContext, path: str) -> dict[str, Any]:
    try:
        value = ctx.read_json(path)
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError, TypeError):
        return {}


def _evidence(ctx: QualificationContext, paths: Any) -> bool:
    if not isinstance(paths, (tuple, list)) or not paths or len(paths) > 20:
        return False
    for path in paths:
        if not isinstance(path, str) or not path.startswith("inputs/"):
            return False
        raw = ctx.read_bytes(path)
        if not raw or raw.strip() in {b"{}", b"[]", b"null"}:
            return False
    return True


def _rights(ctx: QualificationContext, provider: str) -> dict[str, Any]:
    """Describe valid reviews without changing any review or provider state."""
    result: dict[str, Any] = {
        "provider": provider,
        "provider_review_file": f"inputs/provider-rights/{provider}.json",
        "ethical_review_file": f"inputs/universe/source-rights/{provider}.json",
        "provider_rights_current": False,
        "ethical_use_current": False,
        "approved_datasets": [],
        "ethical_scope_origin": None,
        "rights_evidence": [],
        "valid_until": None,
        "approval_evidence_hashes": [],
    }
    try:
        value = _read(ctx, result["provider_review_file"])
        path = value.get("rights_evidence_file")
        if value.get("status") == "REVIEWED" and isinstance(path, str) and _evidence(ctx, [path]):
            raw = ctx.read_bytes(path)
            assert raw is not None
            review = ProviderAdmissionReview.model_validate(
                {**value["review"], "rights_evidence_hash": hashlib.sha256(raw).hexdigest()}
            )
            result["provider_rights_current"] = bool(
                review.provider == provider
                and review.reviewed_by.strip()
                and review.reviewed_at <= ctx.now < review.valid_until
            )
            if result["provider_rights_current"]:
                result["rights_evidence"].append({
                    "path": path, "sha256": hashlib.sha256(raw).hexdigest(),
                    "review_file": result["provider_review_file"],
                    "valid_until": review.valid_until.isoformat(),
                })
                result["valid_until"] = review.valid_until.isoformat()
                review_raw = ctx.read_bytes(result["provider_review_file"])
                assert review_raw is not None
                result["approval_evidence_hashes"] = [ctx.artifact(raw)[0], ctx.artifact(review_raw)[0]]
                # The existing signed provider review is sufficient when its
                # typed dataset scopes explicitly cover ethical use. Free-text "research"
                # and successful API access do not grant additional rights.
                datasets = review.ethical_research_datasets
                if datasets and all(item in ETHICAL_DATASETS[provider] for item in datasets):
                    result["ethical_use_current"] = True
                    result["approved_datasets"] = sorted(set(datasets))
                    result["ethical_scope_origin"] = "PROVIDER_REVIEW"
                    return result
        ethical = _read(ctx, result["ethical_review_file"])
        stamp = IndependentReview.model_validate(ethical.get("review"))
        stamp.require_current(ctx.now)
        datasets = ethical.get("dataset_scopes")
        if (
            ethical.get("provider") == provider
            and ethical.get("permitted_use") == "ethical-research"
            and isinstance(datasets, list)
            and datasets
            and all(item in ETHICAL_DATASETS[provider] for item in datasets)
            and _evidence(ctx, ethical.get("evidence_files"))
        ):
            result["ethical_use_current"] = True
            if result["provider_rights_current"]:
                result["approved_datasets"] = sorted(set(datasets))
                result["ethical_scope_origin"] = "LEGACY_GLOBAL_SOURCE_REVIEW"
                result["valid_until"] = min(review.valid_until, stamp.valid_until).isoformat()
                ethical_raw = ctx.read_bytes(result["ethical_review_file"])
                assert ethical_raw is not None
                result["approval_evidence_hashes"].append(ctx.artifact(ethical_raw)[0])
                for evidence_path in ethical["evidence_files"]:
                    raw = ctx.read_bytes(evidence_path)
                    assert raw is not None
                    result["rights_evidence"].append({
                        "path": evidence_path, "sha256": hashlib.sha256(raw).hexdigest(),
                        "review_file": result["ethical_review_file"],
                        "valid_until": stamp.valid_until.isoformat(),
                    })
                    result["approval_evidence_hashes"].append(ctx.artifact(raw)[0])
    except (ValueError, OSError, TypeError, KeyError):
        # Invalid/expired reviews remain unresolved, never unsigned approvals.
        pass
    return result


def _documents(
    ctx: QualificationContext, *, fetch: bool, offline: bool, fetcher: SafeFetcher | None
) -> list[dict[str, Any]]:
    """Four fixed, unauthenticated GETs at most; exact bytes, not web summaries."""
    results = []
    transport = fetcher
    if fetch and not offline and transport is None:
        transport = SafeFetcher(
            frozenset(urlsplit(url).hostname or "" for url in DOCUMENTS.values()),
            maximum_bytes=1_500_000,
            timeout_seconds=8,
            maximum_redirects=0,
        )
    for key, url in DOCUMENTS.items():
        path = f"state/review-documents/{key}.json"
        result: dict[str, Any] = {"id": key, "url": url, "status": "NOT_CAPTURED"}
        cached = _read(ctx, path)
        backoff = False
        try:
            if cached.get("url") == url:
                if cached.get("status") == "CAPTURE_UNAVAILABLE":
                    attempted = datetime.fromisoformat(cached["attempted_at"])
                    backoff = attempted <= ctx.now < attempted + timedelta(minutes=5)
                    if backoff:
                        result = {**cached, "id": key}
                else:
                    ctx.verify_artifact(cached["sha256"], cached["path"])
                    observed = datetime.fromisoformat(cached["observed_at"])
                    if observed <= ctx.now < observed + timedelta(days=7):
                        result = {**cached, "id": key, "status": "CAPTURED"}
        except (ValueError, OSError, KeyError, TypeError):
            result["status"] = "CAPTURE_INVALID_OR_STALE"
        if result["status"] != "CAPTURED" and fetch and not offline and not backoff:
            assert transport is not None
            try:
                response = transport.get(
                    url, mime_types=("text/html", "text/plain", "text/markdown", "application/json")
                )
                digest, artifact = ctx.artifact(response.content)
                result = {
                    "id": key,
                    "url": url,
                    "status": "CAPTURED",
                    "sha256": digest,
                    "path": artifact,
                    "mime": response.mime,
                    "observed_at": utc_now().isoformat(),
                }
                if key == "trading212-instruments":
                    result["all_available_instruments_heading_detected"] = (
                        "all available instruments" in response.content.decode(errors="replace").lower()
                    )
                ctx.write_json(path, result)
            except (ProviderFailure, SourceSecurityError, ValueError, OSError):
                result["status"] = "CAPTURE_UNAVAILABLE"
                result["attempted_at"] = utc_now().isoformat()
                ctx.write_json(path, result)
        results.append(result)
    ctx.write_json(
        "outputs/universe-review-documents.json",
        {
            "version": "money-review-documents-v2",
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "documents": results,
            "rights_approved": False,
            "limitation": (
                "The instruments documentation describes all available instruments. "
                "Live response integrity and freshness remain required. "
                "This policy does not assert account type, ISA eligibility or buy availability. "
                "Public documentation and successful API access do not establish reuse rights."
            ),
        },
    )
    return results


def _source(
    ctx: QualificationContext, ref: Any, row: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Recheck raw identity-bound observations instead of trusting derived facts."""
    if not isinstance(ref, dict):
        return None
    digest, path = ref.get("sha256"), ref.get("path")
    if not isinstance(digest, str) or path not in {
        f"artifacts/{digest}.json", f"artifacts/{digest}.bin"
    }:
        return None
    envelope = json.loads(ctx.verify_artifact(digest, path))
    if envelope.get("version") != "money-bulk-provider-response-v2":
        return None
    request = envelope.get("request", {})
    if not isinstance(request, dict) or request.get("version") != 2:
        return None
    observed = datetime.fromisoformat(envelope["observed_at"])
    valid = datetime.fromisoformat(envelope["valid_until"])
    current = observed <= ctx.now < valid <= observed + timedelta(days=1)
    host, endpoint, response = request.get("host"), request.get("path"), envelope.get("response")
    if not isinstance(response, dict):
        return None
    facts: dict[str, Any]
    provider: str
    dataset: str
    fields: tuple[str, ...]
    if (
        host == "eodhd.com"
        and row.get("eodhd_mapping_state") == "MAPPED"
        and isinstance(row.get("eodhd_symbol"), str)
        and endpoint == "/api/fundamentals/" + row["eodhd_symbol"]
    ):
        general = response.get("General", response)
        identity = row.get("eodhd_identity", {})
        if not isinstance(general, dict) or not isinstance(identity, dict):
            return None
        if (
            general.get("ISIN") != row.get("isin")
            or not general.get("ISIN")
            or general.get("Code") != identity.get("Code")
            or not general.get("Code")
            or general.get("Name") != identity.get("Name")
            or not general.get("Name")
            or general.get("CurrencyCode") != row.get("quote_currency")
            or str(general.get("Type", "")).casefold() not in {"common stock", "stock", "ordinary shares"}
            or general.get("IsDelisted") is not False
        ):
            return None
        fields = ("Name", "ISIN", "CountryISO", "Description", "Sector", "Industry", "UpdatedAt")
        facts = {key: general[key] for key in fields if key in general}
        provider, dataset = "eodhd", "issuer-profile"
    elif (
        host == "api.company-information.service.gov.uk"
        and row.get("companies_house_state") == "MAPPED"
        and re.fullmatch(r"[A-Z0-9]{8}", str(row.get("companies_house_number", "")))
        and endpoint == "/company/" + row["companies_house_number"]
        and response.get("company_number") == row["companies_house_number"]
        and response.get("company_name") == row.get("legal_company_name")
        and response.get("company_status") == "active"
    ):
        fields = ("company_number", "company_name", "company_status", "type", "sic_codes")
        facts = {key: response[key] for key in fields if key in response}
        provider, dataset = "companies-house", "company"
    elif (
        host == "api.company-information.service.gov.uk"
        and row.get("companies_house_state") == "MAPPED"
        and re.fullmatch(r"[A-Z0-9]{8}", str(row.get("companies_house_number", "")))
        and endpoint == "/company/" + row["companies_house_number"] + "/filing-history"
        and isinstance(response.get("items"), list)
    ):
        fields = ("transaction_id", "category", "type", "date", "description")
        facts = {
            "accounts_filings": [
                {key: item[key] for key in fields if key in item}
                for item in response["items"]
                if isinstance(item, dict) and item.get("category") == "accounts"
            ][:4],
            "financial_document_content_verified": False,
        }
        provider, dataset = "companies-house", "filing"
    else:
        return None
    return {
        "provider": provider, "dataset": dataset, "sha256": digest, "path": path,
        "url": "https://" + host + endpoint, "observed_at": envelope["observed_at"],
        "valid_until": envelope["valid_until"], "fresh": current,
    }, facts


def _dossiers(
    ctx: QualificationContext, rows: Sequence[dict[str, Any]], rights: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("universe_member") is not True:
            continue
        sources: list[dict[str, Any]] = []
        factual: list[dict[str, Any]] = []
        company_number = None
        errors: list[str] = []
        for ref in row.get("provider_evidence", [])[:1000]:
            try:
                observation = _source(ctx, ref, row)
                if observation is None:
                    continue
                source, facts = observation
                permitted = source["dataset"] in rights[source["provider"]]["approved_datasets"]
                source["content_use"] = "RIGHTS_REVIEWED" if permitted else "REFERENCES_ONLY"
                sources.append(source)
                if source["dataset"] == "company" and source["fresh"]:
                    company_number = facts["company_number"]
                if permitted and source["fresh"]:
                    factual.append({"source_sha256": source["sha256"], "facts": facts})
            except (ValueError, OSError, TypeError, KeyError, AttributeError):
                errors.append("SOURCE_INTEGRITY_OR_IDENTITY_UNVERIFIED")
        # Only an actual issuer identifier joins share classes. A name match,
        # an ISIN prefix, or a ticker suffix never groups unrelated issuers.
        isin = row.get("isin")
        valid_isin = isinstance(isin, str) and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin)
        group_key = (
            "companies-house:" + company_number if company_number else
            "isin:" + str(isin) if valid_isin else
            "unresolved:" + fingerprint([row.get("trading212_id"), row.get("raw_sha256")])
        )
        group = groups.setdefault(group_key, {
            "group_key": group_key,
            "grouping_basis": "VERIFIED_COMPANY_NUMBER" if company_number else "SECURITY_IDENTITY_ONLY",
            "status": "DOSSIER_PREPARATION_ONLY", "members": [], "sources": [],
            "approved_source_facts": [], "source_errors": [],
            "approval_granted_by_preparation": False,
            "required_exclusions": list(EXCLUDED_ACTIVITIES),
            "unresolved_exposures": list(EXCLUDED_ACTIVITIES),
            "limitation": (
                "Preparation does not reassess or override recorded ethical state. "
                "Descriptions/SIC/filing metadata alone cannot clear material exposure."
            ),
        })
        group["members"].append({
            "trading212_id": row.get("trading212_id"), "isin": isin, "name": row.get("name"),
            "qualification_state": row.get("qualification_state"),
            "recorded_ethical_state": row.get("ethical_state"),
            "ethical_screening": row.get("ethical_screening"),
        })
        for source in sources:
            if source not in group["sources"]:
                group["sources"].append(source)
        for facts in factual:
            if facts not in group["approved_source_facts"]:
                group["approved_source_facts"].append(facts)
        group["source_errors"] = sorted(set(group["source_errors"] + errors))
    queue = []
    for key, group in sorted(groups.items()):
        group["members"].sort(key=lambda item: (str(item["isin"]), str(item["trading212_id"])))
        group["sources"].sort(key=lambda item: item["sha256"])
        group["approved_source_facts"].sort(key=lambda item: item["source_sha256"])
        states = {
            item["recorded_ethical_state"] for item in group["members"]
            if item["recorded_ethical_state"] in {"PASS", "FAIL", "UNKNOWN", "NOT_YET_SCREENED"}
        }
        state = next(iter(states)) if len(states) == 1 else "UNKNOWN" if states else "NOT_YET_SCREENED"
        group["screening_state"] = state
        group["screening_state_conflict"] = len(states) > 1
        # This is a view of the engine's outcome, not a second approval layer.
        # Legacy clearance names are not silently promoted into new PASSes.
        group["human_action_required"] = state == "UNKNOWN"
        group["human_review_status"] = (
            "EVIDENCE_RESOLUTION_REQUIRED" if state == "UNKNOWN" else "NOT_REQUIRED"
        )
        group["next_action"] = {
            "PASS": "Reuse the persisted issuer screening; do not review again downstream.",
            "FAIL": "Exclude the issuer; a reviewer cannot override supported excluded exposure.",
            "UNKNOWN": "Resolve the missing or contradictory issuer evidence identified by the screening.",
            "NOT_YET_SCREENED": "Acquire admissible issuer evidence and run the machine screening.",
        }[state]
        if state in {"PASS", "FAIL"}:
            group["unresolved_exposures"] = []
        path = f"outputs/ethical-dossiers/{fingerprint(key)}.json"
        ctx.write_json(path, group)
        queue.append({
            "group_key": key, "dossier": path, "status": group["status"],
            "grouping_basis": group["grouping_basis"],
            "member_count": len(group["members"]), "source_count": len(group["sources"]),
            "approved_fact_sources": len(group["approved_source_facts"]),
            "human_review_status": group["human_review_status"],
            "screening_state": state,
            "human_action_required": group["human_action_required"],
            "next_action": group["next_action"],
        })
    return queue


def prepare_universe_reviews(
    ctx: QualificationContext,
    rows: Sequence[dict[str, Any]],
    provenance: dict[str, Any],
    *,
    fetch_documents: bool = False,
    offline: bool = False,
    fetcher: SafeFetcher | None = None,
) -> dict[str, Any]:
    """Prepare global rights and issuer evidence without a second ethical review."""
    for provider in PROVIDERS:
        ctx.template(f"inputs/provider-rights/{provider}.json", _rights_template(provider))
        # Existing scoped global source reviews remain readable, but new runs
        # do not create another approval task alongside the provider review.
    documents = _documents(ctx, fetch=fetch_documents, offline=offline, fetcher=fetcher)
    rights = {provider: _rights(ctx, provider) for provider in PROVIDERS}
    queue = _dossiers(ctx, rows, rights)
    summary = {
        "version": "money-bulk-human-review-preparation-v3", "generated_at": ctx.now.isoformat(),
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "scope": "HUMAN_REVIEW_PREPARATION_ONLY",
        "legacy_account_review": "DEPRECATED_IGNORED_NOT_APPROVED",
        "live_retrieval_machine_facts": {
            key: provenance.get(key) for key in (
                "credential_binding_sha256", "instrument_response_hash", "exchange_response_hash",
                "retrieved_at", "retrieval_environment", "raw_instruments", "gbx_stocks",
            )
        },
        "provider_rights": rights, "issuer_groups": len(queue),
        "verified_issuer_groups": sum(
            item["grouping_basis"] == "VERIFIED_COMPANY_NUMBER" for item in queue
        ),
        "security_identity_groups": sum(
            item["grouping_basis"] == "SECURITY_IDENTITY_ONLY" for item in queue
        ),
        "security_count": sum(item["member_count"] for item in queue),
        "rights_approved_fact_sources": sum(item["approved_fact_sources"] for item in queue),
        "official_documents_captured": sum(item["status"] == "CAPTURED" for item in documents),
        "ethics_approved_by_preparation": False, "production_qualified": False,
        "ethics_queue": "outputs/ethics-work-queue.json",
        "ethical_screening_counts": {
            state: sum(item["screening_state"] == state for item in queue)
            for state in ("PASS", "FAIL", "UNKNOWN", "NOT_YET_SCREENED")
        },
        "human_ethical_resolution_groups": sum(item["human_action_required"] for item in queue),
    }
    ctx.write_json("outputs/ethics-work-queue.json", {
        "version": "money-ethical-review-queue-v2", "scope": "ISSUER_SCREENING_WORK_QUEUE",
        "groups": queue, "approval_granted_by_preparation": False,
        "human_review_groups": [item for item in queue if item["human_action_required"]],
        "machine_work_groups": [item for item in queue if item["screening_state"] == "NOT_YET_SCREENED"],
        "counts": summary["ethical_screening_counts"],
    })
    ctx.write_json("outputs/universe-review-tasks.json", summary)
    ctx.write_bytes("outputs/REVIEW_TASKS.md", _instructions(summary).encode())
    legacy_path = "outputs/ACCOUNT_SCOPE_REVIEW.md"
    historical = ctx.read_bytes(legacy_path)
    archived = None
    if historical and not historical.startswith(b"# Deprecated account-scope review"):
        digest, path = ctx.artifact(historical)
        archived = {"sha256": digest, "path": path}
    if archived or historical is None:
        notice = (
            "# Deprecated account-scope review\n\n"
            f"Policy {UNIVERSE_POLICY_VERSION} no longer requires account-type, ISA-scope "
            "or current-ISA-buyability attestation. Legacy account-scope inputs and schemas "
            "are ignored, not approved. No reviewer or timestamp must be supplied.\n\n"
            "Live Trading 212 response integrity, original timestamps, freshness and technical "
            "credential binding remain required; all other evidence and review gates remain.\n"
        )
        if archived:
            notice += (
                "\nSuperseded preparation retained for audit only: "
                f"{archived['path']} (SHA256 {archived['sha256']}).\n"
            )
        ctx.write_bytes(legacy_path, notice.encode())
    return summary


def _instructions(summary: dict[str, Any]) -> str:
    return f"""# Remaining reviews and issuer evidence

Prepared, not approved. Existing human inputs are never overwritten. No venue
review is required. Public documentation and successful API calls grant no approval.

Legacy `inputs/universe/account-scope.json` and its schema are deprecated and
ignored, not approved. No account-type, ISA-scope or purchase-availability review
is required. Technical credential binding still protects live provenance.

1. **Two provider-wide rights reviews:** `inputs/provider-rights/eodhd.json` and
   `inputs/provider-rights/companies-house.json`. Evidence: captured official
   documentation links/hashes above plus the operator's actual subscription/licence.
   Verify datasets, usage_purpose, storage_policy, redistribution, attribution and
   source_documentation for Money's actual use, including hosted use. Only if
   supported: status=REVIEWED, actual review.reviewed_by/reviewed_at/valid_until,
   all review fields and rights_evidence_file under inputs/. Redistribution must
   be PROHIBITED, FACTS_AND_LINKS or LICENSED as actually supported. API access and
   filing history do not establish licence permission or financial-document rights.
   If insufficient: retain UNRESOLVED and obtain clarification from the provider.

2. **Ethical source use belongs to that same provider-wide review.** Set
   review.ethical_research_datasets only to the scopes the attached licence permits
   for this use: EODHD issuer-profile/financial/news or Companies House
   company/filing/financial. No additional ethical-source signature or per-issuer
   rights review is required. Existing `inputs/universe/source-rights/*.json`
   approvals remain readable as optional global scope supplements. Successful
   requests, free-text "research", and public access alone do not grant reuse rights.

3. **One ethical screening per verified issuer:** `outputs/ethics-work-queue.json` contains
   {summary['issuer_groups']} conservative groups covering {summary['security_count']} securities;
   {summary['rights_approved_fact_sources']} rights-approved fact sources are available.
   Verified company numbers join share classes; otherwise groups remain per ISIN,
   never merged by a similar name. The machine screening checks every configured
   defence/weapons/firearms/military and oil exclusion using admissible issuer-wide
   activity evidence. A name, SIC code or missing keyword cannot clear exposure.
   PASS is reused downstream, FAIL is excluded, UNKNOWN needs the missing or
   contradictory evidence resolved. No second independent ethical reviewer is needed.
   NOT_YET_SCREENED is automatic acquisition/screening work, not a human sign-off.
   Current issuer states: {summary['ethical_screening_counts']}.
   UNKNOWN resolution groups: {summary['human_ethical_resolution_groups']}.

The stored screening supplies its own validity period. Daily live broker/provider
freshness does not require a daily ethical review. Re-screen on identity/material
evidence/policy change, credible contradiction, or clearance expiry. These files
record existing results; preparation grants no new PASS or approval. Provider,
Qlib mode, mandatory LEAN, native/security, first-pass, CIO and release gates remain.
"""
