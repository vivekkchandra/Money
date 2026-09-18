"""Personal R&D Yahoo adapter. Never production-qualified or a live broker authority.

All native I/O runs in a bounded child process; only Money contracts cross that
boundary. History is provider-as-returned with yfinance adjustment/repair off,
not a claim that Yahoo supplies original, unadjusted point-in-time prices.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, TypeVar, cast
from urllib.parse import urlsplit

from pydantic import AwareDatetime, ConfigDict, Field, ValidationError, model_validator

from money.data.security import ProviderFailure, untrusted_text
from money.schemas.contracts import Contract, content_hash, utc_now

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_UK_EXCHANGES = frozenset({"LSE", "LON", "XLON", "LONDON"})
_US_EXCHANGES = frozenset(
    {"NYQ", "NMS", "NGM", "NCM", "NAS", "ASE", "NYS", "NYSE", "NASDAQ", "AMEX", "PCX", "BTS"}
)
_PERIODS = frozenset({"1mo", "3mo", "6mo", "1y", "2y"})
_LIMITATIONS = (
    "Personal research and development only; not production-qualified market data.",
    "Live broker membership and material ethical exposure have not been verified.",
    "Yahoo prices may be delayed or revised; original historical publication times are unknown.",
    "Auto-adjust, back-adjust and repair are disabled; provider bars may already reflect splits.",
    "Dividend and split observations are not a complete corporate-action history.",
    "No commercial redistribution permission or investment recommendation is implied.",
)
_PROCESS_SLOTS = threading.BoundedSemaphore(4)
_MAX_RESULT_BYTES = 2_000_000
_CHILD_FAILURES = frozenset(
    {
        "RND_PROVIDER_RATE_LIMIT",
        "RND_PROVIDER_UNAVAILABLE",
        "RND_RUNTIME_UNAVAILABLE",
        "RND_HISTORY_MISSING",
        "RND_SYMBOL_INVALID",
    }
)
Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]
T = TypeVar("T", bound=Contract)


class RndInstrument(Contract):
    instrument_id: str = Field(pattern=_SYMBOL.pattern)
    ticker: str = Field(pattern=_SYMBOL.pattern)
    provider_symbol: str = Field(pattern=_SYMBOL.pattern)
    canonical_symbol: str = Field(min_length=1, max_length=32)
    company: str = Field(min_length=1, max_length=200)
    exchange: str = Field(min_length=1, max_length=40)
    currency: str = Field(min_length=1, max_length=12)
    listing_country: Literal["GB", "US"]
    instrument_type: Literal["STOCK"] = "STOCK"
    eligibility: Literal["UNKNOWN"] = "UNKNOWN"
    provider: Literal["yfinance"] = "yfinance"
    development_only: Literal[True] = True
    production_qualified: Literal[False] = False

    @model_validator(mode="after")
    def explicit_identity(self) -> RndInstrument:
        expected_country = (
            "GB"
            if self.exchange in _UK_EXCHANGES
            else "US"
            if self.exchange in _US_EXCHANGES
            else None
        )
        canonical = (
            self.ticker[:-2]
            if expected_country == "GB" and self.ticker.endswith(".L")
            else self.ticker
        )
        if (
            self.ticker == "DEMO.L"
            or self.instrument_id != self.ticker
            or self.provider_symbol != self.ticker
            or self.canonical_symbol != canonical
            or self.listing_country != expected_country
            or self.currency not in {"GBP", "GBX", "USD", "UNKNOWN"}
        ):
            raise ValueError("RND_INSTRUMENT_IDENTITY_INVALID")
        return self


class RndSearchPage(Contract):
    instruments: tuple[RndInstrument, ...] = Field(max_length=20)
    provider: Literal["yfinance"] = "yfinance"
    retrieved_at: AwareDatetime
    requested_at: AwareDatetime
    provider_status: Literal["PERSONAL_RND_ONLY"] = "PERSONAL_RND_ONLY"
    limitations: tuple[str, ...] = _LIMITATIONS

    @model_validator(mode="after")
    def ordered_retrieval(self) -> RndSearchPage:
        if self.requested_at > self.retrieved_at:
            raise ValueError("RND_RETRIEVAL_TIMESTAMP_INVALID")
        return self


class RndQuote(Contract):
    raw_price: Decimal = Field(gt=0)
    raw_currency: str
    currency: Literal["GBP", "GBX", "USD"]
    normalized_gbp: Decimal | None
    normalization: Literal["GBP_IDENTITY", "GBX_DIVIDE_100", "NOT_CONVERTED"]
    observed_at: AwareDatetime
    market_timestamp: AwareDatetime
    market_timezone: str | None = Field(default=None, max_length=64)
    requested_at: AwareDatetime
    retrieved_at: AwareDatetime
    freshness: Literal["UNKNOWN"] = "UNKNOWN"
    maximum_age_seconds: int = Field(ge=60, le=1209600)
    source_url: str
    source_field: Literal["regularMarketPrice"] = "regularMarketPrice"

    @model_validator(mode="after")
    def consistent_quote(self) -> RndQuote:
        if (
            self.requested_at > self.retrieved_at
            or self.observed_at != self.market_timestamp
            or self.observed_at > self.retrieved_at
            or (self.retrieved_at - self.observed_at).total_seconds() > self.maximum_age_seconds
        ):
            raise ValueError("RND_QUOTE_TIMESTAMP_INVALID")
        if _currency(self.raw_currency) != self.currency:
            raise ValueError("RND_CURRENCY_CONFLICT")
        expected = (
            self.raw_price / 100
            if self.currency == "GBX"
            else self.raw_price
            if self.currency == "GBP"
            else None
        )
        method = (
            "GBX_DIVIDE_100"
            if self.currency == "GBX"
            else "GBP_IDENTITY"
            if self.currency == "GBP"
            else "NOT_CONVERTED"
        )
        if self.normalized_gbp != expected or self.normalization != method:
            raise ValueError("RND_NORMALIZATION_INVALID")
        return self


class RndBar(Contract):
    timestamp: AwareDatetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    dividends: float = Field(ge=0)
    stock_splits: float = Field(ge=0)

    @model_validator(mode="after")
    def ohlc_order(self) -> RndBar:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("RND_INVALID_OHLC")
        return self


class RndNews(Contract):
    headline: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    published_at: AwareDatetime
    source_id: str = Field(min_length=1, max_length=200)
    content_hash: str


class RndCorporateAction(Contract):
    kind: Literal["DIVIDEND", "SPLIT"]
    timestamp: AwareDatetime
    value: float = Field(gt=0)
    currency: str | None
    historical_availability: Literal["UNKNOWN"] = "UNKNOWN"


class RndMarketSnapshot(Contract):
    model_config = ConfigDict(revalidate_instances="always")
    instrument: RndInstrument
    quote: RndQuote
    history: tuple[RndBar, ...] = Field(min_length=1, max_length=600)
    corporate_actions: tuple[RndCorporateAction, ...] = Field(max_length=1200)
    fundamentals: dict[str, float]
    financial_currency: str | None
    sector: str | None
    industry: str | None
    business_summary: str | None
    news: tuple[RndNews, ...] = Field(max_length=20)
    news_status: Literal["AVAILABLE", "EMPTY", "UNAVAILABLE"]
    fundamentals_status: Literal["AVAILABLE", "EMPTY", "UNAVAILABLE"]
    adjustment: Literal["PROVIDER_AS_RETURNED_NO_AUTO_ADJUST"] = (
        "PROVIDER_AS_RETURNED_NO_AUTO_ADJUST"
    )
    historical_pit_status: Literal["UNVERIFIED_EXCLUDE_FROM_PIT_VALIDATION"] = (
        "UNVERIFIED_EXCLUDE_FROM_PIT_VALIDATION"
    )
    provider: Literal["yfinance"] = "yfinance"
    provider_version: str
    provider_status: Literal["PERSONAL_RND_ONLY"] = "PERSONAL_RND_ONLY"
    retrieved_at: AwareDatetime
    requested_at: AwareDatetime
    content_hash: str
    limitations: tuple[str, ...] = _LIMITATIONS

    @model_validator(mode="after")
    def immutable_identity_and_time(self) -> RndMarketSnapshot:
        instrument = self.instrument
        dates = [bar.timestamp for bar in self.history]
        if (
            instrument.instrument_id != instrument.ticker
            or instrument.ticker != instrument.provider_symbol
            or instrument.currency != self.quote.currency
            or self.requested_at > self.retrieved_at
            or self.quote.requested_at != self.requested_at
            or self.quote.retrieved_at != self.retrieved_at
            or self.quote.source_url
            != f"https://finance.yahoo.com/quote/{instrument.provider_symbol}"
            or len(set(dates)) != len(dates)
            or dates != sorted(dates)
            or any(stamp > self.retrieved_at for stamp in dates)
        ):
            raise ValueError("RND_SNAPSHOT_IDENTITY_OR_TIME_INVALID")
        digest = content_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash and self.content_hash != digest:
            raise ValueError("RND_SNAPSHOT_HASH_MISMATCH")
        object.__setattr__(self, "content_hash", digest)
        return self


class RndProviderHealth(Contract):
    provider: Literal["yfinance"] = "yfinance"
    status: Literal["READY", "FAILED"]
    scope: Literal["PERSONAL_RND_ONLY"] = "PERSONAL_RND_ONLY"
    checked_at: AwareDatetime
    error_code: str | None = None


class RndMarketProvider(Protocol):
    def search_instruments(self, query: str, limit: int = 10) -> RndSearchPage: ...
    def snapshot(self, ticker: str, period: str = "6mo") -> RndMarketSnapshot: ...
    def health(self, ticker: str = "AAPL") -> RndProviderHealth: ...


def _currency(raw: Any, *, unknown: bool = False) -> str:
    # GBp means pence. Uppercasing first would silently turn it into pounds.
    if raw in {"GBp", "GBX"}:
        return "GBX"
    if raw in {"GBP", "USD"}:
        return str(raw)
    if unknown and not raw:
        return "UNKNOWN"
    raise ProviderFailure("RND_CURRENCY_UNSUPPORTED")


def _symbol(value: str) -> str:
    result = value.strip().upper()
    if not _SYMBOL.fullmatch(result) or result == "DEMO.L":
        raise ProviderFailure("RND_SYMBOL_INVALID")
    return result


def _text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = untrusted_text(value, maximum_characters=maximum)
    return cleaned or None


def _instrument(row: dict[str, Any], *, requested: str | None = None) -> RndInstrument:
    symbol = _symbol(str(row.get("symbol", "")))
    if requested is not None and symbol != requested:
        raise ProviderFailure("RND_SYMBOL_MISMATCH")
    if row.get("quoteType", row.get("instrumentType")) != "EQUITY":
        raise ProviderFailure("RND_INSTRUMENT_UNSUPPORTED")
    exchange = str(row.get("exchange", row.get("exchangeName", ""))).upper()
    if exchange in _UK_EXCHANGES:
        country: Literal["GB", "US"] = "GB"
    elif exchange in _US_EXCHANGES:
        country = "US"
    else:
        raise ProviderFailure("RND_EXCHANGE_UNSUPPORTED")
    company = _text(
        row.get("longName") or row.get("longname") or row.get("shortName") or row.get("shortname"),
        200,
    )
    if company is None:
        raise ProviderFailure("RND_COMPANY_IDENTITY_MISSING")
    return RndInstrument(
        instrument_id=symbol,
        ticker=symbol,
        provider_symbol=symbol,
        canonical_symbol=symbol[:-2] if country == "GB" and symbol.endswith(".L") else symbol,
        company=company,
        exchange=exchange,
        currency=_currency(row.get("currency"), unknown=True),
        listing_country=country,
    )


def _timestamp(value: Any) -> datetime:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result = datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise ValueError
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError, OSError) as error:
        raise ProviderFailure("RND_TIMESTAMP_INVALID") from error


def _quote(
    metadata: dict[str, Any], now: datetime, requested_at: datetime, maximum_age_seconds: int
) -> RndQuote:
    raw_currency = metadata.get("currency")
    currency = _currency(raw_currency)
    try:
        price = Decimal(str(metadata["regularMarketPrice"]))
        timestamp = _timestamp(metadata["regularMarketTime"])
        if timestamp > now or not price.is_finite() or price <= 0:
            raise ValueError
    except (KeyError, ValueError, ArithmeticError) as error:
        raise ProviderFailure("RND_QUOTE_INVALID") from error
    if (now - timestamp).total_seconds() > maximum_age_seconds:
        raise ProviderFailure("RND_STALE_DATA", retryable=True)
    return RndQuote(
        raw_price=price,
        raw_currency=str(raw_currency),
        currency=cast(Literal["GBP", "GBX", "USD"], currency),
        normalized_gbp=price / 100 if currency == "GBX" else price if currency == "GBP" else None,
        normalization="GBX_DIVIDE_100"
        if currency == "GBX"
        else "GBP_IDENTITY"
        if currency == "GBP"
        else "NOT_CONVERTED",
        observed_at=timestamp,
        market_timestamp=timestamp,
        market_timezone=_text(metadata.get("exchangeTimezoneName"), 64),
        requested_at=requested_at,
        retrieved_at=now,
        maximum_age_seconds=maximum_age_seconds,
        source_url=f"https://finance.yahoo.com/quote/{_symbol(metadata['symbol'])}",
    )


def _quote_response(raw: dict[str, Any], ticker: str, maximum_age_seconds: int) -> RndQuote:
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("symbol") != ticker:
        raise ProviderFailure("RND_SYMBOL_MISMATCH")
    if metadata.get("instrumentType") != "EQUITY":
        raise ProviderFailure("RND_INSTRUMENT_UNSUPPORTED")
    return _quote(
        metadata,
        _timestamp(raw["retrieved_at"]),
        _timestamp(raw["requested_at"]),
        maximum_age_seconds,
    )


def _news_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if (
            parts.scheme != "https"
            or not host
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or host == "localhost"
            or "." not in host
            or host.endswith((".local", ".internal", ".localhost"))
            or "\\" in value
            or any(ord(char) < 33 for char in value)
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return None
        return value
    except ValueError:
        return None


def _news(records: Any, now: datetime) -> tuple[RndNews, ...]:
    if not isinstance(records, list) or len(records) > 100:
        raise ProviderFailure("RND_NEWS_INVALID")
    output: list[RndNews] = []
    seen: set[str] = set()
    for raw in records:
        if not isinstance(raw, dict):
            continue
        row = raw.get("content", raw)
        if not isinstance(row, dict):
            continue
        url = row.get("canonicalUrl", row.get("clickThroughUrl", row.get("link")))
        if isinstance(url, dict):
            url = url.get("url")
        url = _news_url(url)
        headline = _text(row.get("title"), 500)
        provider = row.get("provider", row.get("publisher"))
        publisher = _text(
            provider.get("displayName") if isinstance(provider, dict) else provider, 200
        )
        try:
            published = _timestamp(row.get("pubDate", row.get("providerPublishTime")))
        except ProviderFailure:
            continue
        if not url or not headline or not publisher or published > now:
            continue
        fingerprint = content_hash(
            (headline.casefold(), publisher.casefold(), published.isoformat())
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        output.append(
            RndNews(
                headline=headline,
                publisher=publisher,
                url=url,
                published_at=published,
                source_id=_text(row.get("id", raw.get("id", raw.get("uuid"))), 200) or fingerprint,
                content_hash=fingerprint,
            )
        )
        if len(output) == 20:
            break
    return tuple(output)


_FUNDAMENTALS = frozenset(
    {
        "marketCap",
        "trailingPE",
        "forwardPE",
        "priceToBook",
        "totalRevenue",
        "totalCash",
        "totalDebt",
        "operatingCashflow",
        "freeCashflow",
        "profitMargins",
        "returnOnEquity",
        "revenueGrowth",
        "earningsGrowth",
        "sharesOutstanding",
        "debtToEquity",
    }
)


def _snapshot(raw: dict[str, Any], ticker: str, maximum_age_seconds: int) -> RndMarketSnapshot:
    now = _timestamp(raw.get("retrieved_at"))
    requested_at = _timestamp(raw.get("requested_at"))
    metadata, info = raw.get("metadata"), raw.get("info", {})
    if not isinstance(metadata, dict) or not isinstance(info, dict):
        raise ProviderFailure("RND_MARKET_DATA_MISSING")
    for field in ("symbol", "currency"):
        if metadata.get(field) and info.get(field) and metadata[field] != info[field]:
            if field != "currency" or _currency(metadata[field]) != _currency(info[field]):
                raise ProviderFailure("RND_PROVIDER_CONFLICT")
    if (
        metadata.get("exchangeName")
        and info.get("exchange")
        and metadata["exchangeName"] != info["exchange"]
    ) or (
        metadata.get("instrumentType")
        and info.get("quoteType")
        and metadata["instrumentType"] != info["quoteType"]
    ):
        raise ProviderFailure("RND_PROVIDER_CONFLICT")
    identity = {**info, **{key: value for key, value in metadata.items() if value is not None}}
    instrument = _instrument(identity, requested=ticker)
    quote = _quote(identity, now, requested_at, maximum_age_seconds)
    records = raw.get("history")
    if not isinstance(records, list) or not 1 <= len(records) <= 600:
        raise ProviderFailure("RND_HISTORY_MISSING")
    bars = tuple(RndBar.model_validate(record) for record in records)
    dates = [bar.timestamp for bar in bars]
    if len(set(dates)) != len(dates) or dates != sorted(dates) or any(date > now for date in dates):
        raise ProviderFailure("RND_HISTORY_TIMESTAMP_INVALID")
    actions = tuple(
        RndCorporateAction(
            kind=cast(Literal["DIVIDEND", "SPLIT"], kind),
            timestamp=bar.timestamp,
            value=value,
            currency=quote.raw_currency if kind == "DIVIDEND" else None,
        )
        for bar in bars
        for kind, value in (("DIVIDEND", bar.dividends), ("SPLIT", bar.stock_splits))
        if value > 0
    )
    fundamentals = {
        key: float(value)
        for key, value in info.items()
        if key in _FUNDAMENTALS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    }
    news = _news(raw.get("news", []), now)
    values = dict(
        instrument=instrument,
        quote=quote,
        history=bars,
        corporate_actions=actions,
        fundamentals=fundamentals,
        financial_currency=_text(info.get("financialCurrency"), 12),
        sector=_text(info.get("sector"), 100),
        industry=_text(info.get("industry"), 100),
        business_summary=_text(info.get("longBusinessSummary"), 2500),
        news=news,
        news_status="UNAVAILABLE"
        if raw.get("news_unavailable")
        else "AVAILABLE"
        if news
        else "EMPTY",
        fundamentals_status="UNAVAILABLE"
        if raw.get("info_unavailable")
        else "AVAILABLE"
        if fundamentals
        else "EMPTY",
        provider_version=str(raw.get("provider_version", "unknown"))[:40],
        retrieved_at=now,
        requested_at=requested_at,
    )
    return RndMarketSnapshot.model_validate({**values, "content_hash": ""})


class YFinanceProvider:
    """Small personal-use provider with bounded memory cache, coalescing and circuits.

    Local rate limits are an additional safeguard, not a replacement for the API's
    durable user/workspace admission limits across multiple deployed instances.
    The default seven-calendar-day quote ceiling detects dead feeds through
    weekends/holidays; it does not certify trading freshness or real-time prices.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
        search_timeout_seconds: float = 4,
        cache_ttl_seconds: float = 60,
        maximum_calls_per_minute: int = 30,
        maximum_quote_age_seconds: int = 604800,
        transport: Transport | None = None,
    ) -> None:
        if not 1 <= timeout_seconds <= 60 or not 0.1 <= search_timeout_seconds <= 8:
            raise ValueError("RND_TIMEOUT_INVALID")
        if not 0 <= cache_ttl_seconds <= 300 or not 1 <= maximum_calls_per_minute <= 120:
            raise ValueError("RND_BUDGET_INVALID")
        if not 60 <= maximum_quote_age_seconds <= 1209600:
            raise ValueError("RND_FRESHNESS_POLICY_INVALID")
        self.timeout = timeout_seconds
        self.search_timeout = search_timeout_seconds
        self.ttl = cache_ttl_seconds
        self.maximum_calls = maximum_calls_per_minute
        self.maximum_quote_age = maximum_quote_age_seconds
        self.transport = transport or _run_bounded
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, Contract]] = OrderedDict()
        self._inflight: dict[str, Future[Contract]] = {}
        self._calls: deque[float] = deque()
        self._failures = 0
        self._open_until = 0.0

    def _call(
        self,
        operation: str,
        parameters: dict[str, Any],
        timeout: float,
        parse: Callable[[dict[str, Any]], T],
        *,
        cache: bool = True,
    ) -> T:
        key = content_hash((operation, parameters))
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            usable = bool(hit and hit[0] > now)
            if hit and isinstance(hit[1], (RndQuote, RndMarketSnapshot)):
                quote = hit[1] if isinstance(hit[1], RndQuote) else hit[1].quote
                usable = (
                    usable
                    and (utc_now() - quote.observed_at).total_seconds() <= self.maximum_quote_age
                )
            if cache and hit and usable:
                self._cache.move_to_end(key)
                return cast(T, hit[1].model_copy(deep=True))
            if self._open_until > now:
                raise ProviderFailure("RND_PROVIDER_CIRCUIT_OPEN", retryable=True)
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                while self._calls and self._calls[0] <= now - 60:
                    self._calls.popleft()
                if len(self._calls) >= self.maximum_calls or len(self._inflight) >= 4:
                    raise ProviderFailure("RND_PROVIDER_RATE_LIMIT", retryable=True)
                self._calls.append(now)
                future = Future()
                self._inflight[key] = future
        assert future is not None
        if not owner:
            try:
                return cast(T, future.result(timeout=timeout + 0.5).model_copy(deep=True))
            except FutureTimeout as error:
                raise ProviderFailure("RND_PROVIDER_TIMEOUT", retryable=True) from error
        try:
            result = parse(self.transport(operation, parameters, timeout))
            with self._lock:
                self._failures = 0
                if cache and self.ttl:
                    self._cache[key] = (time.monotonic() + self.ttl, result)
                    while len(self._cache) > 32:
                        self._cache.popitem(last=False)
            future.set_result(result)
            return result.model_copy(deep=True)
        except Exception as error:
            failure = (
                error
                if isinstance(error, ProviderFailure)
                else ProviderFailure("RND_PROVIDER_DATA_INVALID")
            )
            with self._lock:
                self._failures += 1
                if self._failures >= 3:
                    self._open_until = time.monotonic() + 30
            future.set_exception(failure)
            if failure is error:
                raise
            raise failure from error
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def search(self, query: str, limit: int = 10) -> RndSearchPage:
        if (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 80
            or any(ord(c) < 32 for c in query)
        ):
            raise ProviderFailure("RND_SEARCH_INVALID")
        if not 1 <= limit <= 20:
            raise ProviderFailure("RND_SEARCH_INVALID")

        def parse(raw: dict[str, Any]) -> RndSearchPage:
            rows = raw.get("quotes")
            if not isinstance(rows, list) or len(rows) > 100:
                raise ProviderFailure("RND_SEARCH_DATA_INVALID")
            instruments: dict[str, RndInstrument] = {}
            for row in rows:
                try:
                    item = _instrument(row)
                except (ProviderFailure, ValidationError, AttributeError):
                    continue
                previous = instruments.get(item.ticker)
                if previous is not None and previous != item:
                    raise ProviderFailure("RND_SEARCH_IDENTITY_CONFLICT")
                instruments[item.ticker] = item
            return RndSearchPage(
                instruments=tuple(instruments.values())[:limit],
                retrieved_at=_timestamp(raw.get("retrieved_at")),
                requested_at=_timestamp(raw.get("requested_at")),
            )

        return self._call(
            "search", {"query": query.strip(), "limit": limit}, self.search_timeout, parse
        )

    def search_instruments(self, query: str, limit: int = 10) -> RndSearchPage:
        return self.search(query, limit)

    def snapshot(self, ticker: str, period: str = "6mo") -> RndMarketSnapshot:
        ticker = _symbol(ticker)
        if period not in _PERIODS:
            raise ProviderFailure("RND_PERIOD_INVALID")
        return self._call(
            "snapshot",
            {"ticker": ticker, "period": period},
            self.timeout,
            lambda raw: _snapshot(raw, ticker, self.maximum_quote_age),
        )

    def get_quote(self, ticker: str) -> RndQuote:
        ticker = _symbol(ticker)
        return self._call(
            "quote",
            {"ticker": ticker},
            self.timeout,
            lambda raw: _quote_response(raw, ticker, self.maximum_quote_age),
        )

    def get_history(self, ticker: str, period: str = "6mo") -> tuple[RndBar, ...]:
        return self.snapshot(ticker, period).history

    def get_corporate_actions(
        self, ticker: str, period: str = "6mo"
    ) -> tuple[RndCorporateAction, ...]:
        return self.snapshot(ticker, period).corporate_actions

    def health(self, ticker: str = "AAPL") -> RndProviderHealth:
        try:
            ticker = _symbol(ticker)
            self._call(
                "quote",
                {"ticker": ticker},
                min(self.timeout, 8),
                lambda raw: _quote_response(raw, ticker, self.maximum_quote_age),
                cache=False,
            )
        except ProviderFailure as error:
            return RndProviderHealth(status="FAILED", checked_at=utc_now(), error_code=error.code)
        return RndProviderHealth(status="READY", checked_at=utc_now())


