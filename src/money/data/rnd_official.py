"""Bounded official-source adapters for personal R&D, not production qualification.

SEC: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
BoE: https://www.bankofengland.co.uk/boeapps/database/help.asp
ONS: https://developer.ons.gov.uk/observations/
FRED: https://fred.stlouisfed.org/docs/api/fred/series_observations.html

Current/revised observations are never represented as historical PIT-safe facts.
No raw articles, execution capabilities, or commercial licence assertions belong here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any, Literal, Protocol, TypeVar
from urllib.parse import urlencode

from pydantic import AwareDatetime, Field

from money.data.resilience import ProviderCircuit
from money.data.security import ProviderFailure, SafeFetcher, untrusted_text
from money.data.uk.live import CompaniesHouseProvider
from money.schemas.contracts import Contract, content_hash

T = TypeVar("T")
_FORMS = frozenset({"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A"})
_SEC_LOCK = threading.Lock()
_SEC_LAST_REQUEST = 0.0
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class OfficialProvenance(Contract):
    provider: str
    source_url: str
    retrieved_at: AwareDatetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash_basis: Literal["canonical_json", "response_bytes"] = "canonical_json"
    availability_time: AwareDatetime
    historical_pit_safe: Literal[False] = False
    commercial_rights: Literal["UNAPPROVED"] = "UNAPPROVED"
    limitations: tuple[str, ...] = ()


class OfficialBatch[T](Contract):
    provenance: OfficialProvenance
    records: tuple[T, ...]


class SecCompany(Contract):
    cik: str = Field(pattern=r"^[0-9]{10}$")
    name: str
    ticker: str
    exchange: str | None


class SecFiling(Contract):
    cik: str
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    # SEC's date-only filingDate is NOT an intraday publication timestamp.
    acceptance_time: AwareDatetime | None
    primary_document_url: str | None


class SecFact(Contract):
    cik: str
    taxonomy: str
    concept: str
    label: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    filing_date: date
    accession: str
    form: str


class MacroObservation(Contract):
    period: str
    period_start: date
    precision: Literal["DAY", "MONTH", "YEAR"]
    value: Decimal | None
    # None is missing/suppressed, not zero. No forward filling.
    vintage_date: date | None = None


class MacroSeries(Contract):
    series_id: str
    unit: str | None
    provenance: OfficialProvenance
    observations: tuple[MacroObservation, ...]


class MacroProvider(Protocol):
    def observations(self, start: date, end: date, retrieved_at: datetime) -> MacroSeries: ...


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderFailure("OFFICIAL_RESPONSE_INVALID")
    return value


def _rows(value: Any, maximum: int = 10000) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ProviderFailure("OFFICIAL_RESPONSE_INVALID")
    return value


def _text(value: Any, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProviderFailure("OFFICIAL_TEXT_INVALID")
    return untrusted_text(value, maximum_characters=maximum)


def _date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProviderFailure("OFFICIAL_DATE_INVALID")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ProviderFailure("OFFICIAL_DATE_INVALID") from error


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ProviderFailure("OFFICIAL_VALUE_INVALID")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ProviderFailure("OFFICIAL_VALUE_INVALID") from error
    if not result.is_finite() or len(str(value)) > 100:
        raise ProviderFailure("OFFICIAL_VALUE_INVALID")
    return result


def _retrieval(stamp: datetime) -> None:
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("OFFICIAL_RETRIEVAL_TIME_INVALID")


def _window(start: date, end: date, retrieved_at: datetime) -> None:
    _retrieval(retrieved_at)
    if start > end or end > retrieved_at.date() or (end - start).days > 3660:
        raise ValueError("OFFICIAL_WINDOW_INVALID")


def _provenance(
    provider: str, url: str, payload: Any, retrieved_at: datetime, *limitations: str
) -> OfficialProvenance:
    _retrieval(retrieved_at)
    return OfficialProvenance(
        provider=provider,
        source_url=url,
        retrieved_at=retrieved_at,
        availability_time=retrieved_at,
        content_hash=content_hash(payload),
        limitations=limitations,
    )


def _cik(value: str | int) -> str:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,10}", str(value)):
        raise ValueError("SEC_CIK_INVALID")
    if int(value) == 0:
        raise ValueError("SEC_CIK_INVALID")
    return str(value).zfill(10)


def _accession(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", value):
        raise ProviderFailure("SEC_ACCESSION_INVALID")
    return value


def _sec_throttle() -> None:
    # Shared by all SEC adapters in this process; four calls/sec leaves headroom.
    # Multiple processes MUST also supply a shared rate_gate (5/window=1 sec
    # permits at most ten requests across any adjacent fixed-window boundary).
    global _SEC_LAST_REQUEST
    if not _SEC_LOCK.acquire(timeout=1):
        raise ProviderFailure("SEC_RATE_LIMIT", retryable=True)
    try:
        delay = 0.25 - (time.monotonic() - _SEC_LAST_REQUEST)
        if delay > 0:
            time.sleep(delay)
        _SEC_LAST_REQUEST = time.monotonic()
    finally:
        _SEC_LOCK.release()


class SecEdgarProvider:
    """SEC read-only JSON APIs. User-Agent must identify operator and contact.

    ``rate_gate`` is an optional shared admission callback for multi-process use;
    it must deny before the fetch when the SEC operator-wide limit is exhausted.
    This adapter does not assert deploy-wide qualification from its local limiter.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        fetcher: SafeFetcher | None = None,
        circuit: ProviderCircuit | None = None,
        rate_gate: Callable[[], bool] | None = None,
    ) -> None:
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", user_agent):
            raise ValueError("SEC_CONTACT_USER_AGENT_REQUIRED")
        # Even injected fetchers must carry the explicit validated contact header.
        policy = SafeFetcher(
            frozenset({"www.sec.gov", "data.sec.gov"}),
            maximum_bytes=20_000_000,
            maximum_redirects=0,
            user_agent=user_agent,
        )
        self._fetcher = fetcher or policy
        if self._fetcher.user_agent != user_agent:
            raise ValueError("SEC_CONTACT_USER_AGENT_MISMATCH")
        self._circuit, self._rate_gate = circuit, rate_gate

    def _get(self, url: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            _sec_throttle()
            if self._rate_gate is not None and not self._rate_gate():
                raise ProviderFailure("SEC_RATE_LIMIT", retryable=True)
            return _object(self._fetcher.json(url))

        return self._circuit.call("sec-edgar:official", operation) if self._circuit else operation()

    def search(
        self, query: str, retrieved_at: datetime, *, limit: int = 10
    ) -> OfficialBatch[SecCompany]:
        _retrieval(retrieved_at)
        if not 1 <= len(query.strip()) <= 100 or not 1 <= limit <= 50:
            raise ValueError("SEC_SEARCH_INVALID")
        url = "https://www.sec.gov/files/company_tickers_exchange.json"
        data = self._get(url)
        fields = data.get("fields")
        if fields != ["cik", "name", "ticker", "exchange"]:
            raise ProviderFailure("SEC_DIRECTORY_SCHEMA_INVALID")
        matches = []
        needle = query.strip().casefold()
        for row in _rows(data.get("data"), 100000):
            if not isinstance(row, list) or len(row) != 4:
                raise ProviderFailure("SEC_DIRECTORY_SCHEMA_INVALID")
            company = SecCompany(
                cik=_cik(row[0]),
                name=_text(row[1]),
                ticker=_text(row[2], 32),
                exchange=_text(row[3], 100) if row[3] else None,
            )
            if (
                needle in company.name.casefold()
                or needle in company.ticker.casefold()
                or (needle.isascii() and needle.isdigit() and company.cik == needle.zfill(10))
            ):
                matches.append(company)
        matches.sort(key=lambda c: (c.ticker.casefold() != needle, c.ticker, c.cik))
        return OfficialBatch[SecCompany](
            records=tuple(matches[:limit]),
            provenance=_provenance(
                "sec-edgar",
                url,
                data,
                retrieved_at,
                "SEC ticker association is not current listing or ISA eligibility verification.",
            ),
        )

    def submissions(self, cik: str | int, retrieved_at: datetime) -> OfficialBatch[SecFiling]:
        _retrieval(retrieved_at)
        identifier = _cik(cik)
        url = f"https://data.sec.gov/submissions/CIK{identifier}.json"
        data = self._get(url)
        if _cik(data.get("cik", "")) != identifier:
            raise ProviderFailure("SEC_COMPANY_MISMATCH")
        recent = _object(_object(data.get("filings")).get("recent"))
        columns = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
        values = {key: _rows(recent.get(key)) for key in columns}
        length = len(values["form"])
        acceptance = _rows(recent.get("acceptanceDateTime", [None] * length))
        if any(len(column) != length for column in (*values.values(), acceptance)):
            raise ProviderFailure("SEC_SUBMISSIONS_COLUMNS_INVALID")
        records = []
        for index, form in enumerate(values["form"]):
            if not isinstance(form, str):
                raise ProviderFailure("SEC_FORM_INVALID")
            if form not in _FORMS:
                continue
            accession = _accession(values["accessionNumber"][index])
            filing_date = _date(values["filingDate"][index])
            if filing_date > retrieved_at.date():
                raise ProviderFailure("PIT_VIOLATION")
            accepted = None
            raw_acceptance = acceptance[index]
            if raw_acceptance:
                try:
                    accepted = datetime.fromisoformat(raw_acceptance)
                except (ValueError, TypeError) as error:
                    raise ProviderFailure("SEC_ACCEPTANCE_TIME_INVALID") from error
                # Never guess a timezone for an ambiguous source timestamp.
                if accepted.tzinfo is None or accepted.utcoffset() is None:
                    accepted = None
                elif accepted > retrieved_at:
                    raise ProviderFailure("PIT_VIOLATION")
            document = values["primaryDocument"][index]
            if document and (
                not isinstance(document, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", document)
                or ".." in document
            ):
                raise ProviderFailure("SEC_DOCUMENT_PATH_INVALID")
            records.append(
                SecFiling(
                    cik=identifier,
                    accession=accession,
                    form=form,
                    filing_date=filing_date,
                    report_date=_date(values["reportDate"][index])
                    if values["reportDate"][index]
                    else None,
                    acceptance_time=accepted,
                    primary_document_url=(
                        f"https://www.sec.gov/Archives/edgar/data/{int(identifier)}/"
                        f"{accession.replace('-', '')}/{document}"
                        if document
                        else None
                    ),
                )
            )
        return OfficialBatch[SecFiling](
            records=tuple(records),
            provenance=_provenance(
                "sec-edgar",
                url,
                data,
                retrieved_at,
                "Recent submission metadata only; historical archive segments are not fetched.",
                "10-K/10-Q/8-K and amendments only; no document contents or historical PIT guarantee.",
            ),
        )

    def company_facts(
        self,
        cik: str | int,
        retrieved_at: datetime,
        *,
        concepts: tuple[tuple[str, str], ...] = (
            ("us-gaap", "Assets"),
            ("us-gaap", "Liabilities"),
            ("us-gaap", "NetIncomeLoss"),
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ),
    ) -> OfficialBatch[SecFact]:
        _retrieval(retrieved_at)
        if not 1 <= len(concepts) <= 20 or len(set(concepts)) != len(concepts):
            raise ValueError("SEC_CONCEPT_SELECTION_INVALID")
        identifier = _cik(cik)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{identifier}.json"
        data = self._get(url)
        if _cik(data.get("cik", "")) != identifier:
            raise ProviderFailure("SEC_COMPANY_MISMATCH")
        facts = _object(data.get("facts"))
        records = []
        for taxonomy, concept in concepts:
            block = _object(facts.get(taxonomy, {})).get(concept)
            if block is None:
                continue  # Missing concepts are not manufactured or substituted.
            block = _object(block)
            for unit, rows in _object(block.get("units")).items():
                for row in _rows(rows):
                    row = _object(row)
                    if not isinstance(row.get("form"), str):
                        raise ProviderFailure("SEC_FORM_INVALID")
                    if row.get("form") not in _FORMS:
                        continue
                    filed, end = _date(row.get("filed")), _date(row.get("end"))
                    start = _date(row["start"]) if row.get("start") else None
                    if filed > retrieved_at.date() or end > filed or (start and start > end):
                        raise ProviderFailure("PIT_VIOLATION")
                    records.append(
                        SecFact(
                            cik=identifier,
                            taxonomy=_text(taxonomy, 50),
                            concept=_text(concept, 150),
                            label=_text(block.get("label", concept), 500),
                            unit=_text(unit, 100),
                            value=_decimal(row.get("val")),
                            period_start=start,
                            period_end=end,
                            filing_date=filed,
                            accession=_accession(row.get("accn")),
                            form=row["form"],
                        )
                    )
                    if len(records) > 10000:
                        raise ProviderFailure("SEC_FACT_LIMIT")
        return OfficialBatch[SecFact](
            records=tuple(records),
            provenance=_provenance(
                "sec-edgar",
                url,
                data,
                retrieved_at,
                "Selected entity-wide standard taxonomy concepts only; units and revisions retained.",
                "Filed date has day precision; facts are excluded from historical PIT validation.",
            ),
        )


class _MacroBase:
    provider: str

    def __init__(self, fetcher: SafeFetcher, circuit: ProviderCircuit | None) -> None:
        self._fetcher, self._circuit = fetcher, circuit

    def _call(self, operation: Callable[[], T]) -> T:
        return (
            self._circuit.call(f"{self.provider}:macro", operation)
            if self._circuit
            else operation()
        )


class BoEMacroProvider(_MacroBase):
    """Official Bank Rate (IUDBEDR) by default; CSV has no historical publication data."""

    provider = "bank-of-england"

    def __init__(
        self,
        series_id: str = "IUDBEDR",
        *,
        unit: str | None = None,
        fetcher: SafeFetcher | None = None,
        circuit: ProviderCircuit | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Z0-9]{3,24}", series_id):
            raise ValueError("BOE_SERIES_INVALID")
        self.series_id = series_id
        self.unit = unit if unit is not None else ("percent" if series_id == "IUDBEDR" else None)
        super().__init__(fetcher or SafeFetcher(frozenset({"www.bankofengland.co.uk"})), circuit)

    def observations(self, start: date, end: date, retrieved_at: datetime) -> MacroSeries:
        _window(start, end, retrieved_at)

        def format_date(value: date) -> str:
            return f"{value.day:02}/{_MONTHS[value.month - 1]}/{value.year}"

        url = (
            "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?"
            + urlencode(
                {
                    "csv.x": "yes",
                    "Datefrom": format_date(start),
                    "Dateto": format_date(end),
                    "SeriesCodes": self.series_id,
                    "CSVF": "TN",
                    "UsingCodes": "Y",
                    "VPD": "N",
                }
            )
        )
        response = self._call(
            lambda: self._fetcher.get(url, mime_types=("text/csv", "application/csv", "text/plain"))
        )
        try:
            reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
            if reader.fieldnames != ["DATE", self.series_id]:
                raise ProviderFailure("BOE_COLUMNS_INVALID")
            observations = []
            seen: set[date] = set()
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ProviderFailure("BOE_COLUMNS_INVALID")
                # BoE CSV reports DD Mon YYYY, never a publication timestamp.
                parts = row["DATE"].split()
                if len(parts) != 3 or parts[1].title() not in _MONTHS:
                    raise ProviderFailure("BOE_DATE_INVALID")
                day = date(int(parts[2]), _MONTHS.index(parts[1].title()) + 1, int(parts[0]))
                if not start <= day <= end or day in seen or len(seen) >= 10000:
                    raise ProviderFailure("BOE_DATE_INVALID")
                seen.add(day)
                value = row[self.series_id].strip()
                observations.append(
                    MacroObservation(
                        period=day.isoformat(),
                        period_start=day,
                        precision="DAY",
                        value=None if value in {"", "..", "n/a"} else _decimal(value),
                    )
                )
        except (UnicodeError, csv.Error, ValueError) as error:
            raise ProviderFailure("BOE_RESPONSE_INVALID") from error
        provenance = _provenance(
            self.provider,
            url,
            {},
            retrieved_at,
            "CSV contains current/revised observations, not historical publication availability.",
            "Missing/suppressed values are retained as null; provisional values are not requested.",
        ).model_copy(
            update={
                "content_hash": hashlib.sha256(response.content).hexdigest(),
                "hash_basis": "response_bytes",
            }
        )
        return MacroSeries(
            series_id=self.series_id,
            unit=self.unit,
            provenance=provenance,
            observations=tuple(sorted(observations, key=lambda item: item.period_start)),
        )


class FredMacroProvider(_MacroBase):
    provider = "fred"

    def __init__(
        self,
        api_key: str,
        series_id: str,
        *,
        unit: str | None = None,
        vintage_date: date | None = None,
        fetcher: SafeFetcher | None = None,
        circuit: ProviderCircuit | None = None,
    ) -> None:
        if (
            not re.fullmatch(r"[a-z0-9]{32}", api_key)
            or api_key == "abcdefghijklmnopqrstuvwxyz123456"
        ):
            raise ValueError("FRED_CREDENTIAL_MISSING")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", series_id):
            raise ValueError("FRED_SERIES_INVALID")
        self._key, self.series_id, self.unit, self.vintage_date = (
            api_key,
            series_id,
            unit,
            vintage_date,
        )
        super().__init__(fetcher or SafeFetcher(frozenset({"api.stlouisfed.org"})), circuit)

    def observations(self, start: date, end: date, retrieved_at: datetime) -> MacroSeries:
        _window(start, end, retrieved_at)
        vintage = self.vintage_date or retrieved_at.date()
        if vintage > retrieved_at.date():
            raise ValueError("FRED_VINTAGE_INVALID")
        params = {
            "series_id": self.series_id,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "limit": "10000",
            "offset": "0",
            "sort_order": "asc",
            "realtime_start": vintage.isoformat(),
            "realtime_end": vintage.isoformat(),
        }
        public_url = "https://api.stlouisfed.org/fred/series/observations?" + urlencode(params)
        data = _object(
            self._call(
                lambda: self._fetcher.json(public_url + "&" + urlencode({"api_key": self._key}))
            )
        )
        rows = _rows(data.get("observations"))
        if data.get("count") != len(rows) or data.get("offset") != 0:
            raise ProviderFailure("FRED_INCOMPLETE_RESPONSE")
        observations = []
        seen = set()
        for row in rows:
            row = _object(row)
            day = _date(row.get("date"))
            if day in seen or not start <= day <= end:
                raise ProviderFailure("FRED_DATE_INVALID")
            if (
                _date(row.get("realtime_start")) != vintage
                or _date(row.get("realtime_end")) != vintage
            ):
                raise ProviderFailure("FRED_VINTAGE_MISMATCH")
            seen.add(day)
            observations.append(
                MacroObservation(
                    period=day.isoformat(),
                    period_start=day,
                    precision="DAY",
                    vintage_date=vintage,
                    value=None if row.get("value") == "." else _decimal(row.get("value")),
                )
            )
        return MacroSeries(
            series_id=self.series_id,
            unit=self.unit,
            observations=tuple(observations),
            provenance=_provenance(
                self.provider,
                public_url,
                data,
                retrieved_at,
                "FRED vintage is date-granular; intraday historical availability is not established.",
                "Observation date labels do not establish series frequency or publication time.",
            ),
        )


class OnsMacroProvider(_MacroBase):
    """A deliberately pinned CMD dataset version; no guessed latest-version lookup.

    Configured periods map exact official time labels to their period start. One
    observation is requested per label (maximum 24). This avoids guessing the
    meaning of provider time codes or silently choosing a different dataset.
    """

    provider = "ons"

    def __init__(
        self,
        dataset: str,
        edition: str,
        version: int,
        *,
        dimensions: Mapping[str, str],
        periods: Mapping[str, date],
        precision: Literal["DAY", "MONTH", "YEAR"],
        fetcher: SafeFetcher | None = None,
        circuit: ProviderCircuit | None = None,
    ) -> None:
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) for value in (dataset, edition)):
            raise ValueError("ONS_DATASET_INVALID")
        if not 1 <= version <= 100000 or not 1 <= len(periods) <= 24 or not dimensions:
            raise ValueError("ONS_SELECTION_INVALID")
        if "time" in dimensions or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value)
            for key, value in dimensions.items()
        ):
            raise ValueError("ONS_DIMENSIONS_INVALID")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", label) for label in periods):
            raise ValueError("ONS_PERIOD_INVALID")
        self.dataset, self.edition, self.version = dataset, edition, version
        self.dimensions, self.periods, self.precision = dict(dimensions), dict(periods), precision
        super().__init__(fetcher or SafeFetcher(frozenset({"api.beta.ons.gov.uk"})), circuit)

    def observations(self, start: date, end: date, retrieved_at: datetime) -> MacroSeries:
        _window(start, end, retrieved_at)
        endpoint = (
            f"https://api.beta.ons.gov.uk/v1/datasets/{self.dataset}/editions/"
            f"{self.edition}/versions/{self.version}/observations"
        )
        observations, payloads = [], []
        unit = None
        for label, period in sorted(self.periods.items(), key=lambda item: item[1]):
            if not start <= period <= end:
                continue
            url = endpoint + "?" + urlencode(dict(self.dimensions, time=label))
            data = _object(self._call(partial(self._fetcher.json, url)))
            rows = _rows(data.get("observations"), 1)
            if len(rows) != 1 or data.get("total_observations") != 1 or data.get("offset") != 0:
                raise ProviderFailure("ONS_INCOMPLETE_RESPONSE")
            returned_dimensions = _object(data.get("dimensions"))
            for dimension, expected in dict(self.dimensions, time=label).items():
                selected = _object(_object(returned_dimensions.get(dimension)).get("option"))
                if selected.get("id") != expected:
                    raise ProviderFailure("ONS_DIMENSION_MISMATCH")
            current_unit = _text(data.get("unit_of_measure"), 200)
            if unit is not None and current_unit != unit:
                raise ProviderFailure("ONS_UNIT_MISMATCH")
            unit = current_unit
            payloads.append(data)
            observations.append(
                MacroObservation(
                    period=label,
                    period_start=period,
                    precision=self.precision,
                    value=_decimal(_object(rows[0]).get("observation")),
                )
            )
        return MacroSeries(
            series_id=f"{self.dataset}/{self.edition}/{self.version}",
            unit=unit,
            observations=tuple(observations),
            provenance=_provenance(
                self.provider,
                endpoint,
                payloads,
                retrieved_at,
                "Explicit pinned dataset/version/period selection; no original publication guarantee.",
            ),
        )


