"""Explicit research-use scope; personal execution never grants licence rights."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from money.schemas.contracts import Contract

USAGE_POLICY_VERSION: Literal["money-provider-usage-v1"] = "money-provider-usage-v1"


class UsageMode(StrEnum):
    PERSONAL_RESEARCH = "PERSONAL_RESEARCH"
    HOSTED_COMMERCIAL_PRODUCTION = "HOSTED_COMMERCIAL_PRODUCTION"


def usage_mode(environ: Mapping[str, str]) -> UsageMode:
    """Require opt-in even on a developer machine; deployment flags cannot opt in."""
    selected = environ.get("MONEY_USAGE_MODE", "hosted_commercial_production")
    try:
        return UsageMode(selected.strip().upper())
    except ValueError:
        raise ValueError("MONEY_USAGE_MODE_INVALID") from None


def require_local_personal_inference(inference: object, selected_usage: str) -> None:
    """Unverified personal-use source content may not leave the local workflow.

    Unknown transports cannot establish this boundary. OS/native enforcement is
    still independently required; this check does not qualify a native runtime.
    """
    if selected_usage != UsageMode.PERSONAL_RESEARCH:
        return
    from money.research.inference import HTTPInference, inference_endpoint

    if type(inference) is not HTTPInference or inference.configuration.endpoint_scope != "local":
        raise ValueError("PERSONAL_USE_REMOTE_INFERENCE_SHARING_FORBIDDEN")
    inference_endpoint(inference.configuration.endpoint, "local")


class PersonalUseAudit(Contract):
    """Restrictions, attribution and uncertainty, never an approval or signature."""

    policy_version: Literal["money-provider-usage-v1"] = USAGE_POLICY_VERSION
    usage_mode: Literal[UsageMode.PERSONAL_RESEARCH] = UsageMode.PERSONAL_RESEARCH
    rights_status: Literal["UNVERIFIED_PERSONAL_USE"] = "UNVERIFIED_PERSONAL_USE"
    provider: str = Field(min_length=1)
    datasets: tuple[str, ...] = Field(min_length=1)
    endpoints: tuple[str, ...] = Field(min_length=1)
    redistribution: Literal[False] = False
    public_raw_display: Literal[False] = False
    resale: Literal[False] = False
    external_sharing: Literal[False] = False
    attribution: str = Field(min_length=1)
    source_references: tuple[str, ...] = ()

    @field_validator("endpoints", "source_references")
    @classmethod
    def no_credential_urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value.strip():
                raise ValueError("PERSONAL_USE_SOURCE_REFERENCE_INVALID")
            parsed = urlsplit(value)
            if parsed.query or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("PERSONAL_USE_SOURCE_REFERENCE_MUST_BE_SECRET_FREE")
        return values

    def require_local_use(self, provider: str, dataset: str) -> None:
        if self.provider != provider or dataset not in self.datasets:
            raise ValueError("PERSONAL_USE_AUDIT_COVERAGE_MISSING")

    def require_release(self) -> None:
        """Every public/commercial/export boundary must reject uncertain rights."""
        raise ValueError("PERSONAL_USE_PUBLIC_COMMERCIAL_REDISTRIBUTION_FORBIDDEN")