def _run_bounded(operation: str, parameters: dict[str, Any], timeout: float) -> dict[str, Any]:
    if not _PROCESS_SLOTS.acquire(blocking=False):
        raise ProviderFailure("RND_PROVIDER_CAPACITY", retryable=True)
    try:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "LANG",
                "LC_ALL",
                "TZ",
                "TMPDIR",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
            }
        }
        environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHONUNBUFFERED="1")
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-m", "money.data.rnd_market", "--child"],
                input=json.dumps(
                    {"operation": operation, "parameters": parameters, "timeout": timeout}
                ).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ProviderFailure("RND_PROVIDER_TIMEOUT", retryable=True) from error
        except OSError as error:
            raise ProviderFailure("RND_RUNTIME_UNAVAILABLE", retryable=True) from error
        if completed.returncode or not 0 < len(completed.stdout) <= _MAX_RESULT_BYTES:
            raise ProviderFailure("RND_PROVIDER_UNAVAILABLE", retryable=True)
        try:
            payload = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderFailure("RND_PROVIDER_DATA_INVALID") from error
        if not isinstance(payload, dict):
            raise ProviderFailure("RND_PROVIDER_DATA_INVALID")
        if payload.get("error") in _CHILD_FAILURES:
            raise ProviderFailure(
                payload["error"],
                retryable=payload["error"] not in {"RND_HISTORY_MISSING", "RND_SYMBOL_INVALID"},
            )
        return payload
    finally:
        _PROCESS_SLOTS.release()