class UKFiling(Contract):
    company_number: str
    transaction_id: str
    filing_date: date
    category: str
    description: str
    source_url: str


class UKCompanyNumberProvider(CompaniesHouseProvider):
    """Reuse CH authentication/transport for an explicitly reviewed number mapping.

    This deliberately does not manufacture ISA, ISIN or other instrument metadata
    required by the stricter production CompaniesHouseProvider.filings interface.
    """

    def filing_index(self, company_number: str, retrieved_at: datetime) -> OfficialBatch[UKFiling]:
        _retrieval(retrieved_at)
        if not re.fullmatch(r"[A-Z0-9]{8}", company_number):
            raise ValueError("COMPANY_NUMBER_MAPPING_INVALID")
        url = (
            "https://api.company-information.service.gov.uk/company/"
            f"{company_number}/filing-history?items_per_page=100&start_index=0"
        )
        data = _object(self._fetcher.json(url, headers={"Authorization": self._authorization}))
        records = []
        seen = set()
        for row in _rows(data.get("items"), 100):
            row = _object(row)
            transaction_id = row.get("transaction_id")
            if (
                not isinstance(transaction_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", transaction_id)
                or transaction_id in seen
            ):
                raise ProviderFailure("FILING_IDENTIFIER_INVALID")
            seen.add(transaction_id)
            filed = _date(row.get("date"))
            if filed > retrieved_at.date():
                raise ProviderFailure("PIT_VIOLATION")
            records.append(
                UKFiling(
                    company_number=company_number,
                    transaction_id=transaction_id,
                    filing_date=filed,
                    category=_text(row.get("category", "unknown"), 100),
                    description=_text(row.get("description", "Filing"), 500),
                    source_url="https://find-and-update.company-information.service.gov.uk/company/"
                    f"{company_number}/filing-history/{transaction_id}",
                )
            )
        return OfficialBatch[UKFiling](
            records=tuple(records),
            provenance=_provenance(
                "companies-house",
                url,
                data,
                retrieved_at,
                "Operator-reviewed ticker/company-number mapping; no ISA eligibility assertion.",
                "First 100 index entries only, not statement contents or extracted fundamentals.",
                "Filing dates have day precision; original publication availability is unverified.",
            ),
        )


def _context(
    provider: str,
    status: str,
    *,
    reason: str | None = None,
    records: list[dict[str, Any]] | None = None,
    provenance: list[dict[str, Any]] | None = None,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": provider,
        "status": status,
        "records": records or [],
        "provenance": provenance or [],
        "production_qualified": False,
        "historical_pit_safe": False,
        "commercial_rights": "UNAPPROVED",
        "retrieved_at": retrieved_at.isoformat() if retrieved_at else None,
    }
    if reason:
        result["reason"] = reason
    return result


def collect_official_context(
    symbol: str,
    country: str,
    *,
    user_agent: str | None,
    companies_house_key: str | None = None,
    company_number: str | None = None,
    rate_gate: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Optional snapshot context; failures remain visible and never become facts."""
    if country == "US":
        if not user_agent:
            return _context("sec-edgar", "NOT_CONFIGURED", reason="SEC_CONTACT_USER_AGENT_REQUIRED")
        try:
            provider = SecEdgarProvider(user_agent, rate_gate=rate_gate)
            stamp = datetime.now(UTC)
            directory = provider.search(symbol, stamp, limit=50)
            matches = [company for company in directory.records if company.ticker == symbol]
            if len(matches) != 1:
                return _context(
                    "sec-edgar", "UNAVAILABLE", reason="SEC_EXACT_TICKER_MATCH_REQUIRED"
                )
            company = matches[0]
            filings = provider.submissions(company.cik, datetime.now(UTC))
            facts = provider.company_facts(company.cik, datetime.now(UTC))
            # Bounded presentation excerpt. Full response hashes identify source batches.
            recent_facts = sorted(
                facts.records, key=lambda fact: (fact.filing_date, fact.period_end)
            )
            records = [dict(company.model_dump(mode="json"), record_type="company")]
            records.extend(
                dict(row.model_dump(mode="json"), record_type="filing")
                for row in filings.records[:30]
            )
            records.extend(
                dict(row.model_dump(mode="json"), record_type="financial_fact")
                for row in recent_facts[-100:]
            )
            return _context(
                "sec-edgar",
                "READY",
                records=records,
                retrieved_at=datetime.now(UTC),
                provenance=[
                    batch.provenance.model_dump(mode="json")
                    for batch in (directory, filings, facts)
                ],
            )
        except (ProviderFailure, ValueError) as error:
            return _context(
                "sec-edgar",
                "UNAVAILABLE",
                reason=error.code
                if isinstance(error, ProviderFailure)
                else "OFFICIAL_RESPONSE_INVALID",
            )
    if country == "GB":
        if not companies_house_key:
            return _context(
                "companies-house", "NOT_CONFIGURED", reason="COMPANIES_HOUSE_CREDENTIAL_MISSING"
            )
        if not company_number:
            return _context(
                "companies-house", "NOT_CONFIGURED", reason="COMPANY_NUMBER_MAPPING_MISSING"
            )
        try:
            batch = UKCompanyNumberProvider(companies_house_key).filing_index(
                company_number, datetime.now(UTC)
            )
            return _context(
                "companies-house",
                "READY" if batch.records else "UNAVAILABLE",
                reason=None if batch.records else "OFFICIAL_NO_RECORDS",
                records=[
                    dict(row.model_dump(mode="json"), record_type="filing") for row in batch.records
                ],
                provenance=[batch.provenance.model_dump(mode="json")],
                retrieved_at=datetime.now(UTC),
            )
        except (ProviderFailure, ValueError) as error:
            return _context(
                "companies-house",
                "UNAVAILABLE",
                reason=error.code
                if isinstance(error, ProviderFailure)
                else "OFFICIAL_RESPONSE_INVALID",
            )
    return _context("official-filings", "NOT_CONFIGURED", reason="OFFICIAL_COUNTRY_UNSUPPORTED")


def collect_macro_context() -> dict[str, Any]:
    """Keyless BoE Bank Rate context; only a successful nonempty fetch is READY."""
    stamp = datetime.now(UTC)
    try:
        series = BoEMacroProvider().observations(
            stamp.date() - timedelta(days=30), stamp.date(), stamp
        )
        has_values = any(row.value is not None for row in series.observations)
        return _context(
            "bank-of-england",
            "READY" if has_values else "UNAVAILABLE",
            reason=None if has_values else "OFFICIAL_NO_RECORDS",
            records=[
                dict(
                    row.model_dump(mode="json"),
                    series_id=series.series_id,
                    unit=series.unit,
                    record_type="macro",
                )
                for row in series.observations
            ],
            provenance=[series.provenance.model_dump(mode="json")],
            retrieved_at=datetime.now(UTC),
        )
    except (ProviderFailure, ValueError) as error:
        return _context(
            "bank-of-england",
            "UNAVAILABLE",
            reason=error.code
            if isinstance(error, ProviderFailure)
            else "OFFICIAL_RESPONSE_INVALID",
        )
