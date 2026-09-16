"""Explicit mappings, never ticker suffix guessing across providers."""

import re
from datetime import datetime

from pydantic import AwareDatetime, Field, model_validator

from money.schemas.contracts import Contract, Ticker


class InstrumentIdentifiers(Contract):
    ticker: Ticker
    company_name: str = Field(min_length=1, max_length=200)
    trading212_id: str = Field(min_length=1, max_length=100)
    exchange_ticker: str = Field(min_length=1, max_length=32)
    exchange: str = Field(min_length=1, max_length=20)
    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    quote_currency: str = Field(pattern=r"^(GBP|GBX)$")
    companies_house_number: str | None = Field(default=None, pattern=r"^[A-Z0-9]{8}$")
    provider_symbols: tuple[tuple[str, str], ...] = ()
    verified_at: AwareDatetime
    valid_until: AwareDatetime
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_mapping(self) -> "InstrumentIdentifiers":
        if any(
            not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,49}", provider)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", symbol)
            for provider, symbol in self.provider_symbols
        ):
            raise ValueError("invalid provider mapping")
        if len({p for p, _ in self.provider_symbols}) != len(self.provider_symbols):
            raise ValueError("ambiguous provider mapping")
        if self.valid_until <= self.verified_at:
            raise ValueError("invalid identifier validity")
        digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in self.isin)
        total = sum(
            (int(c) * (2 if i % 2 else 1)) // 10 + (int(c) * (2 if i % 2 else 1)) % 10
            for i, c in enumerate(reversed(digits))
        )
        if total % 10:
            raise ValueError("invalid ISIN check digit")
        return self

    def require_current(self, at: datetime) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("IDENTIFIER_TIMESTAMP_INVALID")
        if not self.verified_at <= at < self.valid_until:
            raise ValueError("IDENTIFIER_MAPPING_STALE")

    def symbol_for(self, provider: str, at: datetime) -> str:
        self.require_current(at)
        mappings = dict(self.provider_symbols)
        if provider not in mappings:
            raise ValueError("IDENTIFIER_MAPPING_MISSING")
        return mappings[provider]
