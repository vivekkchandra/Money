"""Stable, bounded failure categories independent from provider response text."""

from dataclasses import dataclass

import httpx

from money.data.security import ProviderFailure


@dataclass(frozen=True)
class Failure:
    code: str
    retryable: bool
    message: str


def classify_failure(error: BaseException) -> Failure:
    if isinstance(error, ProviderFailure):
        code = (
            error.code
            if error.code
            in {
                "PROVIDER_UNAVAILABLE",
                "PROVIDER_TIMEOUT",
                "PROVIDER_COVERAGE_MISSING",
                "CRITICAL_DATA_MISSING",
                "CRITICAL_DATA_STALE",
                "CRITICAL_DATA_CONFLICT",
                "PIT_VIOLATION",
                "ELIGIBILITY_UNKNOWN",
                "ELIGIBILITY_STALE",
            }
            else "PROVIDER_UNAVAILABLE"
        )
        return Failure(
            code,
            error.retryable and code in {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"},
            "A research provider could not supply qualified evidence.",
        )
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return Failure("PROVIDER_TIMEOUT", True, "A research provider timed out.")
    if isinstance(error, (ConnectionError, httpx.NetworkError)):
        return Failure("PROVIDER_UNAVAILABLE", True, "A research provider is unavailable.")
    if isinstance(error, httpx.HTTPStatusError) and (
        error.response.status_code == 429 or error.response.status_code >= 500
    ):
        return Failure("PROVIDER_UNAVAILABLE", True, "A research provider is unavailable.")
    return Failure("RESEARCH_FAILED", False, "Research could not be completed safely.")
