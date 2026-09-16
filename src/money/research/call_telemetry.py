"""Bounded, content-free inference receipts across native process boundaries.

Only measurements cross this channel. It never carries prompts, provider
responses, database access, customer identity or credentials into a native firm.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from money.schemas.contracts import Contract


class InferenceReceipt(Contract):
    provider: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")
    duration_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)
    status: Literal["SUCCEEDED", "FAILED"]
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    estimated_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    actual_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: Literal["GBP", "USD", "EUR"] | None = None
    provider_calls: int = Field(default=1, ge=0, le=1, strict=True)
    cache_hit: bool | None = None
    retry: bool = False
    error_code: Literal["INFERENCE_FAILED", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"] | None = (
        None
    )

    @model_validator(mode="after")
    def cost_currency(self) -> InferenceReceipt:
        if (
            self.actual_cost is not None or self.estimated_cost is not None
        ) and self.currency is None:
            raise ValueError("CALL_COST_CURRENCY_REQUIRED")
        if self.status == "SUCCEEDED" and self.error_code is not None:
            raise ValueError("SUCCESS_CANNOT_HAVE_CALL_ERROR")
        return self


_receipts: ContextVar[list[InferenceReceipt] | None] = ContextVar(
    "money_call_receipts", default=None
)


@contextmanager
def capture_calls() -> Iterator[list[InferenceReceipt]]:
    """Create an isolated receipt list; copied threads/processes get no DB capability."""
    values: list[InferenceReceipt] = []
    token = _receipts.set(values)
    try:
        yield values
    finally:
        _receipts.reset(token)


def emit_calls(values: Sequence[InferenceReceipt]) -> None:
    """Append only validated accounting facts, with an explicit transport bound."""
    destination = _receipts.get()
    if destination is None:
        return
    if len(destination) + len(values) > 256:
        raise ValueError("CALL_TELEMETRY_LIMIT")
    destination.extend(values)