def _native(operation: str, parameters: dict[str, Any], timeout: float) -> dict[str, Any]:
    import yfinance as yf  # type: ignore[import-untyped]

    request_timeout = min(8, max(1, timeout - 1))
    common = {"provider_version": yf.__version__, "requested_at": utc_now().isoformat()}
    if operation == "search":
        search = yf.Search(
            parameters["query"],
            max_results=parameters["limit"],
            news_count=0,
            lists_count=0,
            recommended=0,
            include_nav_links=False,
            include_research=False,
            timeout=request_timeout,
            raise_errors=True,
        )
        fields = {"symbol", "shortname", "longname", "exchange", "quoteType", "currency"}
        return {
            **common,
            "quotes": [
                {key: value for key, value in row.items() if key in fields}
                for row in search.quotes[:20]
            ],
            "retrieved_at": utc_now().isoformat(),
        }
    ticker = yf.Ticker(_symbol(parameters["ticker"]))
    history = ticker.history(
        period=parameters.get("period", "5d"),
        interval="1d",
        auto_adjust=False,
        back_adjust=False,
        repair=False,
        actions=True,
        keepna=True,
        rounding=False,
        timeout=request_timeout,
        raise_errors=True,
    )
    metadata_fields = {
        "symbol",
        "longName",
        "shortName",
        "exchangeName",
        "instrumentType",
        "currency",
        "regularMarketPrice",
        "regularMarketTime",
        "exchangeTimezoneName",
    }
    metadata = {
        key: value
        for key, value in ticker.get_history_metadata(repair=False).items()
        if key in metadata_fields
    }
    if operation == "quote":
        return {**common, "metadata": metadata, "retrieved_at": utc_now().isoformat()}
    if operation != "snapshot" or len(history) > 600 or history.empty:
        raise ProviderFailure("RND_HISTORY_MISSING")
    bars = []
    for timestamp, row in history.iterrows():
        values = {
            name: row[column]
            for name, column in (
                ("open", "Open"),
                ("high", "High"),
                ("low", "Low"),
                ("close", "Close"),
                ("volume", "Volume"),
                ("dividends", "Dividends"),
                ("stock_splits", "Stock Splits"),
            )
        }
        bars.append(
            {
                "timestamp": timestamp.isoformat(),
                **{key: float(value) for key, value in values.items()},
            }
        )
    info_unavailable = news_unavailable = False
    try:
        info = ticker.get_info()
        fields = set(_FUNDAMENTALS) | {
            "symbol",
            "longName",
            "shortName",
            "quoteType",
            "exchange",
            "currency",
            "financialCurrency",
            "sector",
            "industry",
            "longBusinessSummary",
        }
        info = {key: value for key, value in info.items() if key in fields}
    except Exception:
        info, info_unavailable = {}, True
    try:
        news = ticker.get_news(count=10, tab="news")[:20]
    except Exception:
        news, news_unavailable = [], True
    return {
        **common,
        "metadata": metadata,
        "info": info,
        "history": bars,
        "news": news,
        "info_unavailable": info_unavailable,
        "news_unavailable": news_unavailable,
        "retrieved_at": utc_now().isoformat(),
    }


