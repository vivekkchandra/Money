"""Exact official-issuer/Companies House fallback, without licensing approval.

Selections identify public sources to inspect, not facts to trust. Every use
rehashes their actual bytes and repeats the exact security/company join. This
module never qualifies financial datasets, ethical exposure, or live membership.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import Field, model_validator

from money.data.identifiers import InstrumentIdentifiers
from money.data.security import (
    ProviderFailure,
    SafeFetcher,
    SourceSecurityError,
    _TextOnly,
    validate_url,
)
from money.data.source_policy import IssuerSourcePolicy, issuer_source_policy
from money.qualification.core import QualificationContext
from money.schemas.contracts import Contract, utc_now

ISSUER_SOURCE_VERSION = "money-authoritative-issuer-sources-v2"
LEGACY_ISSUER_SOURCE_VERSION = "money-authoritative-issuer-sources-v1"
API_HOST = "api.company-information.service.gov.uk"
PUBLIC_HOST = "find-and-update.company-information.service.gov.uk"
EXCHANGE_HOST = "www.londonstockexchange.com"
REGULATOR_HOST = "data.fca.org.uk"
REGULATOR_DOCUMENT_PATH = (
    r"/artefacts/NSM/RNS/(?:[0-9]+|"
    r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.html"
)
_SAFE_CAPTURE_ERROR_CODES = frozenset({
    "PROVIDER_DNS_UNAVAILABLE", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE",
    "SOURCE_URL_DENIED", "SOURCE_ADDRESS_DENIED", "SOURCE_HEADER_DENIED",
    "SOURCE_REDIRECT_LIMIT", "SOURCE_REDIRECT_INVALID", "SOURCE_CROSS_ORIGIN_REDIRECT_DENIED",
    "SOURCE_COMPRESSION_DENIED", "SOURCE_MIME_DENIED", "SOURCE_SIZE_LIMIT",
})


class IssuerSourceSelection(Contract):
    """Explicit official source locations; no rights or identity attestation."""

    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    company_number: str = Field(pattern=r"^[A-Z0-9]{8}$")
    legal_name: str = Field(min_length=3, max_length=200)
    ticker: str = Field(pattern=r"^[A-Za-z0-9_-]{1,25}$")
    official_issuer_host: str
    issuer_identity_urls: tuple[str, ...] = Field(min_length=1, max_length=4)
    security_identity_url: str
    business_disclosure_urls: tuple[str, ...] = Field(default=(), max_length=4)
    linked_disclosure_urls: dict[str, str] = Field(default_factory=dict, max_length=6)
    terms_url: str | None = None

    @model_validator(mode="after")
    def safe_sources(self) -> IssuerSourceSelection:
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,63}", self.official_issuer_host):
            raise ValueError("ISSUER_HOST_INVALID")
        for document, linking_page in self.linked_disclosure_urls.items():
            validate_url(linking_page, frozenset({self.official_issuer_host}))
            host = urlsplit(document).hostname
            if not host or urlsplit(document).query or urlsplit(linking_page).query:
                raise ValueError("ISSUER_SOURCE_QUERY_DENIED")
            validate_url(document, frozenset({host}))
        for url in (*self.issuer_identity_urls, *self.business_disclosure_urls):
            host = urlsplit(url).hostname
            allowed = {self.official_issuer_host}
            if url in self.linked_disclosure_urls and host:
                allowed.add(host)
            if host == REGULATOR_HOST and re.fullmatch(REGULATOR_DOCUMENT_PATH, urlsplit(url).path):
                allowed.add(host)
            validate_url(url, frozenset(allowed))
            if urlsplit(url).query:
                raise ValueError("ISSUER_SOURCE_QUERY_DENIED")
        validate_url(self.security_identity_url, frozenset({
            EXCHANGE_HOST, REGULATOR_HOST, self.official_issuer_host,
        }))
        security = urlsplit(self.security_identity_url)
        if security.query or (security.hostname == EXCHANGE_HOST and not re.fullmatch(
            r"/stock/" + re.escape(self.ticker) + r"/[a-z0-9-]+/company-page",
            security.path,
        )) or (security.hostname == REGULATOR_HOST and not re.fullmatch(
            REGULATOR_DOCUMENT_PATH, security.path,
        )):
            raise ValueError("ISSUER_SECURITY_SOURCE_INVALID")
        if self.terms_url:
            validate_url(self.terms_url, frozenset({self.official_issuer_host}))
        return self


class _DisclosureLinks(HTMLParser):
    """Only actual anchor links establish an explicitly selected hosted document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)


