"""Read-only bulk identity joins and provider observations, never admission.

The provider's returned exchange/code is accepted only after an exact ISIN,
quote-unit and stock-type match with company/ticker corroboration. Trading 212
venue metadata is informational, not a prerequisite for this independent join.
Search-country is a venue attribute, not domicile.
API access, company descriptions and SIC codes never grant rights or ethics
approval. Cached bytes are rehashed before they can influence a join.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

from money.data.identifiers import InstrumentIdentifiers
from money.data.provider_probes import (
    ProviderAdmissionReview,
    ProviderProbeReport,
    QualificationArtifacts,
    probe_companies_house,
    probe_eodhd,
    qualify_probe,
)
from money.data.qualification import ProviderQualification
from money.data.security import ProviderFailure, SafeFetcher, SourceSecurityError, validate_url
from money.qualification.core import QualificationContext, fingerprint, json_bytes
from money.schemas.contracts import utc_now

_HOSTS = frozenset({"eodhd.com", "api.company-information.service.gov.uk"})
_STOCK_TYPES = frozenset({"stock", "common stock", "ordinary shares"})
_JOIN_ERRORS = frozenset(
    {
        "IDENTITY_INCOMPLETE",
        "EODHD_SEARCH_INCOMPLETE",
        "EODHD_SEARCH_INVALID",
        "EODHD_MAPPING_AMBIGUOUS",
        "EODHD_MAPPING_NOT_FOUND",
        "EODHD_FUNDAMENTALS_INVALID",
        "EODHD_FUNDAMENTALS_IDENTITY_MISMATCH",
        "COMPANIES_HOUSE_SEARCH_INVALID",
        "COMPANIES_HOUSE_SEARCH_INCOMPLETE",
        "COMPANIES_HOUSE_MAPPING_AMBIGUOUS",
        "COMPANIES_HOUSE_PROFILE_MISMATCH",
        "COMPANIES_HOUSE_ISSUER_CORROBORATION_REQUIRED",
    }
)


def _name(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _error(error: Exception) -> str:
    if isinstance(error, ProviderFailure):
        return (
            error.code
            if error.code
            in {
                "PROVIDER_UNAVAILABLE",
                "PROVIDER_TIMEOUT",
                "PROVIDER_REQUEST_BUDGET_EXHAUSTED",
                "PROVIDER_BACKOFF_ACTIVE",
                "PROVIDER_RESPONSE_INVALID",
                "PROVIDER_SOURCE_REJECTED",
            }
            else "PROVIDER_FAILED"
        )
    return (
        "PROVIDER_SOURCE_REJECTED"
        if isinstance(error, SourceSecurityError)
        else "PROVIDER_RESPONSE_INVALID"
    )


def _ref(digest: str, path: str) -> dict[str, str]:
    return {"sha256": digest, "path": path}


class _ContextArtifacts(QualificationArtifacts):
    """Use the existing probe contract with the stricter context secret scanner."""

    def __init__(self, ctx: QualificationContext) -> None:
        self.ctx = ctx
        super().__init__(ctx.root / "artifacts", secrets=())

    def save(self, value: Any) -> tuple[str, str]:
        digest, path = self.ctx.artifact(value)
        return digest, path.removeprefix("artifacts/")

    def save_bytes(
        self, raw: bytes, *, extension: Literal["json", "bin"] = "bin"
    ) -> tuple[str, str]:
        digest = hashlib.sha256(raw).hexdigest()
        path = f"artifacts/{digest}.{extension}"
        existing = self.ctx.read_bytes(path)
        if existing is not None and existing != raw:
            raise ValueError("QUALIFICATION_ARTIFACT_COLLISION")
        self.ctx.write_bytes(path, raw, replace=False)
        self.ctx.verify_artifact(digest, path)
        return digest, path.removeprefix("artifacts/")

    def read(self, digest: str | None, name: str | None) -> bytes:
        if not digest or name not in {digest + ".json", digest + ".bin"}:
            raise ValueError("PROVIDER_PROOF_INVALID")
        return self.ctx.verify_artifact(digest, "artifacts/" + name)


class _TransientArtifacts(QualificationArtifacts):
    """Acquisition-only normalization stays in memory, never admission evidence.

    The existing probes acquire all their real endpoint responses. Their initial
    normalization uses a request-start timestamp and is deliberately discarded;
    only a subsequent cache-only pass with a post-I/O boundary is persisted.
    """

    def __init__(self, ctx: QualificationContext) -> None:
        self.ctx = ctx
        self.memory: dict[str, bytes] = {}
        super().__init__(ctx.root / "artifacts", secrets=())

    def save(self, value: Any) -> tuple[str, str]:
        return self.save_bytes(json_bytes(value), extension="json")

    def save_bytes(
        self, raw: bytes, *, extension: Literal["json", "bin"] = "bin"
    ) -> tuple[str, str]:
        if not raw or len(raw) > 2_000_000:
            raise ValueError("QUALIFICATION_ARTIFACT_SIZE_INVALID")
        self.ctx.check_secrets(raw)
        digest = hashlib.sha256(raw).hexdigest()
        path = digest + "." + extension
        self.memory[path] = raw
        return digest, path


class _CachedFetcher(SafeFetcher):
    """Bounded GET-only transport; resumable successes and short negative cache."""

    def __init__(
        self,
        ctx: QualificationContext,
        fetcher: SafeFetcher | None,
        maximum: int,
        per_minute: int,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
        clock: Callable[[], datetime],
    ) -> None:
        super().__init__(_HOSTS, maximum_redirects=0)
        self.ctx = ctx
        self.delegate = fetcher or SafeFetcher(_HOSTS, maximum_redirects=0)
        self.maximum, self.interval = maximum, 60 / per_minute
        self.sleep, self.monotonic = sleep, monotonic
        self.clock = clock
        self.cache_only = False
        self.integrity_failed = False
        self.used = 0
        self.last_call: float | None = None
        self.refs: set[tuple[str, str]] = set()
        self.observations: list[datetime] = []
        self.backoff: set[str] = set()

    def refresh_clock(self) -> datetime:
        observed = self.clock()
        if observed.tzinfo is None or observed.utcoffset() is None or observed < self.ctx.now:
            raise ValueError("PROVIDER_CLOCK_INVALID")
        self.ctx.now = observed
        return observed

    def cached(self, name: str, key: str, maximum_age: int) -> dict[str, Any] | None:
        """Distinguish an ordinary cache miss from corrupted admitted bytes.

        QualificationContext deliberately treats a broken checkpoint as a miss.
        At this boundary retain an explicit security diagnostic, so a nested
        provider probe cannot reduce a corrupt proof to a generic API failure.
        """
        result = self.ctx.cache(name, key, maximum_age)
        if result is not None:
            return result
        try:
            entry = self.ctx.read_json("state/" + name + ".json")
            if not isinstance(entry, dict) or entry.get("input_fingerprint") != key:
                return None
            created = datetime.fromisoformat(entry["created_at"])
            if not created <= self.ctx.now < created + timedelta(seconds=maximum_age):
                return None
            for digest, path in entry["artifacts"]:
                self.ctx.verify_artifact(digest, path)
            if not isinstance(entry["value"], dict):
                raise ValueError("PROVIDER_CACHE_STRUCTURE_INVALID")
        except (ValueError, KeyError, TypeError, OSError):
            self.integrity_failed = True
            raise SourceSecurityError("PROVIDER_CACHE_INTEGRITY_FAILED") from None
        return None

    def json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        self.refresh_clock()
        host, _ = validate_url(url, _HOSTS)
        parts = urlsplit(url)
        # Even future callers of this private adapter cannot access account,
        # officer, transaction, portfolio or order endpoints.
        allowed = (
            r"/api/(?:exchanges-list/?|search/[A-Z0-9]{12}|"
            r"(?:fundamentals|eod|splits|div)/[A-Za-z0-9._-]{1,100}|news)"
            if host == "eodhd.com"
            else r"/(?:search/companies|company/[A-Z0-9]{8}(?:/filing-history)?)"
        )
        if re.fullmatch(allowed, parts.path) is None:
            raise SourceSecurityError("PROVIDER_READ_ONLY_ENDPOINT_REQUIRED")
        query = sorted([k, v] for k, v in parse_qsl(parts.query) if k != "api_token")
        identity = {"version": 2, "host": host, "path": parts.path, "query": query}
        key = fingerprint(identity)
        cache = self.cached("bulk-http-" + key, key, 86400)
        if cache:
            raw = self.ctx.verify_artifact(cache["sha256"], cache["path"])
            envelope = json.loads(raw)
            if envelope.get("request") == identity:
                observed = datetime.fromisoformat(envelope["observed_at"])
                self.refresh_clock()
                if observed <= self.ctx.now < observed + timedelta(days=1):
                    self.refs.add((cache["sha256"], cache["path"]))
                    self.observations.append(observed)
                    return envelope["response"]
        if self.cache_only:
            raise ProviderFailure("PROVIDER_CACHED_OBSERVATION_REQUIRED")
        failed = self.ctx.cache("bulk-failure-" + key, key, 300)
        if failed:
            raise ProviderFailure("PROVIDER_BACKOFF_ACTIVE")
        if host in self.backoff:
            raise ProviderFailure("PROVIDER_BACKOFF_ACTIVE")
        if self.used >= self.maximum:
            raise ProviderFailure("PROVIDER_REQUEST_BUDGET_EXHAUSTED")
        if self.last_call is not None:
            remaining = self.interval - (self.monotonic() - self.last_call)
            if remaining > 0:
                self.sleep(min(remaining, 60))
        self.last_call = self.monotonic()
        self.used += 1
        try:
            response = self.delegate.json(url, headers=headers)
            observed = self.refresh_clock()
            # All persisted content goes through encoded-secret detection.
            digest, path = self.ctx.artifact(
                {
                    "version": "money-bulk-provider-response-v2",
                    "request": identity,
                    "observed_at": observed.isoformat(),
                    "valid_until": (observed + timedelta(days=1)).isoformat(),
                    "response": response,
                }
            )
        except Exception as error:
            self.ctx.checkpoint("bulk-failure-" + key, key, {"error": _error(error)})
            if isinstance(error, ProviderFailure) and error.retryable:
                self.backoff.add(host)
            raise ProviderFailure(_error(error)) from None
        self.ctx.checkpoint("bulk-http-" + key, key, _ref(digest, path), [(digest, path)])
        self.refs.add((digest, path))
        self.observations.append(observed)
        return response


class BulkProviderEnricher:
    """Resume provider joins for each row without making any eligibility claim.

    A request budget limits each invocation; cached successful requests cost no
    budget. The same command resumes remaining rows. HTTP errors are never
    printed, because provider exception strings may contain credentials.
    """

    def __init__(
        self,
        ctx: QualificationContext,
        *,
        max_requests: int = 300,
        requests_per_minute: int = 30,
        fetcher: SafeFetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not 0 <= max_requests <= 100000 or not 1 <= requests_per_minute <= 60:
            raise ValueError("BULK_PROVIDER_BUDGET_INVALID")
        self.ctx = ctx
        self.fetcher = _CachedFetcher(
            ctx, fetcher, max_requests, requests_per_minute, sleep, monotonic, clock
        )
        self.artifacts = _ContextArtifacts(ctx)

    @property
    def requests_used(self) -> int:
        return self.fetcher.used

    def _eodhd(self, path: str, **query: str) -> Any:
        return self.fetcher.json(
            "https://eodhd.com/api/"
            + path
            + "?"
            + urlencode(dict(query, fmt="json", api_token=self.ctx.environ["EODHD_API_KEY"]))
        )

    def _company(self, path: str, **query: str) -> Any:
        credential = self.ctx.environ["COMPANIES_HOUSE_API_KEY"]
        authorization = "Basic " + base64.b64encode((credential + ":").encode()).decode()
        return self.fetcher.json(
            "https://api.company-information.service.gov.uk/"
            + path
            + ("?" + urlencode(query) if query else ""),
            headers={"Authorization": authorization},
        )

    def _mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        isin = row.get("isin", "")
        if not isinstance(isin, str) or not re.fullmatch(r"[A-Z0-9]{12}", isin):
            raise ValueError("IDENTITY_INCOMPLETE")
        calls_before, observations_before = self.fetcher.used, len(self.fetcher.observations)
        try:
            rows = self._eodhd("search/" + isin, type="stock", limit="100")
        finally:
            # A budget/backoff rejection is not a live lookup. Verified cached
            # responses count as resumed attempts, without claiming network I/O.
            row["eodhd_lookup_origin"] = (
                "NETWORK"
                if self.fetcher.used > calls_before
                else "CACHE"
                if len(self.fetcher.observations) > observations_before
                else "NOT_PERFORMED"
            )
            row["eodhd_mapping_attempted"] = row["eodhd_lookup_origin"] != "NOT_PERFORMED"
        if not isinstance(rows, list) or len(rows) >= 100:
            raise ValueError("EODHD_SEARCH_INCOMPLETE")
        if any(not isinstance(item, dict) for item in rows):
            raise ValueError("EODHD_SEARCH_INVALID")
        matches: dict[str, dict[str, Any]] = {}
        for item in rows:
            if (
                item.get("ISIN") != isin
                or item.get("Currency") != row["quote_currency"]
                or str(item.get("Type", "")).casefold() not in _STOCK_TYPES
                or not isinstance(item.get("Code"), str)
                or not isinstance(item.get("Exchange"), str)
            ):
                continue
            code, venue = item["Code"], item["Exchange"]
            if not (
                (_name(item.get("Name")) and _name(item.get("Name")) == _name(row.get("name")))
                or (code and code.upper() == str(row.get("short_ticker", "")).upper())
            ):
                continue
            # Both components were actually returned. This is not suffix inference.
            symbol = code + "." + venue
            if re.fullmatch(r"[A-Za-z0-9_-]{1,25}\.[A-Za-z0-9_-]{1,20}", symbol):
                previous = matches.get(symbol)
                if previous is not None and previous != item:
                    raise ValueError("EODHD_MAPPING_AMBIGUOUS")
                matches[symbol] = item
        if len(matches) != 1:
            raise ValueError("EODHD_MAPPING_AMBIGUOUS" if matches else "EODHD_MAPPING_NOT_FOUND")
        symbol, item = next(iter(matches.items()))
        row["eodhd_symbol"], row["eodhd_mapping_state"] = symbol, "MAPPED"
        row["eodhd_identity"] = item
        return item

    def _general(self, row: dict[str, Any]) -> dict[str, Any]:
        general = self._eodhd("fundamentals/" + row["eodhd_symbol"], filter="General")
        if not isinstance(general, dict):
            raise ValueError("EODHD_FUNDAMENTALS_INVALID")
        if isinstance(general.get("General"), dict):
            general = general["General"]
        identity = row["eodhd_identity"]
        if (
            general.get("ISIN") != row["isin"]
            or general.get("Code") != identity["Code"]
            or general.get("CurrencyCode") != row["quote_currency"]
            or str(general.get("Type", "")).casefold() not in _STOCK_TYPES
            or _name(general.get("Name")) != _name(identity["Name"])
            or general.get("IsDelisted") is not False
        ):
            raise ValueError("EODHD_FUNDAMENTALS_IDENTITY_MISMATCH")
        allowed = {
            "Name",
            "Code",
            "ISIN",
            "Type",
            "CurrencyCode",
            "CountryName",
            "CountryISO",
            "Description",
            "Sector",
            "Industry",
            "GicSector",
            "GicIndustry",
            "AddressData",
            "WebURL",
            "UpdatedAt",
            "IsDelisted",
        }
        facts = {key: general[key] for key in allowed & general.keys()}
        row["issuer_facts"] = facts
        row["issuer_facts_source"] = "EODHD fundamentals General (unreviewed source observations)"
        return facts

    @staticmethod
    def _address_agrees(general: dict[str, Any], profile: dict[str, Any]) -> bool:
        address = general.get("AddressData")
        registered = profile.get("registered_office_address")
        if not isinstance(address, dict) or not isinstance(registered, dict):
            return False
        # Company addresses can differ; only an actual exact match corroborates.
        postal = _name(address.get("ZIP"))
        street = _name(address.get("Street"))
        return bool(
            postal
            and street
            and postal == _name(registered.get("postal_code"))
            and (
                street == _name(registered.get("address_line_1"))
                or street
                == _name(
                    str(registered.get("premises", ""))
                    + " "
                    + str(registered.get("address_line_1", ""))
                )
            )
        )

    def _companies_house(self, row: dict[str, Any], general: dict[str, Any]) -> None:
        country = general.get("CountryISO")
        if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) and country != "GB":
            row["companies_house_state"] = "NOT_APPLICABLE"
            row["companies_house_applicability_reason"] = (
                "EODHD General identifies issuer registration outside GB; no UK company number inferred."
            )
            return
        if country != "GB":
            row["companies_house_state"] = "UNRESOLVED_APPLICABILITY"
            row["provider_reasons"].append("ISSUER_REGISTRATION_COUNTRY_UNVERIFIED")
            return
        if not self.ctx.environ.get("COMPANIES_HOUSE_API_KEY"):
            row["provider_reasons"].append("COMPANIES_HOUSE_CREDENTIAL_MISSING")
            return
        name = general["Name"]
        result = self._company("search/companies", q=name, items_per_page="100", start_index="0")
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise ValueError("COMPANIES_HOUSE_SEARCH_INVALID")
        items = result["items"]
        if len(items) >= 100 or int(result.get("total_results", len(items))) > len(items):
            raise ValueError("COMPANIES_HOUSE_SEARCH_INCOMPLETE")
        if any(not isinstance(item, dict) for item in items):
            raise ValueError("COMPANIES_HOUSE_SEARCH_INVALID")
        candidates = [
            item
            for item in items
            if _name(item.get("title")) == _name(name)
            and item.get("company_status") == "active"
            and item.get("company_type") == "plc"
            and re.fullmatch(r"[A-Z0-9]{8}", str(item.get("company_number", "")))
        ]
        row["companies_house_candidates"] = [
            {
                key: item.get(key)
                for key in ("title", "company_number", "company_status", "company_type")
            }
            for item in candidates
        ]
        if len(candidates) != 1:
            raise ValueError("COMPANIES_HOUSE_MAPPING_AMBIGUOUS")
        number = candidates[0]["company_number"]
        profile = self._company("company/" + number)
        if (
            not isinstance(profile, dict)
            or profile.get("company_number") != number
            or _name(profile.get("company_name")) != _name(name)
            or profile.get("company_status") != "active"
            or profile.get("type") != "plc"
        ):
            raise ValueError("COMPANIES_HOUSE_PROFILE_MISMATCH")
        if not self._address_agrees(general, profile):
            raise ValueError("COMPANIES_HOUSE_ISSUER_CORROBORATION_REQUIRED")
        row["companies_house_number"], row["companies_house_state"] = number, "MAPPED"
        row["legal_company_name"] = profile["company_name"]
        row["company_status"] = profile["company_status"]

    def _identifiers(self, row: dict[str, Any]) -> InstrumentIdentifiers:
        observed = min(self.fetcher.observations, default=self.ctx.now)
        return InstrumentIdentifiers(
            ticker=row.get("ticker") or row["short_ticker"].upper(),
            company_name=row.get("legal_company_name") or row["eodhd_identity"]["Name"],
            trading212_id=row["trading212_id"],
            exchange_ticker=row["eodhd_identity"]["Code"],
            exchange=row["eodhd_identity"]["Exchange"],
            isin=row["isin"],
            quote_currency=row["quote_currency"],
            companies_house_number=row.get("companies_house_number"),
            provider_symbols=(("eodhd", row["eodhd_symbol"]),),
            verified_at=observed,
            valid_until=observed + timedelta(days=1),
            source=(
                "Trading212 metadata + exact EODHD ISIN/currency/stock-type join with "
                "company/ticker corroboration and returned Code/Exchange; no eligibility approval"
            ),
        )

    def _probe(self, provider: str, identifiers: InstrumentIdentifiers) -> ProviderProbeReport:
        self.fetcher.refresh_clock()
        identity = identifiers.model_dump(mode="json", exclude={"verified_at", "valid_until"})
        key = fingerprint({"version": 2, "provider": provider, "identifiers": identity})
        cached = self.fetcher.cached("bulk-probe-" + key, key, 86400)
        if cached:
            report = ProviderProbeReport.model_validate_json(
                self.ctx.verify_artifact(cached["sha256"], cached["path"])
            )
            if (
                report.provider == provider
                and len(report.samples) == 1
                and report.samples[0].model_dump(
                    mode="json", exclude={"verified_at", "valid_until"}
                )
                == identity
                and report.retrieved_at <= self.ctx.now < report.retrieved_at + timedelta(days=1)
            ):
                for sample in report.samples:
                    sample.require_current(self.ctx.now)
                for reference in cached.get("response_refs", []):
                    self.ctx.verify_artifact(reference["sha256"], reference["path"])
                    self.fetcher.refs.add((reference["sha256"], reference["path"]))
                return report
        probe = probe_eodhd if provider == "eodhd" else probe_companies_house
        credential = self.ctx.environ[
            "EODHD_API_KEY" if provider == "eodhd" else "COMPANIES_HOUSE_API_KEY"
        ]
        # First acquire endpoints using the existing normalizers, but do not
        # publish their provisional request-start timestamps or proof bytes.
        acquired = probe(
            credential,
            (identifiers,),
            self.ctx.now,
            _TransientArtifacts(self.ctx),
            fetcher=self.fetcher,
        )
        observed = self.fetcher.refresh_clock()
        expiry = min(
            identifiers.valid_until,
            min(self.fetcher.observations, default=observed) + timedelta(days=1),
        )
        identifiers = InstrumentIdentifiers.model_validate(
            identifiers.model_dump() | {"valid_until": expiry}
        )
        identifiers.require_current(observed)
        # Every source response already exists before this conservative
        # availability boundary. Midnight/window changes may miss the cache;
        # that remains FAILED until a later run, never a hidden second request.
        self.fetcher.cache_only = True
        try:
            report = probe(
                credential, (identifiers,), observed, self.artifacts, fetcher=self.fetcher
            )
        finally:
            self.fetcher.cache_only = False
        failures = {
            item.dataset: item.error_code for item in acquired.datasets if item.status == "FAILED"
        }
        report = report.model_copy(
            update={
                "datasets": tuple(
                    item.model_copy(update={"error_code": failures[item.dataset]})
                    if item.status == "FAILED" and failures.get(item.dataset)
                    else item
                    for item in report.datasets
                )
            }
        )
        digest, path = self.ctx.artifact(report.model_dump(mode="json"))
        refs = [(digest, path)] + [
            (item.artifact_hash, "artifacts/" + item.artifact_path)
            for item in report.datasets
            if item.artifact_hash and item.artifact_path
        ]
        # A failure must be retried; EMPTY is genuine evidence and may be cached
        # but never promoted into a successful dataset observation.
        if all(item.status != "FAILED" for item in report.datasets):
            response_refs = sorted(self.fetcher.refs)
            self.ctx.checkpoint(
                "bulk-probe-" + key,
                key,
                {
                    **_ref(digest, path),
                    "response_refs": [_ref(digest, path) for digest, path in response_refs],
                },
                refs + response_refs,
            )
        return report

    def _attach_probe(self, row: dict[str, Any], report: ProviderProbeReport) -> None:
        digest, path = self.ctx.artifact(report.model_dump(mode="json"))
        row["provider_reports"][report.provider] = {
            "report": report.model_dump(mode="json"),
            "report_ref": _ref(digest, path),
        }
        self.fetcher.refs.add((digest, path))
        for item in report.datasets:
            dataset = item.model_dump(mode="json")
            dataset["rights_verified"] = False
            dataset["production_qualified"] = False
            row["provider_datasets"][report.provider + ":" + item.dataset] = dataset
            if item.artifact_hash and item.artifact_path:
                self.ctx.verify_artifact(item.artifact_hash, "artifacts/" + item.artifact_path)
                self.fetcher.refs.add((item.artifact_hash, "artifacts/" + item.artifact_path))

    def _accounts(self, row: dict[str, Any]) -> None:
        number = row["companies_house_number"]
        response = self._company(
            "company/" + number + "/filing-history", category="accounts", items_per_page="100"
        )
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            raise ValueError("COMPANIES_HOUSE_ACCOUNTS_INVALID")
        accounts = []
        for item in response["items"]:
            if not isinstance(item, dict) or item.get("category") != "accounts":
                continue
            identifier = item.get("transaction_id")
            if not isinstance(identifier, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}", identifier
            ):
                continue
            accounts.append(
                {
                    key: item[key]
                    for key in (
                        "transaction_id",
                        "date",
                        "type",
                        "category",
                        "description",
                        "description_values",
                        "links",
                    )
                    if key in item
                }
            )
        row["recent_accounts_filings"] = accounts[:4]
        row["financial_documents_verified"] = False

    def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
        """Return observations for one row; malformed peers cannot stop the batch."""
        row = dict(source)
        for field in (
            "identifiers",
            "eodhd_identity",
            "legal_company_name",
            "company_status",
            "issuer_facts_source",
            "companies_house_applicability_reason",
            "companies_house_candidates",
            "provider_evidence_observed_at",
            "provider_evidence_valid_until",
        ):
            row.pop(field, None)
        row.update(
            eodhd_symbol=None,
            eodhd_mapping_state="UNRESOLVED",
            eodhd_mapping_attempted=False,
            eodhd_lookup_origin="NOT_PERFORMED",
            companies_house_number=None,
            companies_house_state="UNRESOLVED",
            provider_reasons=[],
            provider_evidence=[],
            provider_reports={},
            provider_datasets={},
            issuer_facts={},
            recent_accounts_filings=[],
            provider_rights_verified=False,
            financial_documents_verified=False,
        )
        self.fetcher.refs = set()
        self.fetcher.observations = []
        self.fetcher.integrity_failed = False
        if not self.ctx.environ.get("EODHD_API_KEY"):
            row["provider_reasons"].append("EODHD_CREDENTIAL_MISSING")
            return row
        try:
            self._mapping(row)
        except Exception as error:
            code = (
                str(error)
                if isinstance(error, ValueError) and str(error) in _JOIN_ERRORS
                else _error(error)
            )
            row["provider_reasons"].append(code)
        if row["eodhd_mapping_state"] == "MAPPED":
            try:
                general = self._general(row)
                self._companies_house(row, general)
            except Exception as error:
                code = (
                    str(error)
                    if isinstance(error, ValueError) and str(error) in _JOIN_ERRORS
                    else _error(error)
                )
                row["provider_reasons"].append(code)
            try:
                identifiers = self._identifiers(row)
                identifiers.require_current(self.ctx.now)
                market_report = self._probe("eodhd", identifiers)
                identifiers = market_report.samples[0]
                self._attach_probe(row, market_report)
                if row["companies_house_state"] == "MAPPED":
                    company_report = self._probe("companies-house", identifiers)
                    identifiers = company_report.samples[0]
                    self._attach_probe(row, company_report)
                    try:
                        self._accounts(row)
                    except Exception as error:
                        row["provider_reasons"].append(_error(error))
                row["identifiers"] = identifiers.model_dump(mode="json")
            except Exception as error:
                row["provider_reasons"].append(_error(error))
        if self.fetcher.integrity_failed:
            # Preserve only objective observations and the explicit failure.
            # No partially successful report may be mistaken for admission
            # when another referenced cache artifact failed integrity checks.
            row.pop("identifiers", None)
            row.update(
                eodhd_symbol=None,
                eodhd_mapping_state="UNRESOLVED",
                provider_reports={},
                provider_datasets={},
            )
            row["provider_reasons"].append("PROVIDER_CACHE_INTEGRITY_FAILED")
        row["provider_evidence"] = [
            _ref(digest, path) for digest, path in sorted(self.fetcher.refs)
        ]
        row["provider_evidence_observed_at"] = min(
            self.fetcher.observations, default=self.ctx.now
        ).isoformat()
        row["provider_evidence_valid_until"] = (
            min(self.fetcher.observations, default=self.ctx.now) + timedelta(days=1)
        ).isoformat()
        # Output is observations only; the caller retains every rights, ethics,
        # ISA, review and production gate before changing qualification_state.
        self.ctx.check_secrets(json.dumps(row, allow_nan=False).encode())
        return row


def qualify_bulk_provider_reports(
    ctx: QualificationContext,
    provider: str,
    rows: list[dict[str, Any]],
    rights: dict[str, Any],
) -> ProviderQualification | None:
    """Union only individually admitted coverage without rewriting probe times.

    ``qualify_probe`` revalidates each original dataset artifact and the explicit
    rights review. A failing stock cannot expand coverage or prevent qualified
    peers being admitted. The aggregate proof links those real qualifications;
    it is deliberately not a forged multi-sample ``ProviderProbeReport``.
    """
    if provider not in {"eodhd", "companies-house"} or rights.get("status") != "REVIEWED":
        return None
    try:
        evidence_path = rights["rights_evidence_file"]
        if not isinstance(evidence_path, str) or not evidence_path.startswith("inputs/"):
            return None
        evidence = ctx.read_bytes(evidence_path)
        if evidence is None or not evidence.strip() or evidence.strip() in {b"{}", b"[]", b"null"}:
            return None
        rights_ref = ctx.artifact(evidence)
        review = ProviderAdmissionReview.model_validate(
            {**rights["review"], "rights_evidence_hash": rights_ref[0]}
        )
        if review.provider != provider:
            return None
        artifacts = _ContextArtifacts(ctx)
        admissions: list[ProviderQualification] = []
        identifier_expiries: list[datetime] = []
        links: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            try:
                selected = row.get("provider_reports", {}).get(provider)
                if not selected:
                    continue
                reference = selected["report_ref"]
                if reference["sha256"] in seen:
                    continue
                report = ProviderProbeReport.model_validate_json(
                    ctx.verify_artifact(reference["sha256"], reference["path"])
                )
                if report.provider != provider or len(report.samples) != 1:
                    continue
                sample = report.samples[0]
                identity = row.get("eodhd_identity")
                if not isinstance(identity, dict):
                    continue
                if (
                    sample.trading212_id != row.get("trading212_id")
                    or sample.isin != row.get("isin")
                    or sample.quote_currency != row.get("quote_currency")
                    or sample.exchange != identity.get("Exchange")
                    or sample.exchange_ticker != identity.get("Code")
                ):
                    continue
                if provider == "eodhd" and sample.symbol_for("eodhd", ctx.now) != row.get(
                    "eodhd_symbol"
                ):
                    continue
                if provider == "companies-house" and (
                    row.get("companies_house_state") != "MAPPED"
                    or sample.companies_house_number != row.get("companies_house_number")
                ):
                    continue
                qualification = qualify_probe(
                    report, review, evidence, artifacts, clock=lambda: ctx.now
                )
                qualified_ref = ctx.artifact(qualification.model_dump(mode="json"))
                admissions.append(qualification)
                identifier_expiries.append(sample.valid_until)
                links.append(
                    {
                        "qualification": _ref(*qualified_ref),
                        "original_probe": _ref(reference["sha256"], reference["path"]),
                    }
                )
                seen.add(reference["sha256"])
            except (ValueError, OSError, KeyError, TypeError):
                continue
        if not admissions:
            return None
        # All admitted samples have the same fixed provider datasets and the
        # existing probe schema's GB/STOCK/AS_RETRIEVED scope. Assert this before
        # creating a union, to avoid accidental cross-product overclaim later.
        first = admissions[0]
        if any(
            (item.datasets, item.geography, item.instrument_types, item.publication_times)
            != (first.datasets, first.geography, first.instrument_types, first.publication_times)
            for item in admissions
        ):
            return None
        proof = ctx.artifact(
            {
                "version": "money-bulk-provider-admission-v1",
                "provider": provider,
                "policy": "union-of-individually-qualified-currencies; earliest-expiry; original-probe-times-preserved",
                "rights_evidence": _ref(*rights_ref),
                "individual_qualifications": links,
                "historical_publication_verified": False,
                "financial_documents_verified": False,
            }
        )
        aggregate = ProviderQualification.model_validate(
            first.model_dump()
            | {
                "currencies": tuple(
                    sorted({currency for item in admissions for currency in item.currencies})
                ),
                "earliest_observation": min(item.earliest_observation for item in admissions),
                "verified_at": min(item.verified_at for item in admissions),
                "valid_until": min(
                    *(item.valid_until for item in admissions), *identifier_expiries
                ),
                "maximum_age_seconds": min(item.maximum_age_seconds for item in admissions),
                "qualification_report_hash": proof[0],
            }
        )
        for dataset in aggregate.datasets:
            aggregate.require(dataset, ctx.now)
        ctx.artifact(aggregate.model_dump(mode="json"))
        return aggregate
    except (ValueError, OSError, KeyError, TypeError):
        return None