def _child() -> None:
    """Fixed operation allowlist; no shell commands, user code or arbitrary URLs."""
    result: dict[str, Any]
    try:
        request = json.loads(sys.stdin.buffer.read(4097))
        if request["operation"] not in {"search", "snapshot", "quote"}:
            raise ValueError
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        resource.setrlimit(resource.RLIMIT_FSIZE, (4_000_000, 4_000_000))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        logging.disable(logging.CRITICAL)
        with tempfile.TemporaryDirectory(prefix="money-yfinance-") as cache:
            with (
                open(os.devnull, "w") as sink,
                contextlib.redirect_stdout(sink),
                contextlib.redirect_stderr(sink),
            ):
                import yfinance as yf

                yf.set_tz_cache_location(cache)
                result = _native(request["operation"], request["parameters"], request["timeout"])
    except ImportError:
        result = {"error": "RND_RUNTIME_UNAVAILABLE"}
    except ProviderFailure as error:
        result = {
            "error": error.code if error.code in _CHILD_FAILURES else "RND_PROVIDER_UNAVAILABLE"
        }
    except Exception as error:
        result = {
            "error": "RND_PROVIDER_RATE_LIMIT"
            if type(error).__name__ == "YFRateLimitError"
            else "RND_PROVIDER_UNAVAILABLE"
        }
    try:
        encoded = json.dumps(result, allow_nan=False).encode()
        if len(encoded) > _MAX_RESULT_BYTES:
            encoded = b'{"error":"RND_PROVIDER_UNAVAILABLE"}'
    except (ValueError, TypeError):
        encoded = b'{"error":"RND_PROVIDER_UNAVAILABLE"}'
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__" and sys.argv[1:] == ["--child"]:
    _child()
