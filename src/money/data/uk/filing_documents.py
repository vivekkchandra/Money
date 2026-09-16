"""Authoritative filing documents, with explicit representation and retrieval-time PIT.

The Document API's authenticated content endpoint returns a signed location.
Only individually reviewed storage hosts may receive a separate unauthenticated
download. Signed URLs and raw document bytes never enter serialized evidence.
Run conversion inside the supervised compute worker, not a web request.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import socket
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any, Literal, Protocol
from urllib.parse import urljoin
from zipfile import ZIP_STORED, ZipFile

from pydantic import AwareDatetime, Field, field_validator, model_validator

from money.adapters.native_attestation import SINGLE_MODULE_SOURCE_DIGESTS
from money.data.identifiers import InstrumentIdentifiers
from money.data.qualification import ProviderQualification
from money.data.security import (
    FetchResult,
    ProviderFailure,
    SafeFetcher,
    SourceSecurityError,
    _PinnedHTTPS,
    bounded_zip_members,
    public_addresses,
    validate_url,
)
from money.data.uk.xbrl import parse_company_archive
from money.schemas.contracts import Contract, EvidenceRecord, FinancialFact, utc_now

API_HOST = "api.company-information.service.gov.uk"
DOCUMENT_HOST = "document-api.company-information.service.gov.uk"
MACHINE_TYPES = ("application/xhtml+xml", "application/xml", "application/zip")
IDENTIFIER = r"[A-Za-z0-9_-]{1,128}"


class ReviewedStorageHost(Contract):
    host: str = Field(max_length=253)
    review_evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("host")
    @classmethod
    def exact_host(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
            raise ValueError("FILING_STORAGE_HOST_INVALID")
        return value


class FinancialCurrencyProof(Contract):
    """Reviewed original accounting units; quotation currency is not sufficient."""

    company_number: str = Field(pattern=r"^[A-Z0-9]{8}$")
    filing_id: str = Field(pattern=rf"^{IDENTIFIER}$")
    currency: Literal["GBP"]
    document_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class FilingDocumentBundle(Contract):
    company_number: str
    filing_id: str
    document_id: str
    filing_date: date
    metadata_url: str
    mime_type: str
    content_length: int
    content_hash: str
    metadata_hash: str
    filing_index_hash: str
    document_created_at: AwareDatetime | None
    document_updated_at: AwareDatetime | None
    representation_created_at: AwareDatetime | None
    representation_updated_at: AwareDatetime | None
    retrieval_time: AwareDatetime
    availability_time: AwareDatetime
    original_publication_time: None = None
    point_in_time_status: Literal["AS_RETRIEVED"] = "AS_RETRIEVED"
    currency_evidence_hash: str
    conversion_adapter_version: Literal["money-companies-house-xbrl-v1"] = (
        "money-companies-house-xbrl-v1"
    )
    conversion_parser: Literal["stream-read-xbrl"] = "stream-read-xbrl"
    # Declared lock target, not a claim of independently attested installed code.
    conversion_expected_upstream_sha: Literal["b95b48bbf50727648cebcba56634b17dc9e60ad3"] = (
        "b95b48bbf50727648cebcba56634b17dc9e60ad3"
    )
    conversion_source_attestation: Literal["PINNED_SOURCE_VERIFIED", "INJECTED_UNATTESTED"]
    conversion_source_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    storage_host: str
    storage_host_review_hash: str | None
    licensing_status: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    raw_redistribution_allowed: Literal[False] = False
    attribution: str = "Companies House filing; company-supplied accounts"
    evidence: tuple[EvidenceRecord, ...]
    raw_document: bytes = Field(exclude=True, repr=False)

    @model_validator(mode="after")
    def conversion_attestation_consistency(self) -> FilingDocumentBundle:
        expected = (
            SINGLE_MODULE_SOURCE_DIGESTS["stream_read_xbrl"]
            if self.conversion_source_attestation == "PINNED_SOURCE_VERIFIED"
            else None
        )
        if self.conversion_source_hash != expected:
            raise ValueError("FILING_CONVERSION_ATTESTATION_MISMATCH")
        return self


class FilingTransport(Protocol):
    def metadata(self, url: str) -> bytes: ...

    def content(self, url: str, mime: str) -> tuple[FetchResult, str]: ...


class CompaniesHouseDocumentTransport:
    """One authenticated location lookup, then a separately bounded content GET."""

    def __init__(
        self,
        api_key: str,
        storage_hosts: tuple[ReviewedStorageHost, ...] = (),
        *,
        maximum_bytes: int = 5_000_000,
        timeout_seconds: float = 20,
    ) -> None:
        if not api_key or len(api_key) > 512 or ":" in api_key or not api_key.isascii():
            raise ValueError("COMPANIES_HOUSE_CREDENTIAL_INVALID")
        if any(ord(character) < 33 or ord(character) > 126 for character in api_key):
            raise ValueError("COMPANIES_HOUSE_CREDENTIAL_INVALID")
        if len({item.host for item in storage_hosts}) != len(storage_hosts):
            raise ValueError("FILING_STORAGE_HOST_DUPLICATE")
        self._authorization = "Basic " + base64.b64encode(f"{api_key}:".encode()).decode()
        self.storage_hosts = frozenset(item.host for item in storage_hosts)
        self.timeout_seconds = timeout_seconds
        self.maximum_bytes = maximum_bytes
        # Validation is shared with the canonical bounded transport.
        self._metadata = SafeFetcher(
            frozenset({API_HOST, DOCUMENT_HOST}),
            maximum_bytes=500_000,
            timeout_seconds=timeout_seconds,
            maximum_redirects=0,
        )
        SafeFetcher(
            frozenset({DOCUMENT_HOST}),
            maximum_bytes=maximum_bytes,
            timeout_seconds=timeout_seconds,
            maximum_redirects=0,
        )

    def metadata(self, url: str) -> bytes:
        validate_url(url, frozenset({API_HOST, DOCUMENT_HOST}))
        return self._metadata.get(
            url, headers={"Authorization": self._authorization, "Accept": "application/json"}
        ).content

    def _location(self, url: str, mime: str) -> str:
        host, target = validate_url(url, frozenset({DOCUMENT_HOST}))
        if not re.fullmatch(rf"/document/{IDENTIFIER}/content", target):
            raise SourceSecurityError("FILING_CONTENT_URL_INVALID")
        deadline = time.monotonic() + self.timeout_seconds
        address = public_addresses(host, min(self.timeout_seconds, 5))[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True)
        connection = _PinnedHTTPS(host, address, min(5, remaining))
        timer = None
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Authorization": self._authorization,
                    "Accept": mime,
                    "Accept-Encoding": "identity",
                    "User-Agent": "Money-research/0.2",
                },
            )
            assert connection.sock is not None
            stream_socket = connection.sock
            stream_socket.settimeout(max(0.001, deadline - time.monotonic()))

            def interrupt() -> None:
                try:
                    stream_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = threading.Timer(max(0.001, deadline - time.monotonic()), interrupt)
            timer.daemon = True
            timer.start()
            response = connection.getresponse()
            if time.monotonic() >= deadline:
                raise TimeoutError
            if response.status == 200:
                # Re-request direct content using the full shared body/MIME/size protections.
                return url
            if response.status != 302:
                raise ProviderFailure(
                    "FILING_DOCUMENT_UNAVAILABLE",
                    retryable=response.status == 429 or response.status >= 500,
                )
            location = response.getheader("Location")
            if not location:
                raise SourceSecurityError("FILING_LOCATION_MISSING")
            redirected = urljoin(url, location)
            validate_url(redirected, self.storage_hosts | {DOCUMENT_HOST})
            return redirected
        except TimeoutError as error:
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True) from error
        except (OSError, http.client.HTTPException) as error:
            code = "PROVIDER_TIMEOUT" if time.monotonic() >= deadline else "PROVIDER_UNAVAILABLE"
            raise ProviderFailure(code, retryable=True) from error
        finally:
            if timer:
                timer.cancel()
            connection.close()

    def content(self, url: str, mime: str) -> tuple[FetchResult, str]:
        if mime not in MACHINE_TYPES:
            raise SourceSecurityError("FILING_MIME_UNSUPPORTED")
        location = self._location(url, mime)
        host, _ = validate_url(location, self.storage_hosts | {DOCUMENT_HOST})
        headers = {"Accept": mime}
        if host == DOCUMENT_HOST:
            headers["Authorization"] = self._authorization
        # A storage redirect cannot cause a second onward redirect or acquire credentials.
        fetcher = SafeFetcher(
            frozenset({host}),
            maximum_bytes=self.maximum_bytes,
            timeout_seconds=self.timeout_seconds,
            maximum_redirects=0,
        )
        return fetcher.get(location, headers=headers, mime_types=(mime,)), host


def _json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(items)
        if len(result) != len(items):
            raise SourceSecurityError("FILING_DUPLICATE_JSON_KEY")
        return result

    def invalid_constant(_: str) -> None:
        raise SourceSecurityError("FILING_NONFINITE_JSON")

    if len(raw) > 500_000:
        raise SourceSecurityError("FILING_METADATA_SIZE_LIMIT")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise SourceSecurityError("FILING_METADATA_INVALID") from error
    if not isinstance(value, dict):
        raise SourceSecurityError("FILING_METADATA_INVALID")
    return value


def _timestamp(value: Any, retrieved_at: datetime) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("FILING_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("FILING_TIMESTAMP_INVALID") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed > retrieved_at:
        raise ValueError("PIT_VIOLATION")
    return parsed


def _archive(content: bytes, mime: str) -> bytes:
    if not content or len(content) > 5_000_000:
        raise SourceSecurityError("FILING_CONTENT_SIZE_LIMIT")
    if mime == "application/zip":
        members = bounded_zip_members(content)
        archive = content
    else:
        members = (("filing.xhtml" if mime == "application/xhtml+xml" else "filing.xml", content),)
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_STORED) as bundle:
            bundle.writestr(members[0][0], content)
        archive = buffer.getvalue()
    if not members:
        raise ValueError("FILING_EMPTY_DOCUMENT")
    for name, data in members:
        if not name.lower().endswith((".xml", ".xhtml", ".html", ".htm")):
            raise SourceSecurityError("XBRL_MEMBER_TYPE_DENIED")
        upper = data.upper().replace(b"\x00", b"")
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise SourceSecurityError("XBRL_EXTERNAL_ENTITY_DENIED")
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeError as error:
            raise SourceSecurityError("XBRL_ENCODING_DENIED") from error
        declaration = re.match(r"\s*<\?xml\b(.*?)\?>", decoded, re.DOTALL)
        if declaration:
            encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration[1])
            if encoding and encoding[1].lower() not in {"utf-8", "utf8"}:
                raise SourceSecurityError("XBRL_ENCODING_DENIED")
    return archive


class CompaniesHouseFilingDocuments:
    def __init__(
        self,
        api_key: str,
        qualification: ProviderQualification,
        *,
        storage_hosts: tuple[ReviewedStorageHost, ...] = (),
        transport: FilingTransport | None = None,
        parser: Callable[..., tuple[EvidenceRecord, ...]] = parse_company_archive,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if qualification.provider != "companies-house":
            raise ValueError("PROVIDER_QUALIFICATION_MISMATCH")
        if len({item.host for item in storage_hosts}) != len(storage_hosts):
            raise ValueError("FILING_STORAGE_HOST_DUPLICATE")
        self.qualification = qualification
        self.storage_hosts = {item.host: item.review_evidence_hash for item in storage_hosts}
        self.transport = transport or CompaniesHouseDocumentTransport(api_key, storage_hosts)
        self.parser, self.clock = parser, clock

    def fetch(
        self,
        identifiers: InstrumentIdentifiers,
        filing_id: str,
        snapshot_id: str,
        currency_proof: FinancialCurrencyProof,
    ) -> FilingDocumentBundle:
        start = self.clock()
        identifiers.require_current(start)
        self.qualification.require("filing", start)
        self.qualification.require("financial", start)
        number = identifiers.companies_house_number
        if not number or not re.fullmatch(IDENTIFIER, filing_id):
            raise ValueError("FILING_IDENTIFIER_INVALID")
        if currency_proof.company_number != number or currency_proof.filing_id != filing_id:
            raise ValueError("FILING_CURRENCY_PROOF_MISMATCH")
        filing_url = f"https://{API_HOST}/company/{number}/filing-history/{filing_id}"
        raw_filing = self.transport.metadata(filing_url)
        filing = _json(raw_filing)
        if filing.get("transaction_id") != filing_id or filing.get("category") != "accounts":
            raise ValueError("FILING_IDENTITY_OR_CATEGORY_MISMATCH")
        links = filing.get("links")
        link = links.get("document_metadata") if isinstance(links, dict) else None
        if not isinstance(link, str):
            raise ValueError("FILING_DOCUMENT_LINK_MISSING")
        _, path = validate_url(link, frozenset({DOCUMENT_HOST}))
        match = re.fullmatch(rf"/document/({IDENTIFIER})", path)
        if not match:
            raise SourceSecurityError("FILING_METADATA_URL_INVALID")
        document_id = match[1]
        raw_metadata = self.transport.metadata(link)
        metadata = _json(raw_metadata)
        if (
            metadata.get("id", document_id) != document_id
            or metadata.get("company_number", number) != number
        ):
            raise ValueError("FILING_DOCUMENT_IDENTITY_MISMATCH")
        resources = metadata.get("resources")
        if not isinstance(resources, dict):
            raise ValueError("FILING_MACHINE_READABLE_UNAVAILABLE")
        mime = next((kind for kind in MACHINE_TYPES if kind in resources), None)
        if mime is None:
            raise ValueError("FILING_MACHINE_READABLE_UNAVAILABLE")
        resource = resources[mime]
        length = resource.get("content_length") if isinstance(resource, dict) else None
        if type(length) is not int or not 0 < length <= 5_000_000:
            raise SourceSecurityError("FILING_DECLARED_SIZE_INVALID")
        result, storage_host = self.transport.content(link + "/content", mime)
        if storage_host != DOCUMENT_HOST and storage_host not in self.storage_hosts:
            raise SourceSecurityError("FILING_STORAGE_HOST_UNQUALIFIED")
        if result.mime != mime or len(result.content) != length:
            raise SourceSecurityError("FILING_REPRESENTATION_MISMATCH")
        retrieved_at = self.clock()
        identifiers.require_current(retrieved_at)
        self.qualification.require("filing", retrieved_at)
        self.qualification.require("financial", retrieved_at)
        raw_date = filing.get("date")
        if not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            raise ValueError("FILING_DATE_INVALID")
        try:
            filing_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError("FILING_DATE_INVALID") from error
        if filing_date > retrieved_at.date() or retrieved_at < start:
            raise ValueError("PIT_VIOLATION")
        dates = tuple(
            _timestamp(item, retrieved_at)
            for item in (
                metadata.get("created_at"),
                metadata.get("updated_at"),
                resource.get("created_at"),
                resource.get("updated_at"),
            )
        )
        digest = hashlib.sha256(result.content).hexdigest()
        if currency_proof.document_content_hash != digest:
            # A revised/replaced representation cannot inherit an earlier units review.
            raise ValueError("FILING_CURRENCY_PROOF_CONTENT_MISMATCH")
        source_id = f"{number}:{filing_id}:{document_id}:{digest}"
        try:
            records = self.parser(
                _archive(result.content, mime),
                company_number=number,
                source_id=source_id,
                snapshot_id=snapshot_id,
                publication_time=retrieved_at,
                retrieved_at=retrieved_at,
                verified_currency="GBP",
            )
        except ModuleNotFoundError as error:
            raise ValueError("XBRL_PARSER_UNAVAILABLE") from error
        if not records or len(records) > 1000:
            raise ValueError("FILING_USABLE_FINANCIAL_FACTS_MISSING")
        for record in records:
            if (
                record.snapshot_id != snapshot_id
                or record.publication_time != retrieved_at
                or record.retrieval_time != retrieved_at
                or not record.pit_safe
                or record.source_id != source_id
                or record.canonical_source_id != f"companies-house:{source_id}"
                or not isinstance(record.payload, FinancialFact)
                or record.payload.unit != "GBP"
                or record.payload.period_end > retrieved_at
                or not record.available_at(retrieved_at)
            ):
                raise ValueError("FILING_CONVERSION_PROVENANCE_MISMATCH")
            if record.fresh_until <= retrieved_at:
                raise ValueError("CRITICAL_DATA_STALE")
        expiry = min(
            retrieved_at + timedelta(seconds=self.qualification.maximum_age_seconds),
            self.qualification.valid_until,
            identifiers.valid_until,
        )
        records = tuple(
            EvidenceRecord.model_validate(
                record.model_dump()
                | {
                    "provider": "companies-house",
                    "fresh_until": min(record.fresh_until, expiry),
                    "hash": "",
                }
            )
            for record in records
        )
        return FilingDocumentBundle(
            company_number=number,
            filing_id=filing_id,
            document_id=document_id,
            filing_date=filing_date,
            metadata_url=link,
            mime_type=mime,
            content_length=length,
            content_hash=digest,
            metadata_hash=hashlib.sha256(raw_metadata).hexdigest(),
            filing_index_hash=hashlib.sha256(raw_filing).hexdigest(),
            document_created_at=dates[0],
            document_updated_at=dates[1],
            representation_created_at=dates[2],
            representation_updated_at=dates[3],
            retrieval_time=retrieved_at,
            availability_time=retrieved_at,
            currency_evidence_hash=currency_proof.evidence_hash,
            conversion_source_attestation=(
                "PINNED_SOURCE_VERIFIED"
                if self.parser is parse_company_archive
                else "INJECTED_UNATTESTED"
            ),
            conversion_source_hash=(
                SINGLE_MODULE_SOURCE_DIGESTS["stream_read_xbrl"]
                if self.parser is parse_company_archive
                else None
            ),
            storage_host=storage_host,
            storage_host_review_hash=self.storage_hosts.get(storage_host),
            evidence=records,
            raw_document=result.content,
        )
