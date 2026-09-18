"""Bulk human-review preparation; never an admission or approval authority.

Source references may be indexed without asserting permission to reuse their
contents. Factual ethical dossiers require both the existing provider-rights
review and a current independent ethical-use review. Names, descriptions and
SIC codes never establish absence of excluded exposure.
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
ETHICAL_DATASETS = {"eodhd": ["issuer-profile"], "companies-house": ["company", "filing"]}


def _stamp() -> dict[str, Any]:
    return dict(
        status="UNRESOLVED", prepared_by=None, reviewed_by=None, reviewed_at=None, valid_until=None
    )


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
            "version": "money-review-documents-v1",
            "documents": results,
            "account_binding_established": False,
            "current_buy_availability_established": False,
            "rights_approved": False,
            "limitation": (
                "The instruments documentation describes all available instruments. "
                "Public endpoint documentation does not establish which account owns this key, "
                "account-specific ISA membership, or current purchase availability. "
                "Do not approve those claims from metadata presence or maxOpenQuantity."
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
            "ethical_clearance": False, "complete_material_exposure_review": None,
            "required_exclusions": list(EXCLUDED_ACTIVITIES),
            "unresolved_exposures": list(EXCLUDED_ACTIVITIES),
            "human_review_status": "NOT_ASSESSED_BY_PREPARATION",
            "limitation": (
                "Preparation does not reassess or override recorded ethical state. "
                "Descriptions/SIC/filing metadata alone cannot clear material exposure."
            ),
        })
        group["members"].append({
            "trading212_id": row.get("trading212_id"), "isin": isin, "name": row.get("name"),
            "qualification_state": row.get("qualification_state"),
            "recorded_ethical_state": row.get("ethical_state"),
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
        path = f"outputs/ethical-dossiers/{fingerprint(key)}.json"
        ctx.write_json(path, group)
        queue.append({
            "group_key": key, "dossier": path, "status": group["status"],
            "grouping_basis": group["grouping_basis"],
            "member_count": len(group["members"]), "source_count": len(group["sources"]),
            "approved_fact_sources": len(group["approved_source_facts"]),
            "human_review_status": "NOT_ASSESSED_BY_PREPARATION",
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
    """Create resumable, unsigned global reviews and rights-aware issuer packets."""
    ctx.template("inputs/universe/account-scope.json", {
        "review": _stamp(), "account_context": None, "retrieval_environment": "live",
        "credential_binding_sha256": provenance.get("credential_binding_sha256"),
        "accessible_response_is_account_scoped": None,
        "accessible_response_confirms_current_buy_availability": None, "evidence_files": [],
    })
    ctx.template("inputs/universe/ethics.json", {"review": _stamp(), "instruments": []})
    for provider in PROVIDERS:
        ctx.template(f"inputs/provider-rights/{provider}.json", _rights_template(provider))
        ctx.template(f"inputs/universe/source-rights/{provider}.json", {
            "review": _stamp(), "provider": provider, "permitted_use": "ethical-research",
            "dataset_scopes": ETHICAL_DATASETS[provider], "evidence_files": [],
        })
    documents = _documents(ctx, fetch=fetch_documents, offline=offline, fetcher=fetcher)
    rights = {provider: _rights(ctx, provider) for provider in PROVIDERS}
    queue = _dossiers(ctx, rows, rights)
    summary = {
        "version": "money-bulk-human-review-preparation-v1", "generated_at": ctx.now.isoformat(),
        "scope": "HUMAN_REVIEW_PREPARATION_ONLY", "account_review_file": "inputs/universe/account-scope.json",
        "account_machine_facts": {
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
        "account_scope_approved_by_preparation": False,
        "ethics_approved_by_preparation": False, "production_qualified": False,
        "ethics_queue": "outputs/ethics-work-queue.json",
    }
    ctx.write_json("outputs/ethics-work-queue.json", {
        "version": "money-ethical-review-queue-v1", "scope": "HUMAN_REVIEW_PREPARATION_ONLY",
        "groups": queue, "ethical_clearance": False,
    })
    ctx.write_json("outputs/universe-review-tasks.json", summary)
    ctx.write_bytes("outputs/REVIEW_TASKS.md", _instructions(summary).encode())
    return summary


def _instructions(summary: dict[str, Any]) -> str:
    return f"""# Required human reviews

