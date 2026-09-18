"""Explicit issuer evidence route, independent of licensing and market policy."""

from collections.abc import Mapping
from enum import StrEnum

ISSUER_SOURCE_POLICY_VERSION = "money-issuer-source-policy-v1"


class IssuerSourcePolicy(StrEnum):
    COMPANIES_HOUSE = "companies_house"
    OFFICIAL_DISCLOSURES = "official_disclosures"


def issuer_source_policy(environ: Mapping[str, str]) -> IssuerSourcePolicy:
    """Retain legacy defaults; an alternative must be explicitly selected."""
    try:
        return IssuerSourcePolicy(environ.get("MONEY_ISSUER_SOURCE_POLICY", "companies_house"))
    except ValueError:
        raise ValueError("ISSUER_SOURCE_POLICY_INVALID") from None
