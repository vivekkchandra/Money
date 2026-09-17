"""Commercial licence admission and read-time revocation checks.

These functions do no network I/O and do not import native research runtimes.
Only the trusted application configuration may select synthetic presentation.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from money.reference.catalog import CommercialLicenceError, ReferenceCatalog
from money.schemas.contracts import ResearchSnapshot

if TYPE_CHECKING:
    from money.research.live import LiveManifest


def require_manifest_commercial_rights(
    catalog: ReferenceCatalog,
    manifest: LiveManifest,
    *,
    redistribution: bool = True,
    as_of: date | None = None,
) -> tuple[str, ...]:
    """Check every admitted/embedded source before any commercial live compute.

    The manifest must already have passed Money's hash/qualification validation.
    An archive/reviewer cannot wash away the original providers' licensing.
    """
    providers = {qualification.provider for qualification in manifest.provider_qualifications}
    for item in manifest.reviewed_instruments:
        providers.add(item.metadata.provider)
        providers.add(item.spread_evidence.provider)
        providers.update(record.provider for record in item.supplemental_evidence)
        providers.update(record.provider for record in item.archived_market_evidence)
    if not providers or "money-demo" in providers:
        raise CommercialLicenceError("MISSING_LICENCE")
    for provider in sorted(providers):
        catalog.require_commercial_use(provider, redistribution=redistribution, as_of=as_of)
    return tuple(sorted(providers))


def require_snapshot_commercial_rights(
    catalog: ReferenceCatalog,
    snapshot: ResearchSnapshot,
    *,
    allow_synthetic: bool = False,
    redistribution: bool = True,
    as_of: date | None = None,
) -> tuple[str, ...]:
    """Recheck stored evidence against today's rights before returning a result.

    Call on all customer job/packet/report/evidence views, not only the raw
    evidence endpoint: derived reports can also disclose restricted information.
    The guard returns no payload and cannot accidentally expose a partial result.
    """
    # Validate even callers passing model_copy()/model_construct() without rehashing.
    checked = ResearchSnapshot.model_validate_json(snapshot.model_dump_json())
    providers = {checked.instrument.provider, *(record.provider for record in checked.evidence)}
    if "money-demo" in providers:
        if (
            allow_synthetic is not True
            or checked.ticker != "DEMO.L"
            or providers != {"money-demo"}
            or not checked.evidence
            or any(
                not record.canonical_source_id.startswith("demo:") for record in checked.evidence
            )
        ):
            raise CommercialLicenceError("MISSING_LICENCE")
        return ("money-demo",)
    if not checked.evidence:
        raise CommercialLicenceError("MISSING_LICENCE")
    for provider in sorted(providers):
        catalog.require_commercial_use(provider, redistribution=redistribution, as_of=as_of)
    return tuple(sorted(providers))
