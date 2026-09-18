"""Provider admission records are deployment evidence, not provider marketing."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field

from money.schemas.contracts import Contract
from money.usage_policy import PersonalUseAudit, UsageMode


class ProviderQualification(Contract):
    provider: str
    datasets: tuple[str, ...]
    geography: tuple[str, ...] = ("GB",)
    instrument_types: tuple[str, ...] = ("STOCK",)
    currencies: tuple[str, ...] = ("GBP", "GBX")
    earliest_observation: AwareDatetime
    publication_times: Literal["AS_RETRIEVED", "ORIGINAL_PUBLICATION_VERIFIED"]
    maximum_age_seconds: int = Field(gt=0, le=604800)
    production_qualified: bool = False
    development_only: bool = False
    qualified_by: str | None = None
    qualification_report_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    verified_at: AwareDatetime
    valid_until: AwareDatetime
    usage_purpose: str = "private investment research"
    storage_policy: str = "bounded normalized evidence only"
    redistribution: Literal["PROHIBITED", "UNKNOWN", "FACTS_AND_LINKS", "LICENSED"] = "UNKNOWN"
    attribution: str
    source_documentation: str
    personal_use: PersonalUseAudit | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    def require(
        self, dataset: str, now: datetime, *, historical: bool = False,
        usage_mode: UsageMode = UsageMode.HOSTED_COMMERCIAL_PRODUCTION,
    ) -> None:
        # An environment flag never changes the default at a production caller.
        # Personal permission is explicit at each local data-consumption boundary.
        personal = usage_mode == UsageMode.PERSONAL_RESEARCH and self.personal_use is not None
        if (
            self.development_only
            or self.provider.lower() == "yfinance"
            or not self.qualification_report_hash
        ):
            raise ValueError("PROVIDER_UNQUALIFIED")
        if personal:
            assert self.personal_use is not None
            self.personal_use.require_local_use(self.provider, dataset)
            if self.production_qualified or self.redistribution != "PROHIBITED":
                raise ValueError("PERSONAL_USE_CANNOT_CLAIM_PRODUCTION_RIGHTS")
        elif not self.production_qualified or not self.qualified_by or self.personal_use is not None:
            raise ValueError("PROVIDER_UNQUALIFIED")
        if dataset not in self.datasets or not self.verified_at <= now < self.valid_until:
            raise ValueError("PROVIDER_COVERAGE_MISSING")
        if historical and self.publication_times != "ORIGINAL_PUBLICATION_VERIFIED":
            raise ValueError("PROVIDER_HISTORICAL_AVAILABILITY_UNKNOWN")
