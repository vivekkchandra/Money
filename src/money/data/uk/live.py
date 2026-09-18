"""Read-only UK provider adapters. No brokerage account capability is present.

Retrieval is conservative availability for versionless feeds. An accounting
date, ex-date or trading date is never promoted to a publication timestamp.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

from money.data.identifiers import InstrumentIdentifiers
from money.data.qualification import ProviderQualification
from money.data.security import SafeFetcher, SourceSecurityError, untrusted_text
from money.schemas.contracts import DocumentFact, EvidenceRecord, PriceBar, content_hash


class Trading212MetadataProvider:
    """Only the documented instrument/exchange metadata GETs, never account APIs.

    The official schema does not establish ISA/current purchase eligibility.
    Neither account type nor ISA purchase availability is a Money qualification
    requirement. Callers still verify current membership, identity and all
    independent provider/ethical evidence.
    """

    def __init__(self, api_key: str, api_secret: str, fetcher: SafeFetcher | None = None) -> None:
        if not api_key or not api_secret:
            raise ValueError("TRADING212_METADATA_CREDENTIAL_MISSING")
        self._authorization = (
            "Basic " + base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        )
        # A complete current broker catalogue is larger than a single-symbol
        # response; retain a hard bound without truncating to a seed list.
        self._fetcher = fetcher or SafeFetcher(
            frozenset({"live.trading212.com"}), maximum_bytes=20_000_000,
            maximum_redirects=0,
        )

    def metadata_response(
        self, kind: Literal["instruments", "exchanges"]
    ) -> tuple[bytes, tuple[Any, ...]]:
        """Return actual response bytes and rows for a hashable, bulk audit trail.

        A bad row remains visible to the bulk normalizer, which quarantines it
        without dropping other instruments. The top-level response must still
        be a bounded JSON array. No caller-supplied account/order path is allowed.
        """
        if kind not in {"instruments", "exchanges"}:
            raise ValueError("TRADING212_METADATA_RESOURCE_DENIED")
        response = self._fetcher.get(
            f"https://live.trading212.com/api/v0/equity/metadata/{kind}",
            headers={"Authorization": self._authorization},
        )

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("ELIGIBILITY_METADATA_INVALID")
                result[key] = value
            return result

        def invalid_constant(value: str) -> None:
            raise ValueError("ELIGIBILITY_METADATA_INVALID")

        try:
            rows = json.loads(
                response.content,
                object_pairs_hook=unique_object,
                parse_constant=invalid_constant,
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("ELIGIBILITY_METADATA_INVALID") from error
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError("ELIGIBILITY_METADATA_INVALID")
        return response.content, tuple(rows)

    def exchanges(self) -> tuple[dict[str, Any], ...]:
        """Retrieve exchange metadata without inferring venue country or MIC."""
        _, rows = self.metadata_response("exchanges")
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("EXCHANGE_METADATA_INVALID")
        return tuple(rows)

    def instruments(self) -> tuple[dict[str, Any], ...]:
        rows = self._fetcher.json(
            "https://live.trading212.com/api/v0/equity/metadata/instruments",
            headers={"Authorization": self._authorization},
        )
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError("ELIGIBILITY_METADATA_INVALID")
        allowed = {
            "ticker",
            "type",
            "name",
            "shortName",
            "isin",
            "currencyCode",
            "workingScheduleId",
            "addedOn",
            "maxOpenQuantity",
            "extendedHours",
            "exchangeId",
            "exchange",
        }
        if any(
            not isinstance(row, dict)
            or not {"ticker", "isin", "type", "currencyCode"} <= row.keys()
            for row in rows
        ):
            raise ValueError("ELIGIBILITY_METADATA_INVALID")
        return tuple({key: row[key] for key in allowed & row.keys()} for row in rows)


class CompaniesHouseProvider:
    def __init__(self, api_key: str, fetcher: SafeFetcher | None = None) -> None:
        if not api_key:
            raise ValueError("COMPANIES_HOUSE_CREDENTIAL_MISSING")
        self._authorization = "Basic " + base64.b64encode(f"{api_key}:".encode()).decode()
        self._fetcher = fetcher or SafeFetcher(
            frozenset({"api.company-information.service.gov.uk"})
        )

    def company(
        self, identifiers: InstrumentIdentifiers, retrieved_at: datetime
    ) -> dict[str, Any]:
        """Retrieve only public company identity/classification, never officer data."""
        identifiers.require_current(retrieved_at)
        number = identifiers.companies_house_number
        if not number:
            raise ValueError("COMPANY_NUMBER_MAPPING_MISSING")
        result = self._fetcher.json(
            f"https://api.company-information.service.gov.uk/company/{number}",
            headers={"Authorization": self._authorization},
        )
        if (
            not isinstance(result, dict)
            or result.get("company_number") != number
            or not isinstance(result.get("company_name"), str)
            or not result["company_name"].strip()
        ):
            raise ValueError("COMPANY_RESPONSE_INVALID")
        allowed = {"company_number", "company_name", "company_status", "type", "sic_codes"}
        return {key: result[key] for key in allowed & result.keys()}

    def filings(
        self, identifiers: InstrumentIdentifiers, snapshot_id: str, retrieved_at: datetime
    ) -> tuple[EvidenceRecord, ...]:
        identifiers.require_current(retrieved_at)
        number = identifiers.companies_house_number
        if not number:
            raise ValueError("COMPANY_NUMBER_MAPPING_MISSING")
        endpoint = f"https://api.company-information.service.gov.uk/company/{number}/filing-history"
        records = []
        # Bounded 500 latest filings; no claim of complete historical coverage.
        for start in range(0, 500, 100):
            result = self._fetcher.json(
                endpoint + "?" + urlencode({"items_per_page": 100, "start_index": start}),
                headers={"Authorization": self._authorization},
            )
            if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                raise ValueError("FILING_RESPONSE_INVALID")
            for item in result["items"]:
                identifier = str(item["transaction_id"])
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                    raise ValueError("FILING_IDENTIFIER_INVALID")
                observed = datetime.strptime(item["date"], "%Y-%m-%d").replace(tzinfo=UTC)
                if observed > retrieved_at:
                    raise ValueError("PIT_VIOLATION")
                records.append(
                    EvidenceRecord(
                        snapshot_id=snapshot_id,
                        source="Companies House",
                        provider="companies-house",
                        source_id=identifier,
                        canonical_source_id=f"companies-house:{number}:{identifier}",
                        observation_time=observed,
                        publication_time=retrieved_at,
                        retrieval_time=retrieved_at,
                        fresh_until=retrieved_at + timedelta(days=1),
                        pit_safe=True,
                        payload=DocumentFact(
                            kind="filing",
                            title=untrusted_text(
                                str(item.get("description", "Filing")), maximum_characters=500
                            ),
                            excerpt="Filing index metadata only. Contents and original publication time require separate verification.",
                            url=f"https://find-and-update.company-information.service.gov.uk/company/{number}/filing-history/{identifier}",
                        ),
                    )
                )
            if (
                start + len(result["items"]) >= int(result.get("total_count", 0))
                or not result["items"]
            ):
                break
        return tuple(records)


class EODHDProvider:
    def __init__(
        self, api_key: str, qualification: ProviderQualification, fetcher: SafeFetcher | None = None
    ) -> None:
        if not api_key or api_key.casefold() == "demo":
            raise ValueError("MARKET_PROVIDER_CREDENTIAL_MISSING")
        if qualification.provider != "eodhd":
            raise ValueError("PROVIDER_QUALIFICATION_MISMATCH")
        self._key = api_key
        self.qualification = qualification
        self._fetcher = fetcher or SafeFetcher(frozenset({"eodhd.com"}), maximum_bytes=5_000_000)

    def _rows(self, path: str, query: dict[str, str]) -> list[dict[str, Any]]:
        url = f"https://eodhd.com/api/{path}?" + urlencode(
            dict(query, api_token=self._key, fmt="json")
        )
        rows = self._fetcher.json(url)
        if (
            not isinstance(rows, list)
            or len(rows) > 10000
            or any(not isinstance(r, dict) for r in rows)
        ):
            raise ValueError("PROVIDER_RESPONSE_INVALID")
        return rows

    def fetch(
        self,
        identifiers: InstrumentIdentifiers,
        dataset: str,
        snapshot_id: str,
        retrieved_at: datetime,
    ) -> tuple[EvidenceRecord, ...]:
        self.qualification.require(dataset, retrieved_at)
        if dataset == "ohlcv":
            self.qualification.require("corporate_action", retrieved_at)
        return self._fetch_records(identifiers, dataset, snapshot_id, retrieved_at)

    def _fetch_records(
        self,
        identifiers: InstrumentIdentifiers,
        dataset: str,
        snapshot_id: str,
        retrieved_at: datetime,
    ) -> tuple[EvidenceRecord, ...]:
        """Normalize live responses; also used by the admission probe.

        The production entry point above always enforces qualification. The probe
        uses an explicitly unqualified scope and cannot return production data
        through ``fetch`` until its actual observations have been reviewed.
        """
        if (
            identifiers.quote_currency not in self.qualification.currencies
            or "GB" not in self.qualification.geography
            or "STOCK" not in self.qualification.instrument_types
        ):
            raise ValueError("PROVIDER_COVERAGE_MISSING")
        symbol = identifiers.symbol_for("eodhd", retrieved_at)
        # The symbol comes from an explicit verified provider mapping. A suffix
        # is not a geographic authority: UK venue identity is established by
        # exchange evidence, including venues other than London's main market.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,25}\.[A-Za-z0-9_-]{1,20}", symbol):
            raise ValueError("IDENTIFIER_MAPPING_INVALID")
        start = max(retrieved_at - timedelta(days=365), self.qualification.earliest_observation)
        if start >= retrieved_at:
            raise ValueError("PROVIDER_COVERAGE_MISSING")
        query = {
            "from": start.date().isoformat(),
            "to": retrieved_at.date().isoformat(),
        }
        if dataset == "ohlcv":
            # EODHD explicitly documents raw OHLC and split-adjusted volume.
            # Do not combine these bases across a split/consolidation. Until
            # Money has a qualified reversal policy, reject the affected window.
            # Query through retrieval, not just the last bar, since a later split
            # can retroactively adjust earlier volumes in this versionless feed.
            for action in self._rows(f"splits/{symbol}", query):
                if not isinstance(action.get("date"), str) or not isinstance(action.get("split"), str):
                    raise ValueError("CORPORATE_ACTION_RESPONSE_INVALID")
                split_day = datetime.strptime(action["date"], "%Y-%m-%d").date()
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?/[0-9]+(?:\.[0-9]+)?", action["split"]):
                    raise ValueError("CORPORATE_ACTION_RESPONSE_INVALID")
                if not query["from"] <= split_day.isoformat() <= query["to"]:
                    raise ValueError("PROVIDER_WINDOW_MISMATCH")
                raise ValueError("CORPORATE_ACTION_VOLUME_BASIS_UNVERIFIED")
            rows = self._rows(f"eod/{symbol}", dict(query, period="d", order="a"))
            records = []
            seen: set[str] = set()
            for row in rows:
                day = str(row["date"])
                if day in seen:
                    raise ValueError("DUPLICATE_PRICE_TIMESTAMP")
                seen.add(day)
                # EOD rows are admitted only after the complete trading date.
                observed = datetime.combine(datetime.strptime(day, "%Y-%m-%d").date(), time.max, UTC)
                if day < query["from"] or day > query["to"]:
                    raise ValueError("PROVIDER_WINDOW_MISMATCH")
                if observed > retrieved_at:
                    continue
                payload = PriceBar.model_validate(
                    {name: row[name] for name in ("open", "high", "low", "close", "volume")}
                    | {"currency": identifiers.quote_currency}
                )
                records.append(
                    self._record(
                        snapshot_id,
                        symbol + ":" + day,
                        observed,
                        retrieved_at,
                        payload,
                        critical=True,
                        basis="RAW_OHLC_VOLUME_NO_SPLITS_IN_WINDOW_V1",
                    )
                )
            return tuple(records)
        if dataset == "news":
            rows = self._rows("news", dict(query, s=symbol, limit="50"))
            records = []
            for row in rows:
                link = str(row["link"])
                parts = urlsplit(link)
                if (
                    parts.scheme != "https"
                    or not parts.hostname
                    or parts.username
                    or parts.password
                ):
                    raise SourceSecurityError("SOURCE_LINK_DENIED")
                observed = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
                if observed.tzinfo is None or observed > retrieved_at:
                    raise ValueError("PIT_VIOLATION")
                # Links + bounded title only until raw-content licensing is verified.
                document = DocumentFact(
                    kind="news",
                    title=untrusted_text(str(row["title"]), maximum_characters=500),
                    excerpt="Source link supplied for independent review; raw article redistribution is not enabled.",
                    url=link,
                )
                records.append(
                    self._record(
                        snapshot_id,
                        content_hash(link),
                        observed,
                        retrieved_at,
                        document,
                        canonical=link,
                    )
                )
            return tuple(records)
        if dataset == "corporate_action":
            records = []
            for endpoint in ("splits", "div"):
                for row in self._rows(f"{endpoint}/{symbol}", query):
                    observed = datetime.fromisoformat(str(row["date"])).replace(tzinfo=UTC)
                    if observed > retrieved_at:
                        continue
                    fields = {
                        k: row[k]
                        for k in (
                            "date",
                            "split",
                            "value",
                            "currency",
                            "declarationDate",
                            "paymentDate",
                        )
                        if k in row
                    }
                    records.append(
                        self._record(
                            snapshot_id,
                            content_hash(fields),
                            observed,
                            retrieved_at,
                            DocumentFact(
                                kind="corporate_action",
                                title=f"{endpoint}: {symbol} {row['date']}",
                                excerpt=str(fields)[:2000],
                                url="https://eodhd.com/financial-apis/api-splits-dividends",
                            ),
                        )
                    )
            return tuple(records)
        raise ValueError("PROVIDER_COVERAGE_MISSING")

    def _record(
        self,
        snapshot_id: str,
        source_id: str,
        observed: datetime,
        retrieved: datetime,
        payload: PriceBar | DocumentFact,
        *,
        critical: bool = False,
        canonical: str | None = None,
        basis: str | None = None,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            snapshot_id=snapshot_id,
            source=f"EODHD ({basis})" if basis else "EODHD",
            provider="eodhd",
            source_id=source_id,
            canonical_source_id=canonical or f"eodhd:{source_id}",
            observation_time=observed,
            publication_time=retrieved,
            retrieval_time=retrieved,
            pit_safe=True,
            fresh_until=retrieved + timedelta(seconds=self.qualification.maximum_age_seconds),
            critical=critical,
            payload=payload,
        )
