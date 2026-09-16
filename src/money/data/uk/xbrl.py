"""Bounded stream-read-xbrl normalization; filing publication remains external evidence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module

from money.adapters.native_attestation import require_pinned_module
from money.data.security import SourceSecurityError, bounded_zip_members
from money.schemas.contracts import EvidenceRecord, FinancialFact

METRICS = {
    "cash_bank_in_hand": "cash",
    "turnover_gross_operating_revenue": "revenue",
    "net_assets_liabilities_including_pension_asset_liability": "net_assets",
    "creditors_due_within_one_year": "current_liabilities",
    "creditors_due_after_one_year": "noncurrent_liabilities",
    "operating_profit_loss": "operating_profit",
    "profit_loss_for_period": "net_income",
}


def parse_company_archive(
    archive: bytes,
    *,
    company_number: str,
    source_id: str,
    snapshot_id: str,
    publication_time: datetime,
    retrieved_at: datetime,
    verified_currency: str,
) -> tuple[EvidenceRecord, ...]:
    if (
        publication_time > retrieved_at
        or publication_time.tzinfo is None
        or retrieved_at.tzinfo is None
    ):
        raise ValueError("PIT_VIOLATION")
    if verified_currency != "GBP":
        # Native row format drops original unit metadata. Never guess units.
        raise ValueError("XBRL_VERIFIED_CURRENCY_REQUIRED")
    members = bounded_zip_members(archive)
    for name, data in members:
        if not name.lower().endswith((".xml", ".xhtml", ".html", ".htm")):
            raise SourceSecurityError("XBRL_MEMBER_TYPE_DENIED")
        normalized = data.upper().replace(b"\x00", b"")
        if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
            raise SourceSecurityError("XBRL_EXTERNAL_ENTITY_DENIED")
    require_pinned_module("stream_read_xbrl")
    native = import_module("stream_read_xbrl")
    records, seen = [], set()
    for name, data in members:
        # Exact pinned seam avoids native whole-machine process-pool fanout.
        # This function is executed only under the compute job's hard deadline.
        for row in native._xbrl_to_rows((name, data)):
            values = dict(zip(native._COLUMNS, row, strict=False))
            if values.get("error"):
                raise ValueError("XBRL_PARSE_FAILED")
            if str(values.get("companies_house_registered_number", "")).zfill(8) != company_number:
                raise ValueError("XBRL_COMPANY_MISMATCH")
            period = values.get("period_end") or values.get("balance_sheet_date")
            if period is None:
                raise ValueError("XBRL_PERIOD_UNKNOWN")
            period_end = datetime.combine(period, datetime.min.time(), UTC)
            if period_end > publication_time:
                raise ValueError("PIT_VIOLATION")
            for native_metric, metric in METRICS.items():
                value = values.get(native_metric)
                if value is None:
                    continue
                key = (metric, period_end)
                if key in seen:
                    raise ValueError("XBRL_DUPLICATE_FACT_REQUIRES_REVIEW")
                seen.add(key)
                records.append(
                    EvidenceRecord(
                        snapshot_id=snapshot_id,
                        source="Companies House filing",
                        provider="stream-read-xbrl",
                        source_id=source_id,
                        canonical_source_id=f"companies-house:{source_id}",
                        observation_time=period_end,
                        publication_time=publication_time,
                        retrieval_time=retrieved_at,
                        fresh_until=retrieved_at + timedelta(days=1),
                        pit_safe=True,
                        payload=FinancialFact(
                            metric=metric,
                            value=Decimal(str(value)),
                            unit="GBP",
                            period_end=period_end,
                        ),
                    )
                )
    return tuple(records)
