"""Exact quote normalization; GBX denotes pence, not pounds."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class NormalizedPrice:
    raw_value: Decimal
    raw_currency: str
    gbp: Decimal
    method: str


def normalize_gbp(value: Decimal, currency: str) -> NormalizedPrice:
    return NormalizedPrice(
        value,
        currency,
        price_gbp(value, currency),
        "GBX_DIVIDE_100" if currency == "GBX" else "GBP_IDENTITY",
    )


def price_gbp(value: Decimal, currency: str) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("price must be finite and positive")
    if currency == "GBP":
        return value
    if currency == "GBX":
        return value / Decimal("100")
    raise ValueError("unsupported quote currency")