def _require_link(ctx: QualificationContext, selection: IssuerSourceSelection,
                  receipt: dict[str, Any], url: str) -> None:
    linking_page = selection.linked_disclosure_urls.get(url)
    if linking_page is None:
        return
    refs = [source for source in receipt.get("link_sources", []) if source.get("url") == linking_page]
    if len(refs) != 1:
        raise ValueError("ISSUER_OFFICIAL_DOCUMENT_LINK_REQUIRED")
    parser = _DisclosureLinks()
    parser.feed(_read_source(ctx, refs[0]).decode("utf-8"))
    if url not in {urljoin(linking_page, link).split("#", 1)[0] for link in parser.links}:
        raise ValueError("ISSUER_OFFICIAL_DOCUMENT_LINK_REQUIRED")


def _text(raw: bytes) -> str:
    if len(raw) > 1_900_000:
        raise ValueError("ISSUER_DOCUMENT_SIZE_LIMIT")
    parser = _TextOnly()
    parser.feed(raw.decode("utf-8"))
    text = unicodedata.normalize("NFKC", " ".join(parser.parts))
    text = " ".join(text.split())
    if not text or len(text) > 200_000:
        raise ValueError("ISSUER_DOCUMENT_TEXT_LIMIT")
    return text


def _contains(text: str, value: str) -> bool:
    """Case/whitespace normalization only: never fuzzy/legal-name suffix removal."""
    text, value = " ".join(text.upper().split()), " ".join(value.upper().split())
    return re.search(r"(?<![A-Z0-9])" + re.escape(value) + r"(?![A-Z0-9])", text) is not None


def _selection_path(isin: str) -> str:
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        raise ValueError("ISSUER_ISIN_INVALID")
    return f"inputs/issuer-sources/{isin}.json"


def _receipt_path(isin: str) -> str:
    _selection_path(isin)
    return f"state/issuer-sources/{isin}.json"


def _capture(
    ctx: QualificationContext,
    transport: SafeFetcher,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_response: bool = False,
) -> dict[str, Any]:
    response = transport.get(
        url,
        headers=headers,
        mime_types=("application/json",) if json_response else ("text/html", "text/plain"),
    )
    observed = utc_now()
    digest, path = ctx.artifact(response.content)
    return {
        "url": url,
        "sha256": digest,
        "path": path,
        "mime": response.mime,
        "observed_at": observed.isoformat(),
        "valid_until": (observed + timedelta(days=1)).isoformat(),
    }


def _read_source(ctx: QualificationContext, ref: dict[str, Any]) -> bytes:
    observed = datetime.fromisoformat(ref["observed_at"])
    expires = datetime.fromisoformat(ref["valid_until"])
    if not observed <= ctx.now < expires <= observed + timedelta(days=1):
        raise ValueError("ISSUER_SOURCE_STALE")
    return ctx.verify_artifact(ref["sha256"], ref["path"])


