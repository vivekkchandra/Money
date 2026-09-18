"""Real, provider-neutral market history for research (not release qualification).

Company enrichment and market history are deliberately separate. A provider
failure is recorded and the next explicitly configured provider is tried. A
provider symbol is never manufactured from a broker ticker. Current knowledge
of old prices is not evidence of their original historical availability.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, Protocol, Self

from pydantic import AwareDatetime, Field, model_validator

from money.data.rnd_market import RndMarketSnapshot, YFinanceProvider
from money.data.security import ProviderFailure
from money.qualification.core import QualificationContext
from money.schemas.contracts import Contract, EvidenceRecord, PriceBar, content_hash, utc_now
from money.usage_policy import UsageMode, usage_mode


class MarketProof(Contract):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    path: str = Field(min_length=1)


class ExactMarketMapping(Contract):
    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,49}$")
    trading212_id: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=100)
    currency: Literal["GBP", "GBX"]
    isin: str | None = None
    evidence: tuple[MarketProof, ...] = Field(min_length=1)


class HistoricalMarketBar(Contract):
    timestamp: AwareDatetime
    price: PriceBar
    # None means unknown. In particular, a provider's retrieval time is not
    # relabelled as the original publication of a historical closing price.
    available_at: AwareDatetime | None = None


class HistoricalMarketData(Contract):
    scope: Literal["RESEARCH_ONLY"] = "RESEARCH_ONLY"
    mapping: ExactMarketMapping
    bars: tuple[HistoricalMarketBar, ...] = Field(min_length=1, max_length=100000)
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    proof_refs: tuple[MarketProof, ...] = Field(min_length=1)
    historical_availability_verified: bool = False
    historical_availability_proofs: tuple[MarketProof, ...] = ()
    corporate_actions_complete: bool = False
    adjustment_basis: Literal[
        "UNKNOWN", "RAW_PRICES_VOLUME_SPLIT_ADJUSTED", "RAW_NO_ACTIONS", "SPLIT_ADJUSTED_TOTAL_RETURN"
    ] = "UNKNOWN"
    limitations: tuple[str, ...] = ()
    hash: str = ""

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.valid_until <= self.observed_at:
            raise ValueError("MARKET_DATA_FRESHNESS_INVALID")
        timestamps = [bar.timestamp for bar in self.bars]
        if timestamps != sorted(set(timestamps)):
            raise ValueError("MARKET_DATA_SESSIONS_CONFLICTING")
        if timestamps[-1] > self.observed_at:
            raise ValueError("MARKET_DATA_FUTURE_OBSERVATION")
        if any(bar.price.currency != self.mapping.currency for bar in self.bars):
            raise ValueError("MARKET_DATA_CURRENCY_MISMATCH")
        if any(bar.available_at and bar.available_at > self.observed_at for bar in self.bars):
            raise ValueError("MARKET_DATA_FUTURE_AVAILABILITY")
        if self.historical_availability_verified and (
            not self.historical_availability_proofs
            or any(bar.available_at is None for bar in self.bars)
        ):
            raise ValueError("MARKET_DATA_HISTORICAL_AVAILABILITY_PROOF_REQUIRED")
        if self.adjustment_basis == "RAW_NO_ACTIONS" and not self.corporate_actions_complete:
            raise ValueError("MARKET_DATA_ACTION_COVERAGE_REQUIRED")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("MARKET_DATA_HASH_MISMATCH")
        object.__setattr__(self, "hash", digest)
        return self

    def evidence_records(self, snapshot_id: str) -> tuple[EvidenceRecord, ...]:
        """Freeze real bars without fabricating historical publication times."""
        return tuple(
            EvidenceRecord(
                evidence_id="market-" + content_hash({"data": self.hash, "bar": index}),
                snapshot_id=snapshot_id,
                source=f"{self.mapping.provider}: {self.adjustment_basis}",
                provider=self.mapping.provider,
                source_id=f"{self.mapping.symbol}:{bar.timestamp.isoformat()}",
                canonical_source_id=(
                    f"{self.mapping.provider}:{self.mapping.symbol}:{bar.timestamp.isoformat()}"
                ),
                observation_time=bar.timestamp,
                publication_time=bar.available_at,
                retrieval_time=self.observed_at,
                fresh_until=self.valid_until,
                pit_safe=self.historical_availability_verified,
                payload=bar.price,
            )
            for index, bar in enumerate(self.bars)
        )


class MarketDataRequest(Contract):
    trading212_id: str = Field(min_length=1, max_length=100)
    currency: Literal["GBP", "GBX"]
    as_of: AwareDatetime
    mappings: tuple[ExactMarketMapping, ...] = ()


class MarketDataAttempt(Contract):
    provider: str
    status: Literal["AVAILABLE", "UNAVAILABLE", "NOT_CONFIGURED", "ACCESS_DENIED", "STALE"]
    code: str


class MarketDataAcquisition(Contract):
    dataset: HistoricalMarketData | None = None
    attempts: tuple[MarketDataAttempt, ...] = ()


class MarketDataFailure(ValueError):
    """Only fixed safe codes, never provider exception messages or request URLs."""

    def __init__(
        self,
        code: Literal[
            "EXACT_MARKET_MAPPING_REQUIRED", "MARKET_HISTORY_NOT_CONFIGURED",
            "MARKET_HISTORY_UNAVAILABLE", "MARKET_HISTORY_ACCESS_DENIED", "MARKET_HISTORY_STALE",
            "MARKET_HISTORY_INTEGRITY_FAILED", "MARKET_HISTORY_INVALID",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


class MarketDataProvider(Protocol):
    provider: str

    def history(self, request: MarketDataRequest) -> HistoricalMarketData: ...


def acquire_market_data(
    request: MarketDataRequest, providers: Sequence[MarketDataProvider]
) -> MarketDataAcquisition:
    """Try configured providers in supplied order, rejecting corrupt/mismatched data."""
    attempts: list[MarketDataAttempt] = []
    for provider in providers:
        try:
            dataset = HistoricalMarketData.model_validate_json(
                provider.history(request).model_dump_json()
            )
            if (
                dataset.mapping.provider != provider.provider
                or dataset.mapping.trading212_id != request.trading212_id
                or dataset.mapping.currency != request.currency
                or dataset.mapping not in request.mappings
            ):
                raise MarketDataFailure("MARKET_HISTORY_INVALID")
            if not dataset.observed_at <= request.as_of < dataset.valid_until:
                raise MarketDataFailure("MARKET_HISTORY_STALE")
        except MarketDataFailure as error:
            statuses: dict[str, Any] = {
                "MARKET_HISTORY_ACCESS_DENIED": "ACCESS_DENIED",
                "MARKET_HISTORY_NOT_CONFIGURED": "NOT_CONFIGURED",
                "MARKET_HISTORY_STALE": "STALE",
            }
            attempts.append(MarketDataAttempt(
                provider=provider.provider,
                status=statuses.get(error.code, "UNAVAILABLE"),
                code=error.code,
            ))
        except (ValueError, TypeError, KeyError, OSError, TimeoutError):
            # Opaque transport/parser exceptions can contain credentials.
            attempts.append(MarketDataAttempt(
                provider=provider.provider, status="UNAVAILABLE", code="MARKET_HISTORY_UNAVAILABLE"
            ))
        else:
            attempts.append(MarketDataAttempt(
                provider=provider.provider, status="AVAILABLE", code="REAL_MARKET_HISTORY_AVAILABLE"
            ))
            return MarketDataAcquisition(dataset=dataset, attempts=tuple(attempts))
    return MarketDataAcquisition(attempts=tuple(attempts))


class CachedEODHDMarketData:
    """Reuse Money's hash-checked raw responses; no Fundamentals or network request.

    ISIN/currency/type plus exact returned symbol and company/ticker corroboration
    establish the mapping. Missing mapping blocks *these prices*, not research
    admission. Daily retrieval supplies current knowledge only, not archived PIT.
    """

    provider = "eodhd"

    def __init__(self, ctx: QualificationContext, row: Mapping[str, Any]) -> None:
        self.ctx, self.row = ctx, row

    def _responses(self) -> list[tuple[dict[str, Any], MarketProof]]:
        responses = []
        references = self.row.get("provider_evidence", [])
        if not isinstance(references, list) or len(references) > 1000:
            raise MarketDataFailure("MARKET_HISTORY_INTEGRITY_FAILED")
        for reference in references:
            try:
                proof = MarketProof.model_validate(reference)
                raw = self.ctx.verify_artifact(proof.sha256, proof.path)
                response = json.loads(raw)
            except (ValueError, TypeError, KeyError, OSError):
                raise MarketDataFailure("MARKET_HISTORY_INTEGRITY_FAILED") from None
            if isinstance(response, dict) and response.get("version") == "money-bulk-provider-response-v2":
                responses.append((response, proof))
        return responses

    def mapping(self) -> ExactMarketMapping:
        identities: dict[str, tuple[dict[str, Any], MarketProof]] = {}
        isin = self.row.get("isin")
        if not isinstance(isin, str) or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
            raise MarketDataFailure("EXACT_MARKET_MAPPING_REQUIRED")
        for envelope, proof in self._responses():
            request = envelope.get("request", {})
            if request.get("host") != "eodhd.com" or request.get("path") != "/api/search/" + isin:
                continue
            observed = datetime.fromisoformat(envelope["observed_at"])
            until = min(datetime.fromisoformat(envelope["valid_until"]), observed + timedelta(days=1))
            if not observed <= self.ctx.now < until:
                continue
            rows = envelope.get("response")
            if not isinstance(rows, list) or len(rows) >= 100:
                continue
            for item in rows:
                if not isinstance(item, dict):
                    continue
                code, venue = item.get("Code"), item.get("Exchange")
                if (
                    item.get("ISIN") != isin
                    or item.get("Currency") != self.row.get("quote_currency")
                    or str(item.get("Type", "")).casefold() not in {"stock", "common stock", "ordinary shares"}
                    or not isinstance(code, str)
                    or not isinstance(venue, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,25}\.[A-Za-z0-9_-]{1,20}", code + "." + venue)
                ):
                    continue
                def clean(value: Any) -> str:
                    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
                if not (
                    code.upper() == str(self.row.get("short_ticker", "")).upper()
                    or (clean(item.get("Name")) and clean(item.get("Name")) == clean(self.row.get("name")))
                ):
                    continue
                symbol = code + "." + venue
                if symbol in identities and identities[symbol][0] != item:
                    raise MarketDataFailure("EXACT_MARKET_MAPPING_REQUIRED")
                identities[symbol] = item, proof
        if len(identities) != 1:
            raise MarketDataFailure("EXACT_MARKET_MAPPING_REQUIRED")
        symbol, (_, proof) = next(iter(identities.items()))
        if self.row.get("eodhd_symbol") not in (None, symbol):
            raise MarketDataFailure("EXACT_MARKET_MAPPING_REQUIRED")
        return ExactMarketMapping(
            provider="eodhd", trading212_id=self.row["trading212_id"], symbol=symbol,
            currency=self.row["quote_currency"], isin=isin, evidence=(proof,),
        )

    def history(self, request: MarketDataRequest) -> HistoricalMarketData:
        mapping = self.mapping()
        if mapping not in request.mappings:
            raise MarketDataFailure("EXACT_MARKET_MAPPING_REQUIRED")
        matches = []
        actions: dict[str, tuple[dict[str, Any], MarketProof]] = {}
        stale = False
        for envelope, proof in self._responses():
            source_request = envelope.get("request", {})
            if source_request.get("host") != "eodhd.com":
                continue
            path = source_request.get("path")
            observed = datetime.fromisoformat(envelope["observed_at"])
            until = min(datetime.fromisoformat(envelope["valid_until"]), observed + timedelta(days=1))
            current = observed <= request.as_of < until
            if path == "/api/eod/" + mapping.symbol:
                if current:
                    matches.append((envelope, proof, observed, until))
                else:
                    stale = True
            elif current and path in {
                "/api/splits/" + mapping.symbol, "/api/div/" + mapping.symbol
            }:
                actions[path] = envelope, proof
        if not matches:
            raise MarketDataFailure("MARKET_HISTORY_STALE" if stale else "MARKET_HISTORY_UNAVAILABLE")
        envelope, proof, observed, until = max(matches, key=lambda match: match[2])
        rows = envelope.get("response")
        if not isinstance(rows, list) or not rows or len(rows) > 10000:
            raise MarketDataFailure("MARKET_HISTORY_INVALID")
        bars = []
        for row in rows:
            day = datetime.strptime(row["date"], "%Y-%m-%d").date()
            timestamp = datetime.combine(day, time.max, UTC)
            if timestamp > observed:
                # A partial current session is not a historical daily bar.
                continue
            bars.append(HistoricalMarketBar(
                timestamp=timestamp,
                price=PriceBar.model_validate({
                    key: row[key] for key in ("open", "high", "low", "close", "volume")
                } | {"currency": mapping.currency}),
            ))
        if not bars:
            raise MarketDataFailure("MARKET_HISTORY_INVALID")
        bars.sort(key=lambda bar: bar.timestamp)
        proofs = [proof]
        no_actions = len(actions) == 2
        for action, action_proof in actions.values():
            query = dict(action["request"]["query"])
            no_actions &= (
                isinstance(action.get("response"), list)
                and not action["response"]
                and query.get("from", "9999") <= bars[0].timestamp.date().isoformat()
                and query.get("to", "") >= observed.date().isoformat()
            )
            proofs.append(action_proof)
        return HistoricalMarketData(
            mapping=mapping, bars=tuple(bars), observed_at=observed, valid_until=until,
            proof_refs=tuple(proofs), corporate_actions_complete=no_actions,
            adjustment_basis="RAW_NO_ACTIONS" if no_actions else "RAW_PRICES_VOLUME_SPLIT_ADJUSTED",
            limitations=(
                "Original historical publication/availability is not supplied by this current retrieval.",
                "Provider OHLC is raw; volume may be split-adjusted. Unknown action coverage is not clearance.",
            ),
        )


def load_cached_market_data(
    ctx: QualificationContext, row: Mapping[str, Any]
) -> MarketDataAcquisition:
    """Small qualification-runner integration seam, without network or manual review."""
    provider = CachedEODHDMarketData(ctx, row)
    try:
        mapping = provider.mapping()
    except (ValueError, TypeError, KeyError, OSError):
        return MarketDataAcquisition(attempts=(MarketDataAttempt(
            provider="eodhd", status="UNAVAILABLE", code="EXACT_MARKET_MAPPING_REQUIRED"
        ),))
    request = MarketDataRequest(
        trading212_id=row["trading212_id"], currency=row["quote_currency"],
        as_of=ctx.now, mappings=(mapping,),
    )
    return acquire_market_data(request, (provider,))


class ResearchMarketConfiguration(Contract):
    """Explicit provider order and symbols; no ticker-suffix inference or approval."""

    schema_version: Literal["money-research-market-data-v1"] = "money-research-market-data-v1"
    providers: tuple[Literal["eodhd", "yfinance"], ...] = ("eodhd", "yfinance")
    yfinance_symbols: dict[str, str] = Field(default_factory=dict)
    yfinance_period: Literal["1mo", "3mo", "6mo", "1y", "2y"] = "1y"
    cache_maximum_age_seconds: int = Field(default=3600, ge=60, le=86400)
    timeout_seconds: int = Field(default=30, ge=1, le=60)

    @model_validator(mode="after")
    def valid_configuration(self) -> Self:
        if not self.providers or len(self.providers) != len(set(self.providers)):
            raise ValueError("RESEARCH_MARKET_PROVIDER_ORDER_INVALID")
        for broker_id, symbol in self.yfinance_symbols.items():
            if (
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", broker_id)
                or not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,31}", symbol)
                or symbol == "DEMO.L"
            ):
                raise ValueError("RESEARCH_MARKET_SYMBOL_INVALID")
        return self


class YFinanceResearchMarketData:
    """Bounded personal market transport with an explicitly selected exact symbol.

    This reuses the existing provider implementation, not the ``live_rnd``
    application mode. A configured symbol alone is insufficient: the returned
    exact symbol, canonical ticker, company name and currency must corroborate
    current broker metadata. Yahoo does not supply ISIN here, so no ISIN proof
    is asserted. Historical availability/action completeness remain unknown.
    """

    provider = "yfinance"

    def __init__(
        self,
        ctx: QualificationContext,
        row: Mapping[str, Any],
        configuration: ResearchMarketConfiguration,
        *,
        allow_network: bool,
    ) -> None:
        self.ctx, self.row, self.configuration = ctx, row, configuration
        self.allow_network = allow_network
        self._dataset: HistoricalMarketData | None = None

    def prepare(self) -> HistoricalMarketData:
        if usage_mode(self.ctx.environ) != UsageMode.PERSONAL_RESEARCH:
            raise MarketDataFailure("MARKET_HISTORY_NOT_CONFIGURED")
        symbol = self.configuration.yfinance_symbols.get(str(self.row.get("trading212_id", "")))
        if not symbol:
            raise MarketDataFailure("MARKET_HISTORY_NOT_CONFIGURED")
        identity = {
            "version": "money-yfinance-research-history-v1",
            "trading212_id": self.row.get("trading212_id"),
            "short_ticker": self.row.get("short_ticker"),
            "name": self.row.get("name"),
            "currency": self.row.get("quote_currency"),
            "symbol": symbol,
            "period": self.configuration.yfinance_period,
        }
        key = content_hash(identity)
        checkpoint = self.ctx.cache(
            "research-yfinance-" + key, key, self.configuration.cache_maximum_age_seconds
        )
        if checkpoint is not None:
            proof = MarketProof.model_validate(checkpoint["proof"])
            captured = RndMarketSnapshot.model_validate_json(
                self.ctx.verify_artifact(proof.sha256, proof.path)
            )
        elif not self.allow_network:
            raise MarketDataFailure("MARKET_HISTORY_UNAVAILABLE")
        else:
            try:
                captured = YFinanceProvider(
                    timeout_seconds=self.configuration.timeout_seconds,
                    maximum_calls_per_minute=6,
                ).snapshot(symbol, self.configuration.yfinance_period)
            except ProviderFailure as error:
                raise MarketDataFailure(
                    "MARKET_HISTORY_ACCESS_DENIED"
                    if error.http_status in {401, 402, 403}
                    else "MARKET_HISTORY_UNAVAILABLE"
                ) from None
            captured = RndMarketSnapshot.model_validate_json(captured.model_dump_json())
            clock = utc_now()
            if captured.retrieved_at > clock or clock < self.ctx.now:
                raise MarketDataFailure("MARKET_HISTORY_INVALID")
            self.ctx.now = clock
            digest, path = self.ctx.artifact(captured.model_dump(mode="json"))
            proof = MarketProof(sha256=digest, path=path)
            self.ctx.checkpoint(
                "research-yfinance-" + key, key,
                {"proof": proof.model_dump(mode="json")}, artifacts=((digest, path),),
            )
        instrument = captured.instrument

        def normalized_name(value: Any) -> str:
            return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

        if (
            instrument.provider_symbol != symbol
            or instrument.canonical_symbol != str(self.row.get("short_ticker", "")).upper()
            or not normalized_name(self.row.get("name"))
            or normalized_name(instrument.company) != normalized_name(self.row.get("name"))
            or instrument.currency != self.row.get("quote_currency")
            or self.row.get("instrument_type") != "STOCK"
        ):
            raise MarketDataFailure("EXACT_MARKET_MAPPING_REQUIRED")
        valid_until = captured.retrieved_at + timedelta(
            seconds=self.configuration.cache_maximum_age_seconds
        )
        if not captured.retrieved_at <= self.ctx.now < valid_until:
            raise MarketDataFailure("MARKET_HISTORY_STALE")
        mapping = ExactMarketMapping(
            provider="yfinance", trading212_id=self.row["trading212_id"],
            symbol=instrument.provider_symbol, currency=self.row["quote_currency"],
            isin=None, evidence=(proof,),
        )
        self._dataset = HistoricalMarketData(
            mapping=mapping,
            bars=tuple(HistoricalMarketBar(
                timestamp=bar.timestamp,
                price=PriceBar.model_validate({
                    name: getattr(bar, name) for name in ("open", "high", "low", "close", "volume")
                } | {"currency": mapping.currency}),
            ) for bar in captured.history),
            observed_at=captured.retrieved_at, valid_until=valid_until,
            proof_refs=(proof,), adjustment_basis="UNKNOWN",
            limitations=(
                "Explicit returned symbol/canonical ticker/exact company/currency corroboration; this provider did not supply an ISIN.",
                "Provider-as-returned historical prices; original historical availability and corporate-action completeness are not verified.",
                "Personal research only; no commercial/public redistribution or production qualification.",
            ),
        )
        return self._dataset

    def history(self, request: MarketDataRequest) -> HistoricalMarketData:
        if self._dataset is None:
            raise MarketDataFailure("MARKET_HISTORY_UNAVAILABLE")
        return self._dataset


def load_research_market_data(
    ctx: QualificationContext,
    row: Mapping[str, Any],
    *,
    allow_network: bool = False,
) -> MarketDataAcquisition:
    """Configured real-provider fallback; bulk screens are cache-only by default.

    Only a bounded selected deep-research candidate should enable network calls.
    Symbols in the optional configuration are operator selections, not claims of
    identity approval: the live provider response must still corroborate them.
    """
    ctx.template(
        "inputs/research-market-data.json", ResearchMarketConfiguration().model_dump(mode="json")
    )
    ctx.template(
        "inputs/research-market-data.schema.json", ResearchMarketConfiguration.model_json_schema()
    )
    try:
        configuration = ResearchMarketConfiguration.model_validate(
            ctx.read_json("inputs/research-market-data.json")
        )
    except (ValueError, TypeError, OSError):
        # Invalid optional acquisition configuration supplies no prices. It
        # cannot turn broker-only research into a company/provider prerequisite.
        return MarketDataAcquisition(attempts=(MarketDataAttempt(
            provider="market-data", status="UNAVAILABLE", code="MARKET_CONFIGURATION_INVALID",
        ),))
    attempts: list[MarketDataAttempt] = []
    for provider_name in configuration.providers:
        if provider_name == "eodhd":
            result = load_cached_market_data(ctx, row)
        else:
            provider = YFinanceResearchMarketData(
                ctx, row, configuration, allow_network=allow_network
            )
            try:
                dataset = provider.prepare()
            except MarketDataFailure as error:
                status: Any = {
                    "MARKET_HISTORY_NOT_CONFIGURED": "NOT_CONFIGURED",
                    "MARKET_HISTORY_ACCESS_DENIED": "ACCESS_DENIED",
                    "MARKET_HISTORY_STALE": "STALE",
                }.get(error.code, "UNAVAILABLE")
                result = MarketDataAcquisition(attempts=(MarketDataAttempt(
                    provider="yfinance", status=status, code=error.code,
                ),))
            except (ValueError, TypeError, KeyError, OSError, TimeoutError):
                result = MarketDataAcquisition(attempts=(MarketDataAttempt(
                    provider="yfinance", status="UNAVAILABLE", code="MARKET_HISTORY_UNAVAILABLE",
                ),))
            else:
                result = acquire_market_data(MarketDataRequest(
                    trading212_id=row["trading212_id"], currency=row["quote_currency"],
                    as_of=ctx.now, mappings=(dataset.mapping,),
                ), (provider,))
        attempts.extend(result.attempts)
        if result.dataset is not None:
            return MarketDataAcquisition(dataset=result.dataset, attempts=tuple(attempts))
    return MarketDataAcquisition(attempts=tuple(attempts))