Prepared, not approved. Existing human inputs are never overwritten. No venue
review is required. Public documentation and successful API calls grant no approval.

1. **One account review:** `inputs/universe/account-scope.json`.
   Evidence: `outputs/universe-account-facts.json` and
   `outputs/universe-review-tasks.json` (current credential binding, exact response
   hashes and retrieval time); documentation: `outputs/universe-review-documents.json`.
   Verify this binding belongs to STOCKS_AND_SHARES_ISA, that the metadata response
   is account-specific, and that attached evidence actually establishes current
   purchase availability. Public “all available instruments” documentation proves
   neither account ownership nor buy availability; maxOpenQuantity is not proof.
   Only if supported: account_context=STOCKS_AND_SHARES_ISA,
   accessible_response_is_account_scoped=true,
   accessible_response_confirms_current_buy_availability=true; attach non-secret
   inputs/ evidence_files and the matching credential_binding_sha256. Set
   review.status=REVIEWED with distinct actual prepared_by/reviewed_by and actual
   reviewed_at/valid_until (aware timestamps, existing 24-hour freshness limit).
   If insufficient: leave unresolved; obtain broker/account-specific evidence,
   never tick these fields merely to unblock.

2. **Two provider-wide rights reviews:** `inputs/provider-rights/eodhd.json` and
   `inputs/provider-rights/companies-house.json`. Evidence: captured official
   documentation links/hashes above plus the operator's actual subscription/licence.
   Verify datasets, usage_purpose, storage_policy, redistribution, attribution and
   source_documentation for Money's actual use, including hosted use. Only if
   supported: status=REVIEWED, actual review.reviewed_by/reviewed_at/valid_until,
   all review fields and rights_evidence_file under inputs/. Redistribution must
   be PROHIBITED, FACTS_AND_LINKS or LICENSED as actually supported. API access and
   filing history do not establish licence permission or financial-document rights.
   If insufficient: retain UNRESOLVED and obtain clarification from the provider.

3. **Ethical source use (provider-wide):** `inputs/universe/source-rights/eodhd.json`
   and `inputs/universe/source-rights/companies-house.json`. Verify permitted_use,
   provider, dataset_scopes and actual attached evidence_files authorise ethical
   research. Only then independently sign review.status=REVIEWED with distinct
   actual identities/current timestamps. No per-stock licence signature is needed.
   Until both rights reviews pass, dossiers contain references, not source content.

4. **Issuer-grouped ethical coverage:** `outputs/ethics-work-queue.json` contains
   {summary['issuer_groups']} conservative groups covering {summary['security_count']} securities;
   {summary['rights_approved_fact_sources']} rights-approved fact sources are available.
   Verified company numbers join share classes; otherwise groups remain per ISIN,
   never merged by a similar name. Assess every existing defence/weapons/firearms/
   military and oil exclusion with rights-approved company/annual-report evidence;
   a description, SIC code or missing keyword cannot clear exposure. In
   `inputs/universe/ethics.json`, fill instruments[].isin/company_name,
   business_activities, assessed_exclusions (all policy exclusions), genuine
   inputs/ evidence_files, source_rights_review_files and only when established
   complete_material_exposure_review=true. Independently sign the top review with
   actual identities/timestamps, within the existing 24-hour limit. Material
   excluded activity must remain excluded; unknown exposure stays unresolved.

This preparation changes no ISA, ethical, provider, Qlib, LEAN, native runtime,
inference, first-pass, CIO, release or hosted-production qualification gate.
"""