def _validate(
    ctx: QualificationContext,
    row: dict[str, Any],
    selection: IssuerSourceSelection,
    receipt: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    policy = issuer_source_policy(ctx.environ)
    identifiers = InstrumentIdentifiers.model_validate(row.get("identifiers"))
    identifiers.require_current(ctx.now)
    provider = row.get("eodhd_identity", {})
    if (
        receipt.get("version") not in {ISSUER_SOURCE_VERSION, LEGACY_ISSUER_SOURCE_VERSION}
        or IssuerSourceSelection.model_validate(receipt.get("selection")) != selection
        or receipt.get("issuer_source_policy", "companies_house") != policy.value
        or row.get("identity_valid") is not True
        or row.get("universe_member") is not True
        or row.get("instrument_type") != "STOCK"
        or row.get("quote_currency") != "GBX"
        or identifiers.isin != selection.isin
        or row.get("isin") != selection.isin
        or identifiers.trading212_id != row.get("trading212_id")
        or identifiers.exchange_ticker != selection.ticker
        or provider.get("ISIN") != selection.isin
        or provider.get("Code") != selection.ticker
        or provider.get("Currency") != "GBX"
        or str(provider.get("Type", "")).casefold() not in {"stock", "common stock", "ordinary shares"}
        or row.get("eodhd_symbol") != provider.get("Code", "") + "." + provider.get("Exchange", "")
        or dict(identifiers.provider_symbols).get("eodhd") != row.get("eodhd_symbol")
    ):
        raise ValueError("ISSUER_SECURITY_IDENTITY_MISMATCH")
    sources = receipt["identity_sources"]
    if [r["url"] for r in sources] != list(selection.issuer_identity_urls):
        raise ValueError("ISSUER_SOURCE_LOCATION_MISMATCH")
    for linking_source in receipt.get("link_sources", []):
        if linking_source["url"] not in selection.linked_disclosure_urls.values():
            raise ValueError("ISSUER_SOURCE_LOCATION_MISMATCH")
        _read_source(ctx, linking_source)
    for source in sources:
        _require_link(ctx, selection, receipt, source["url"])
    combined = "\n".join(_text(_read_source(ctx, source)) for source in sources)
    for exact in (selection.legal_name, selection.company_number, selection.ticker):
        if not _contains(combined, exact):
            raise ValueError("OFFICIAL_ISSUER_CORROBORATION_REQUIRED")
    security = receipt["security_source"]
    if security["url"] != selection.security_identity_url:
        raise ValueError("ISSUER_SOURCE_LOCATION_MISMATCH")
    security_text = _text(_read_source(ctx, security))
    # The regulator supplies exact legal name + ISIN. The current provider join
    # and the issuer's own current disclosure independently supply the ticker.
    security_fields: tuple[str, ...] = (selection.legal_name, selection.isin)
    if urlsplit(security["url"]).hostname != REGULATOR_HOST:
        security_fields += (selection.ticker,)
    for exact in security_fields:
        if not _contains(security_text, exact):
            raise ValueError("OFFICIAL_SECURITY_CORROBORATION_REQUIRED")
    number = selection.company_number
    company = receipt.get("company_source", {})
    if not isinstance(company, dict):
        raise ValueError("COMPANIES_HOUSE_EXACT_ISSUER_MISMATCH")
    company_raw = _read_source(ctx, company) if policy == IssuerSourcePolicy.COMPANIES_HOUSE else None
    if policy == IssuerSourcePolicy.OFFICIAL_DISCLOSURES:
        profile = {"company_number": number, "company_name": selection.legal_name}
        jurisdiction = None
    elif company["url"] == f"https://{API_HOST}/company/{number}":
        assert company_raw is not None
        profile = json.loads(company_raw)
        if (
            profile.get("company_number") != number
            or " ".join(str(profile.get("company_name", "")).upper().split())
            != " ".join(selection.legal_name.upper().split())
            or profile.get("company_status") != "active"
            or profile.get("type") != "plc"
        ):
            raise ValueError("COMPANIES_HOUSE_EXACT_ISSUER_MISMATCH")
        jurisdiction = profile.get("jurisdiction")
    elif company["url"] == f"https://{PUBLIC_HOST}/company/{number}":
        assert company_raw is not None
        public = _text(company_raw)
        if not all(_contains(public, fact) for fact in (
            selection.legal_name, "Company number " + number,
            "Company status Active", "Company type Public limited Company",
        )):
            raise ValueError("COMPANIES_HOUSE_EXACT_ISSUER_MISMATCH")
        profile = {"company_number": number, "company_name": selection.legal_name,
                   "company_status": "active", "type": "plc"}
        jurisdiction = None
    else:
        raise ValueError("ISSUER_SOURCE_LOCATION_MISMATCH")
    # A registry identity plus the issuer's explicit incorporation disclosure,
    # not an ISIN prefix, trading venue, company name or guessed country.
    normalized = combined.upper().replace("&", "AND")
    if re.search(r"REGISTERED IN ENGLAND\s+AND\s+WALES", normalized):
        disclosed = "england-wales"
    elif re.search(r"INCORPORATED IN SCOTLAND", normalized):
        disclosed = "scotland"
    elif re.search(r"INCORPORATED IN NORTHERN IRELAND", normalized):
        disclosed = "northern-ireland"
    else:
        raise ValueError("OFFICIAL_ISSUER_JURISDICTION_REQUIRED")
    if jurisdiction is not None and jurisdiction != disclosed:
        raise ValueError("ISSUER_JURISDICTION_CONFLICT")
    known = row.get("issuer_facts") or {}
    if (known.get("Jurisdiction") not in (None, "", disclosed)
            or known.get("CountryISO") not in (None, "", "GB")
            or row.get("issuer_company_number") not in (None, "", number)):
        raise ValueError("ISSUER_JURISDICTION_CONFLICT")
    profile["jurisdiction"] = disclosed
    evidence = [*sources, security, *receipt.get("link_sources", [])]
    if policy == IssuerSourcePolicy.COMPANIES_HOUSE:
        evidence.append(company)
    return profile, evidence


def apply_issuer_sources(ctx: QualificationContext, row: dict[str, Any]) -> bool:
    """Apply a current exact corroboration; absence/errors leave the row unchanged."""
    try:
        selection = IssuerSourceSelection.model_validate(ctx.read_json(_selection_path(row["isin"])))
        receipt = ctx.read_json(_receipt_path(selection.isin))
        if not isinstance(receipt, dict):
            return False
        profile, sources = _validate(ctx, row, selection, receipt)
        observed = min(datetime.fromisoformat(source["observed_at"]) for source in sources)
        policy = issuer_source_policy(ctx.environ)
        provider_refs = []
        for key in ("company_source", "filing_source"):
            if policy != IssuerSourcePolicy.COMPANIES_HOUSE:
                continue
            ref = receipt.get(key)
            if not isinstance(ref, dict) or urlsplit(ref["url"]).hostname != API_HOST:
                continue
            endpoint = urlsplit(ref["url"]).path
            if endpoint not in {
                "/company/" + selection.company_number,
                "/company/" + selection.company_number + "/filing-history",
            }:
                continue
            body = json.loads(_read_source(ctx, ref))
            digest, path = ctx.artifact({
                "version": "money-bulk-provider-response-v2",
                "request": {"version": 2, "host": API_HOST, "path": endpoint},
                "observed_at": ref["observed_at"], "valid_until": ref["valid_until"],
                "response": body,
            })
            provider_refs.append({"sha256": digest, "path": path})
        row.update(
            legal_company_name=profile["company_name"],
            issuer_identity_state="VERIFIED",
            issuer_company_number=selection.company_number,
            issuer_facts={"Name": profile["company_name"], "ISIN": selection.isin,
                          "CountryISO": "GB", "Jurisdiction": profile["jurisdiction"],
                          "WebURL": "https://" + selection.official_issuer_host},
            issuer_facts_source=("Exact official issuer + official security + Companies House"
                                 if policy == IssuerSourcePolicy.COMPANIES_HOUSE
                                 else "Exact official issuer + official security disclosure"),
            issuer_source_policy=policy.value,
            issuer_source_evidence=sources,
            issuer_source_observed_at=observed.isoformat(),
        )
        if policy == IssuerSourcePolicy.COMPANIES_HOUSE:
            row.update(companies_house_number=selection.company_number,
                       companies_house_state="MAPPED", company_status=profile["company_status"])
        for reference in provider_refs:
            if reference not in row.setdefault("provider_evidence", []):
                row["provider_evidence"].append(reference)
        # A public HTML profile is not relabelled an API/dataset qualification.
        return True
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return False


def validated_issuer_documents(ctx: QualificationContext, row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return integrity/issuer-bound disclosure references, never an ethical result."""
    try:
        selection = IssuerSourceSelection.model_validate(ctx.read_json(_selection_path(row["isin"])))
        receipt = ctx.read_json(_receipt_path(selection.isin))
        if not isinstance(receipt, dict):
            return []
        _validate(ctx, row, selection, receipt)
        result = []
        for document in receipt.get("business_sources", []):
            if document["url"] not in selection.business_disclosure_urls:
                raise ValueError("ISSUER_SOURCE_LOCATION_MISMATCH")
            _require_link(ctx, selection, receipt, document["url"])
            raw = _read_source(ctx, document)
            text = _text(raw)
            regulatory = (
                issuer_source_policy(ctx.environ) == IssuerSourcePolicy.OFFICIAL_DISCLOSURES
                and urlsplit(document["url"]).hostname == REGULATOR_HOST
                and re.fullmatch(REGULATOR_DOCUMENT_PATH, urlsplit(document["url"]).path)
                is not None
            )
            # An exact-name regulatory issuer disclosure is joined to the already
            # verified issuer/company-number/ISIN chain above. Its omission of a
            # registry number does not undo that chain. Legacy CH mode retains
            # its existing per-document company-number requirement.
            if not (_contains(text, selection.legal_name)
                    and (regulatory or _contains(text, selection.company_number))):
                raise ValueError("ISSUER_BUSINESS_DOCUMENT_IDENTITY_REQUIRED")
            digest = hashlib.sha256(text.encode()).hexdigest()
            path = f"inputs/issuer-sources/documents/{digest}.txt"
            ctx.write_bytes(path, text.encode(), replace=False)
            if ctx.read_bytes(path) != text.encode():
                raise ValueError("ISSUER_SOURCE_TEXT_INTEGRITY_FAILED")
            result.append({
                "isin": selection.isin, "provider": "official-issuer", "dataset": "business-disclosure",
                "source_authority": "official-regulatory" if regulatory else "official-issuer",
                "evidence_file": path, "text_path": path, "text_sha256": digest,
                "raw_sha256": document["sha256"], "raw_path": document["path"],
                "source_url": document["url"], "published_at": None,
                "retrieved_at": document["observed_at"], "evidence_kind": "business_profile",
                "publication_time_established": False,
            })
        return result
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return []


def _capture_failure(error: Exception, *, target: str, url: str) -> dict[str, Any]:
    """Expose only allowlisted codes and transport facts, never exception text."""
    code = "SOURCE_CAPTURE_UNAVAILABLE"
    status = None
    retryable = False
    if isinstance(error, ProviderFailure):
        if error.code in _SAFE_CAPTURE_ERROR_CODES:
            code = error.code
        status = error.http_status
        retryable = error.retryable is True
    elif isinstance(error, SourceSecurityError):
        if str(error) in _SAFE_CAPTURE_ERROR_CODES:
            code = str(error)
    elif isinstance(error, OSError):
        code = "SOURCE_CAPTURE_IO_ERROR"
    return {
        "source_role": target, "source_url": url, "code": code,
        "http_status": status, "retryable": retryable,
    }


def prepare_issuer_evidence(
    ctx: QualificationContext,
    selection: IssuerSourceSelection,
    *,
    fetcher: SafeFetcher | None = None,
) -> dict[str, Any]:
    """Capture bounded public GETs and optional CH API bytes; never approve use."""
    policy = issuer_source_policy(ctx.environ)
    hosts = {selection.official_issuer_host, EXCHANGE_HOST, REGULATOR_HOST}
    if policy == IssuerSourcePolicy.COMPANIES_HOUSE:
        hosts.update({API_HOST, PUBLIC_HOST})
    hosts.update(str(urlsplit(url).hostname) for url in selection.linked_disclosure_urls)
    transport = fetcher or SafeFetcher(
        frozenset(hosts),
        maximum_bytes=1_900_000, timeout_seconds=15, maximum_redirects=0,
    )
    receipt: dict[str, Any] = {
        "version": ISSUER_SOURCE_VERSION, "selection": selection.model_dump(mode="json"),
        "issuer_source_policy": policy.value,
        "identity_sources": [], "business_sources": [], "link_sources": [], "failures": [],
        "rights_approved": False, "ethical_result": None, "financial_qualified": False,
    }
    cached: dict[str, dict[str, Any]] = {}
    previous = ctx.read_json(_receipt_path(selection.isin))
    # Sources are immutable bytes, not prior classifications. Reuse current exact
    # URL captures even when an operator explicitly changes the source route.
    if isinstance(previous, dict):
        for key in ("identity_sources", "business_sources", "security_source", "company_source", "filing_source", "terms_source", "link_sources"):
            refs = previous.get(key, [])
            for ref in refs if isinstance(refs, list) else [refs]:
                try:
                    _read_source(ctx, ref)
                    cached[ref["url"]] = ref
                except (ValueError, OSError, KeyError, TypeError):
                    continue
    tasks = [("link_sources", url) for url in sorted(set(selection.linked_disclosure_urls.values()))]
    tasks += [("identity_sources", url) for url in selection.issuer_identity_urls]
    tasks += [("security_source", selection.security_identity_url)]
    credential = (ctx.environ.get("COMPANIES_HOUSE_API_KEY")
                  if policy == IssuerSourcePolicy.COMPANIES_HOUSE else None)
    company_url = f"https://{API_HOST if credential else PUBLIC_HOST}/company/{selection.company_number}"
    if policy == IssuerSourcePolicy.COMPANIES_HOUSE:
        tasks += [("company_source", company_url)]
    tasks += [("business_sources", url) for url in selection.business_disclosure_urls]
    if selection.terms_url:
        tasks += [("terms_source", selection.terms_url)]
    if credential:
        tasks += [("filing_source", company_url + "/filing-history?category=accounts&items_per_page=4")]
    for target, url in tasks:
        try:
            _require_link(ctx, selection, receipt, url)
            authenticated = urlsplit(url).hostname == API_HOST
            headers = ({"Authorization": "Basic " + base64.b64encode((credential + ":").encode()).decode()}
                       if authenticated and credential else None)
            ref = cached.get(url) or _capture(ctx, transport, url, headers=headers, json_response=authenticated)
            if target in {"identity_sources", "business_sources", "link_sources"}:
                receipt[target].append(ref)
            else:
                receipt[target] = ref
        except (ProviderFailure, ValueError, OSError) as error:
            # URLs are prevalidated public paths, never authenticated query strings.
            receipt["failures"].append(_capture_failure(error, target=target, url=url))
    receipt["status"] = "CAPTURED" if not receipt["failures"] else "CAPTURE_INCOMPLETE"
    if isinstance(previous, dict) and previous != receipt:
        # Retain legacy requests/reviews as immutable audit material, not current
        # authority; switching policy never edits their hashes or observation times.
        ctx.artifact(previous)
    ctx.template(_selection_path(selection.isin), selection.model_dump(mode="json"))
    ctx.write_json(_receipt_path(selection.isin), receipt)
    ctx.write_json(f"outputs/issuer-sources/{selection.isin}.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    """Run explicit source acquisition locally with optional environment credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/qualified/local-inference"))
    parser.add_argument("--selection", required=True, help="Selection path relative to qualification root")
    args = parser.parse_args(argv)
    ctx = QualificationContext(args.root, Path.cwd(), dict(os.environ), utc_now())
    try:
        selection = IssuerSourceSelection.model_validate(ctx.read_json(args.selection))
        with ctx.locked():
            receipt = prepare_issuer_evidence(ctx, selection)
        print(json.dumps({"status": receipt["status"], "isin": selection.isin,
                          "failed_sources": len(receipt["failures"]),
                          "failures": receipt["failures"], "rights_approved": False}))
        return 0 if receipt["status"] == "CAPTURED" else 2
    except (ValueError, OSError, TypeError) as error:
        status = ("QUALIFICATION_ALREADY_RUNNING" if str(error) == "QUALIFICATION_ALREADY_RUNNING"
                  else "ISSUER_SOURCE_INPUT_INVALID")
        print(json.dumps({"status": status, "rights_approved": False}))
        return 2
