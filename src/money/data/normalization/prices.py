"""Exact quote normalization; GBX denotes pence, not pounds."""

from decimal import Decimal


def price_gbp(value: Decimal, currency: str) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("price must be finite and positive")
    if currency == "GBP":
        return value
    if currency == "GBX":
        return value / Decimal("100")
    raise ValueError("unsupported quote currency")
