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
        source_code = {
            "RND_PROVIDER_TIMEOUT": "PROVIDER_TIMEOUT",
            "RND_PROVIDER_RATE_LIMIT": "PROVIDER_RATE_LIMITED",
            "RND_PROVIDER_UNAVAILABLE": "MARKET_DATA_UNAVAILABLE",
            "RND_PROVIDER_CAPACITY": "PROVIDER_BUSY",
            "RND_PROVIDER_CIRCUIT_OPEN": "PROVIDER_BUSY",
            "RND_STALE_DATA": "STALE_DATA",
            "RND_SYMBOL_INVALID": "INSTRUMENT_NOT_FOUND",
            "RND_HISTORY_MISSING": "CRITICAL_DATA_MISSING",
            "RND_MARKET_DATA_MISSING": "CRITICAL_DATA_MISSING",
            "RND_PROVIDER_CONFLICT": "CRITICAL_DATA_CONFLICT",
            "RND_RUNTIME_UNAVAILABLE": "PROVIDER_COVERAGE_MISSING",
            "RND_CACHE_CORRUPT": "CRITICAL_DATA_CONFLICT",
        }.get(error.code, error.code)
        code = (
            source_code
            if source_code
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
                "MARKET_DATA_UNAVAILABLE",
                "PROVIDER_RATE_LIMITED",
                "PROVIDER_BUSY",
                "INSTRUMENT_NOT_FOUND",
                "STALE_DATA",
            }
            else "PROVIDER_UNAVAILABLE"
        )
        return Failure(
            code,
            error.retryable and code in {
                "PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMITED",
                "PROVIDER_BUSY", "MARKET_DATA_UNAVAILABLE",
            },
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
