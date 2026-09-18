"""Resumable live provider observations and independently reviewed admission.

Broker discovery is not eligibility. API access is not a licence. In particular,
the filing-history probe below never qualifies financial-document extraction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, Field, TypeAdapter

from money.adapters.eligibility import eligibility_failures
from money.adapters.native_attestation import SINGLE_MODULE_SOURCE_DIGESTS
from money.data.identifiers import InstrumentIdentifiers
from money.data.live_eligibility import EligibilityReview
from money.data.provider_probes import (
    ProviderAdmissionReview,
    ProviderProbeReport,
    QualificationArtifacts,
    json_bytes,
    probe_companies_house,
    probe_eodhd,
    qualify_probe,
)
from money.data.qualification import ProviderQualification
from money.data.uk.filing_documents import (
    DOCUMENT_HOST,
    CompaniesHouseFilingDocuments,
    FinancialCurrencyProof,
    ReviewedStorageHost,
)
from money.data.uk.live import Trading212MetadataProvider
from money.qualification.core import QualificationContext
from money.research.live import VerifiedInstrument
from money.schemas.contracts import (
    Contract,
    EvidenceRecord,
    FinancialFact,
    InstrumentMetadata,
    ResearchMandate,
    utc_now,
)


class IndependentReview(Contract):
    """Explicit operator assertion with separate preparer/reviewer identities."""

    status: Literal["REVIEWED"]
    prepared_by: str = Field(min_length=1, max_length=200)
    reviewed_by: str = Field(min_length=1, max_length=200)
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime

    def require_current(self, now: datetime) -> None:
        if (
            not self.prepared_by.strip()
            or not self.reviewed_by.strip()
            or self.prepared_by.strip().casefold() == self.reviewed_by.strip().casefold()
            or not self.reviewed_at <= now < self.valid_until
        ):
            raise ValueError("INDEPENDENT_REVIEW_REQUIRED")


class InstrumentReview(Contract):
    review: IndependentReview
    discovered: dict[str, Any]
    metadata: InstrumentMetadata
    identifiers: InstrumentIdentifiers
    eligibility_evidence_files: tuple[str, ...] = Field(min_length=1, max_length=10)
    ethical_evidence_files: tuple[str, ...] = Field(min_length=1, max_length=10)


class SupplementalReview(Contract):
    review: IndependentReview
    # Generated identity/proof fields are forbidden in this operator section.
    instrument_evidence: dict[str, Any]
    corporate_action_coverage_file: str
    archived_market_proof_file: str | None = None
    financial_currency_evidence_files: tuple[str, ...] = ()


class SourceObservation(Contract):
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
    evidence: EvidenceRecord
    source_evidence_file: str
    publication_evidence_file: str | None = None


class SupplementalSourceAdmission(Contract):
    """Independent admission of attached source bytes, never guessed API rights."""

    review: IndependentReview
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,49}$")
    usage_purpose: str = Field(min_length=1, max_length=500)
    storage_policy: str = Field(min_length=1, max_length=500)
    redistribution: Literal["PROHIBITED", "FACTS_AND_LINKS", "LICENSED"]
    attribution: str = Field(min_length=1, max_length=500)
    source_documentation: str = Field(min_length=1, max_length=2000)
    rights_evidence_file: str
    publication_times: Literal["AS_RETRIEVED", "ORIGINAL_PUBLICATION_VERIFIED"]
    maximum_age_seconds: int = Field(gt=0, le=604800)
    observations: tuple[SourceObservation, ...] = Field(min_length=1, max_length=4000)


class _Artifacts(QualificationArtifacts):
    """Use the runner's secret-safe, contained artifact writer for probe bytes."""

    def __init__(self, ctx: QualificationContext, refs: set[tuple[str, str]]) -> None:
        super().__init__(ctx.root / "artifacts", secrets=tuple(ctx.environ.values()))
        self.ctx, self.refs = ctx, refs

    def save_bytes(
        self, raw: bytes, *, extension: Literal["json", "bin"] = "bin"
    ) -> tuple[str, str]:
        digest, path = self.ctx.artifact(raw)
        self.refs.add((digest, path))
        return digest, path.removeprefix("artifacts/")

    def read(self, digest: str | None, name: str | None) -> bytes:
        if digest is None or name not in {digest + ".json", digest + ".bin"}:
            raise ValueError("PROVIDER_PROOF_INVALID")
        relative = "artifacts/" + name
        raw = self.ctx.read_bytes(relative)
        if raw is None or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("PROVIDER_PROOF_INVALID")
        self.refs.add((digest, relative))
        return raw


def _stamp_template() -> dict[str, Any]:
    return {
        "status": "UNRESOLVED",
        "prepared_by": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "valid_until": None,
    }


def _candidate_key(row: dict[str, Any]) -> str:
    return hashlib.sha256(row["ticker"].encode()).hexdigest()[:24]


def _candidate_template(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "review": _stamp_template(),
        "discovered": row,
        "metadata": {
            "ticker": None,
            "company": None,
            "instrument_type": None,
            "quote_currency": None,
            # Objective presence in this live response, not permission to buy.
            "currently_available": True,
            "business_activities": [],
            "activities_verified": False,
            "verified_at": None,
            "source": None,
            "provider": None,
            "source_id": None,
        },
        "identifiers": {
            "ticker": None,
            "company_name": None,
            "trading212_id": None,
            "exchange_ticker": None,
            "exchange": None,
            "isin": None,
            "quote_currency": None,
            "companies_house_number": None,
            "provider_symbols": [],
            "verified_at": None,
            "valid_until": None,
            "source": None,
        },
        "eligibility_evidence_files": [],
        "ethical_evidence_files": [],
    }


def _supplemental_template() -> dict[str, Any]:
    return {
        "review": _stamp_template(),
        "instrument_evidence": {
            "spread_bps": None,
            "spread_evidence": None,
            "corporate_actions_complete": None,
            "supplemental_evidence": [],
            "cost_applicability": {
                "sdrt": "UNKNOWN",
                "evidence_source": None,
                "exemption_reason": None,
                "broker_round_trip_fee_gbp": None,
                "broker_fee_source": None,
                "other_round_trip_charges_gbp": None,
                "other_charge_source": None,
            },
            "archived_market_evidence": [],
            "filing_documents": [],
        },
        "corporate_action_coverage_file": None,
        "archived_market_proof_file": None,
        "financial_currency_evidence_files": [],
    }


def _attach(ctx: QualificationContext, path: str, refs: set[tuple[str, str]]) -> tuple[str, str]:
    # Input files are bounded and symlink-safe, and artifact() scans their exact bytes.
    if not path.startswith("inputs/"):
        raise ValueError("REVIEW_EVIDENCE_INPUT_REQUIRED")
    raw = ctx.read_bytes(path)
    if not raw or not raw.strip() or raw.strip() in {b"null", b"{}", b"[]"}:
        raise ValueError("REVIEW_EVIDENCE_REQUIRED")
    artifact = ctx.artifact(raw)
    refs.add(artifact)
    return artifact


def _eligibility(
    ctx: QualificationContext,
    row: dict[str, Any],
    path: str,
    refs: set[tuple[str, str]],
) -> EligibilityReview | None:
    value = ctx.read_json(path)
    if not isinstance(value, dict) or value.get("review", {}).get("status") != "REVIEWED":
        return None
    review = InstrumentReview.model_validate(value)
    review.review.require_current(ctx.now)
    review.identifiers.require_current(ctx.now)
    # This path is called only with a row from the successful current live
    # retrieval below. Presence is machine evidence, not a human buy attestation.
    # Keep the reviewed file and its timestamp untouched; its other facts are
    # independently checked against this row before the derived join is admitted.
    metadata = review.metadata.model_copy(update={"currently_available": True, "isa_available": None})
    identifiers = review.identifiers
    # The independent review must concern exactly this still-listed instrument.
    # Display-name changes do not confer or revoke an identity mapping.
    keys = ("ticker", "isin", "type", "currencyCode")
    if (
        any(review.discovered.get(key) != row.get(key) for key in keys)
        or identifiers.trading212_id != row["ticker"]
        or identifiers.isin != row.get("isin")
        or metadata.instrument_type != row["type"]
        or metadata.quote_currency != row["currencyCode"]
        or metadata.verified_at > review.review.reviewed_at
        or identifiers.verified_at > review.review.reviewed_at
        or eligibility_failures(metadata, ResearchMandate(), ctx.now)
    ):
        raise ValueError("LIVE_METADATA_REVIEW_JOIN_FAILED")
    input_proof = _attach(ctx, path, refs)
    eligibility_files = [_attach(ctx, item, refs) for item in review.eligibility_evidence_files]
    ethical_files = [_attach(ctx, item, refs) for item in review.ethical_evidence_files]
    eligibility_proof = ctx.artifact(
        {
            "kind": "independently-reviewed-eligibility",
            "review": input_proof,
            "evidence": eligibility_files,
            "broker_metadata": row,
        }
    )
    ctx.write_json(
        "state/eligibility-join-" + _candidate_key(row) + ".json",
        {
            "broker_observed_at": ctx.now.isoformat(),
            "eligibility_proof_hash": eligibility_proof[0],
            "status": "CURRENT_METADATA_AND_REVIEW_JOIN_VERIFIED",
        },
    )
    ethical_proof = ctx.artifact(
        {
            "kind": "independently-reviewed-ethical-screen",
            "review": input_proof,
            "evidence": ethical_files,
        }
    )
    refs.update((eligibility_proof, ethical_proof))
    return EligibilityReview(
        metadata=metadata,
        identifiers=identifiers,
        eligibility_proof_hash=eligibility_proof[0],
        ethical_proof_hash=ethical_proof[0],
    )


def _verified_instrument(
    ctx: QualificationContext,
    eligible: EligibilityReview,
    path: str,
    refs: set[tuple[str, str]],
    *,
    foreign_issuer_evidence: tuple[str, str] | None = None,
) -> VerifiedInstrument | None:
    value = ctx.read_json(path)
    if not isinstance(value, dict) or value.get("review", {}).get("status") != "REVIEWED":
        return None
    review = SupplementalReview.model_validate(value)
    review.review.require_current(ctx.now)
    controlled = {
        "metadata",
        "identifiers",
        "eligibility_proof_hash",
        "ethical_proof_hash",
        "corporate_action_coverage_hash",
        "archived_market_proof_hash",
        "issuer_jurisdiction",
        "issuer_jurisdiction_proof_hash",
    }
    if controlled.intersection(review.instrument_evidence):
        raise ValueError("GENERATED_PROOF_OVERRIDE_FORBIDDEN")
    _attach(ctx, path, refs)
    corporate = _attach(ctx, review.corporate_action_coverage_file, refs)
    archived = (
        _attach(ctx, review.archived_market_proof_file, refs)
        if review.archived_market_proof_file
        else None
    )
    financial_hashes = {
        _attach(ctx, item, refs)[0] for item in review.financial_currency_evidence_files
    }
    instrument = VerifiedInstrument.model_validate(
        {
            **eligible.model_dump(),
            **review.instrument_evidence,
            "corporate_action_coverage_hash": corporate[0],
            "archived_market_proof_hash": archived[0] if archived else None,
            "issuer_jurisdiction": foreign_issuer_evidence[0] if foreign_issuer_evidence else None,
            "issuer_jurisdiction_proof_hash": foreign_issuer_evidence[1]
            if foreign_issuer_evidence
            else None,
        }
    )
    instrument.identifiers.symbol_for("eodhd", ctx.now)
    costs = instrument.cost_applicability
    if (
        (
            not instrument.identifiers.companies_house_number
            and not (
                foreign_issuer_evidence
                and foreign_issuer_evidence[0] != "GB"
                and any(
                    record.payload.kind == "filing" for record in instrument.supplemental_evidence
                )
                and any(
                    isinstance(record.payload, FinancialFact)
                    and record.payload.metric != "spread_bps"
                    for record in instrument.supplemental_evidence
                )
            )
        )
        or not instrument.corporate_actions_complete
        or not instrument.spread_evidence.available_at(ctx.now)
        or costs.sdrt == "UNKNOWN"
        or (costs.sdrt == "EXEMPT" and not costs.exemption_reason)
        or costs.broker_round_trip_fee_gbp is None
        or not costs.broker_fee_source
        or costs.other_round_trip_charges_gbp is None
        or not costs.other_charge_source
        or (instrument.archived_market_evidence and archived is None)
        or any(proof.evidence_hash not in financial_hashes for proof in instrument.filing_documents)
    ):
        raise ValueError("SUPPLEMENTAL_INSTRUMENT_EVIDENCE_INCOMPLETE")
    return instrument


def _rights_template(provider: str) -> dict[str, Any]:
    return {
        "status": "UNRESOLVED",
        "rights_evidence_file": None,
        "review": {
            "provider": provider,
            "reviewed_by": None,
            "usage_purpose": None,
            "storage_policy": None,
            "redistribution": None,
            "attribution": None,
            "source_documentation": None,
            "reviewed_at": None,
            "valid_until": None,
        },
    }


def _provider(
    ctx: QualificationContext,
    name: Literal["eodhd", "companies-house"],
    samples: tuple[InstrumentIdentifiers, ...],
    artifacts: _Artifacts,
) -> dict[str, Any] | None:
    review_path = f"inputs/provider-rights/{name}.json"
    ctx.template(review_path, _rights_template(name))
    credential_name = "EODHD_API_KEY" if name == "eodhd" else "COMPANIES_HOUSE_API_KEY"
    if not ctx.environ.get(credential_name):
        ctx.block("PROVIDER_CREDENTIAL_MISSING", f"Configure {credential_name} on Money.")
        return None
    if not samples:
        ctx.block("PROVIDER_REVIEWED_SAMPLES_REQUIRED", f"Review instrument mappings for {name}.")
        return None
    report = None
    cache_path = f"state/providers/{name}.json"
    cache = ctx.read_json(cache_path)
    identity = hashlib.sha256(
        json_bytes([item.model_dump(mode="json") for item in samples])
    ).hexdigest()
    if isinstance(cache, dict) and cache.get("samples_sha256") == identity:
        try:
            raw = artifacts.read(cache.get("sha256"), cache.get("path"))
            cached = ProviderProbeReport.model_validate_json(raw)
            if (
                cached.provider == name
                and cached.samples == samples
                and cached.retrieved_at <= ctx.now < cached.retrieved_at + timedelta(days=1)
                and all(item.status != "FAILED" for item in cached.datasets)
            ):
                for dataset in cached.datasets:
                    artifacts.read(dataset.artifact_hash, dataset.artifact_path)
                report = cached
        except (ValueError, OSError, TypeError):
            # Tampered/stale cached observations cannot be used as qualification.
            report = None
    if report is None:
        probe = probe_eodhd if name == "eodhd" else probe_companies_house
        try:
            report = probe(ctx.environ[credential_name], samples, ctx.now, artifacts)
            digest, path = artifacts.save(report.model_dump(mode="json"))
            ctx.write_json(cache_path, {"samples_sha256": identity, "sha256": digest, "path": path})
        except Exception:
            ctx.block(
                "PROVIDER_PROBE_FAILED", f"Resolve the live {name} retrieval failure and resume."
            )
            return None
    if any(item.status == "FAILED" for item in report.datasets):
        failed = sorted({item.dataset for item in report.datasets if item.status == "FAILED"})
        ctx.block("PROVIDER_DATASET_RETRIEVAL_FAILED", f"{name}: {', '.join(failed)}.")
        return None
    if name == "companies-house":
        filings = []
        for dataset in report.datasets:
            if dataset.dataset != "filing":
                continue
            observation = json.loads(artifacts.read(dataset.artifact_hash, dataset.artifact_path))
            for record in observation["records"]:
                filings.append(
                    {
                        "ticker": dataset.ticker,
                        "filing_id": record["source_id"],
                        "title": record["payload"]["title"],
                        "url": record["payload"]["url"],
                        "financial_document_qualified": False,
                    }
                )
        ctx.write_json("discovery/companies-house-filings.json", {"filings": filings})
    rights = ctx.read_json(review_path)
    if not isinstance(rights, dict) or rights.get("status") != "REVIEWED":
        ctx.block(
            "PROVIDER_RIGHTS_REVIEW_REQUIRED",
            f"Complete {review_path}; API access is not permission.",
        )
        return None
    expected_datasets = (
        ("ohlcv", "corporate_action", "news") if name == "eodhd" else ("company", "filing")
    )
    unobserved = [
        dataset
        for dataset in expected_datasets
        if not any(
            item.dataset == dataset and item.status == "RETRIEVED" and item.record_count > 0
            for item in report.datasets
        )
    ]
    if unobserved:
        ctx.block(
            "PROVIDER_DATASET_OBSERVATION_REQUIRED",
            f"{name} returned no qualifying records for {', '.join(unobserved)}; review additional sample mappings with actual accessible records.",
        )
        return None
    try:
        if set(rights) != {"status", "rights_evidence_file", "review"}:
            raise ValueError("RIGHTS_REVIEW_INVALID")
        evidence_hash, evidence_path = _attach(ctx, rights["rights_evidence_file"], artifacts.refs)
        review_data = rights["review"]
        if not isinstance(review_data, dict) or "rights_evidence_hash" in review_data:
            raise ValueError("GENERATED_PROOF_OVERRIDE_FORBIDDEN")
        review = ProviderAdmissionReview.model_validate(
            {**review_data, "rights_evidence_hash": evidence_hash}
        )
        if any(
            not getattr(review, field).strip()
            for field in (
                "reviewed_by",
                "usage_purpose",
                "storage_policy",
                "attribution",
                "source_documentation",
            )
        ):
            raise ValueError("RIGHTS_REVIEW_INVALID")
        rights_raw = ctx.read_bytes(evidence_path)
        if rights_raw is None:
            raise ValueError("RIGHTS_EVIDENCE_REQUIRED")
        qualification = qualify_probe(report, review, rights_raw, artifacts, clock=lambda: ctx.now)
        artifacts.save(qualification.model_dump(mode="json"))
        return qualification.model_dump(mode="json")
    except Exception:
        ctx.block(
            "PROVIDER_ADMISSION_REVIEW_FAILED",
            f"Correct rights review or observed dataset coverage in {review_path}.",
        )
        return None


def _document_observation(
    ctx: QualificationContext,
    adapter: CompaniesHouseFilingDocuments,
    identity: InstrumentIdentifiers,
    proof: FinancialCurrencyProof,
    refs: set[tuple[str, str]],
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[str, str]:
    """Reuse only hash-verified, current native observations of this exact proof."""
    key = hashlib.sha256(
        json_bytes(
            {
                "identity": identity.model_dump(mode="json"),
                "proof": proof.model_dump(mode="json"),
                "parser": SINGLE_MODULE_SOURCE_DIGESTS["stream_read_xbrl"],
                "storage_hosts": adapter.storage_hosts,
            }
        )
    ).hexdigest()
    cache_name = "financial-document-" + key[:24]
    cached = ctx.cache(cache_name, key, 86400)
    if cached:
        try:
            digest, relative = cached["bundle_ref"]
            raw = ctx.verify_artifact(digest, relative)
            observed = json.loads(raw)
            records = TypeAdapter(tuple[EvidenceRecord, ...]).validate_python(observed["evidence"])
            retrieved = datetime.fromisoformat(observed["retrieval_time"].replace("Z", "+00:00"))
            checked_at = (clock or utc_now)()
            identity.require_current(checked_at)
            expected_source = (
                f"{identity.companies_house_number}:{proof.filing_id}:"
                f"{observed['document_id']}:{proof.document_content_hash}"
            )
            storage_host = observed["storage_host"]
            if (
                observed["company_number"] != identity.companies_house_number
                or observed["filing_id"] != proof.filing_id
                or observed["content_hash"] != proof.document_content_hash
                or observed["currency_evidence_hash"] != proof.evidence_hash
                or observed["point_in_time_status"] != "AS_RETRIEVED"
                or observed["original_publication_time"] is not None
                or datetime.fromisoformat(observed["availability_time"].replace("Z", "+00:00"))
                != retrieved
                or storage_host not in {DOCUMENT_HOST, *adapter.storage_hosts}
                or observed["storage_host_review_hash"] != adapter.storage_hosts.get(storage_host)
                or observed["conversion_source_attestation"] != "PINNED_SOURCE_VERIFIED"
                or observed["conversion_source_hash"]
                != SINGLE_MODULE_SOURCE_DIGESTS["stream_read_xbrl"]
                or not retrieved <= checked_at < retrieved + timedelta(days=1)
                or not records
                or any(
                    not item.available_at(checked_at)
                    or item.fresh_until <= checked_at
                    or item.conflicting
                    or not isinstance(item.payload, FinancialFact)
                    or item.provider != "companies-house"
                    or item.snapshot_id != "provider-admission-probe"
                    or item.source_id != expected_source
                    or item.canonical_source_id != "companies-house:" + expected_source
                    or item.payload.unit != "GBP"
                    or item.payload.period_end > retrieved
                    or item.publication_time != retrieved
                    or item.retrieval_time != retrieved
                    for item in records
                )
            ):
                raise ValueError("FINANCIAL_OBSERVATION_INVALID")
            refs.add((digest, relative))
            return digest, relative
        except (ValueError, KeyError, TypeError):
            pass
    bundle = adapter.probe_document(identity, proof.filing_id, proof)
    if bundle.conversion_source_attestation != "PINNED_SOURCE_VERIFIED":
        raise ValueError("FILING_NATIVE_PARSER_ATTESTATION_REQUIRED")
    bundle_ref = ctx.artifact(bundle.model_dump(mode="json"))
    refs.add(bundle_ref)
    ctx.checkpoint(cache_name, key, {"bundle_ref": bundle_ref}, (bundle_ref,))
    return bundle_ref


def _financial_documents(
    ctx: QualificationContext,
    result: dict[str, Any],
    refs: set[tuple[str, str]],
    *,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Probe true document extraction without pretending the filing feed qualified it."""
    path = "inputs/financial-documents.json"
    live_clock = clock or utc_now
    value = ctx.read_json(path)
    base_data = next(
        (
            item
            for item in result["provider_qualifications"]
            if item["provider"] == "companies-house"
        ),
        None,
    )
    selected = value.get("documents") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or not base_data
        or not isinstance(selected, list)
        or not 1 <= len(selected) <= 4
    ):
        ctx.block(
            "CH_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED",
            f"Select up to four actual accounts filings in {path}; review company mappings and filing-data rights first.",
        )
        return
    try:
        base = ProviderQualification.model_validate(base_data)
        hosts = []
        for entry in value.get("storage_hosts", []):
            stamp = IndependentReview.model_validate(entry["review"])
            stamp.require_current(live_clock())
            digest, _ = _attach(ctx, entry["evidence_file"], refs)
            hosts.append(ReviewedStorageHost(host=entry["host"], review_evidence_hash=digest))
        # This is an explicitly unqualified scope, never used by public .fetch().
        scope = base.model_copy(
            update={
                "datasets": ("filing", "financial"),
                "production_qualified": False,
                "qualified_by": None,
                "qualification_report_hash": None,
            }
        )
        adapter = CompaniesHouseFilingDocuments(
            ctx.environ["COMPANIES_HOUSE_API_KEY"],
            scope,
            storage_hosts=tuple(hosts),
            clock=live_clock,
        )
        identities = {
            item["identifiers"]["ticker"]: InstrumentIdentifiers.model_validate(item["identifiers"])
            for item in result["eligibility_reviews"]
        }
        proofs: dict[str, list[FinancialCurrencyProof]] = {}
        bundles = []
        seen = set()
        for selection in selected:
            ticker, filing_id = selection["ticker"], selection["filing_id"]
            if (ticker, filing_id) in seen:
                raise ValueError("FILING_SELECTION_DUPLICATE")
            seen.add((ticker, filing_id))
            identity = identities[ticker]
            if not identity.companies_house_number:
                raise ValueError("COMPANY_MAPPING_REQUIRED")
            if (
                selection.get("accounting_currency") != "GBP"
                or not selection.get("document_content_hash")
                or not selection.get("currency_evidence_file")
            ):
                discovered = adapter.inspect_document(identity, filing_id)
                ctx.write_json(
                    "discovery/filing-"
                    + hashlib.sha256(f"{ticker}:{filing_id}".encode()).hexdigest()[:24]
                    + ".json",
                    discovered,
                )
                if discovered["status"] == "UNREVIEWED_STORAGE_HOST":
                    ctx.block(
                        "CH_DOCUMENT_STORAGE_HOST_REVIEW_REQUIRED",
                        f"Review the exact discovered storage host for {ticker}/{filing_id} in {path}; it was not followed.",
                    )
                    continue
                ctx.block(
                    "CH_ACCOUNTING_CURRENCY_REVIEW_REQUIRED",
                    f"Review original accounting units and exact discovered document hash for {ticker}/{filing_id} in {path}.",
                )
                continue
            digest, _ = _attach(ctx, selection["currency_evidence_file"], refs)
            proof = FinancialCurrencyProof(
                company_number=identity.companies_house_number,
                filing_id=filing_id,
                currency="GBP",
                document_content_hash=selection["document_content_hash"],
                evidence_hash=digest,
            )
            bundle_ref = _document_observation(
                ctx, adapter, identity, proof, refs, clock=live_clock
            )
            proofs.setdefault(ticker, []).append(proof)
            bundles.append(
                {"ticker": ticker, "proof": proof.model_dump(mode="json"), "bundle": bundle_ref}
            )
        if len(bundles) != len(selected):
            return
        stamp = IndependentReview.model_validate(value.get("review"))
        stamp.require_current(live_clock())
        if value.get("rights_cover_financial_documents") is not True:
            raise ValueError("FINANCIAL_DOCUMENT_RIGHTS_REVIEW_REQUIRED")
        review_ref = _attach(ctx, path, refs)
        report_ref = ctx.artifact(
            {
                "kind": "money-financial-document-qualification-v1",
                "provider": "companies-house",
                "publication_times": "AS_RETRIEVED",
                "historical_publication_verified": False,
                "filing_qualification": base.model_dump(mode="json"),
                "independent_review": review_ref,
                "documents": bundles,
            }
        )
        refs.add(report_ref)
        qualified = ProviderQualification.model_validate(
            {
                **base.model_dump(),
                "datasets": ("filing", "financial"),
                "qualified_by": stamp.reviewed_by,
                "qualification_report_hash": report_ref[0],
                "valid_until": min(base.valid_until, stamp.valid_until),
            }
        )
        qualified.require("financial", live_clock())
        instruments = []
        for instrument in result["instruments"]:
            updated = dict(instrument)
            if instrument["metadata"]["ticker"] in proofs:
                updated["filing_documents"] = [
                    item.model_dump(mode="json")
                    for item in proofs[instrument["metadata"]["ticker"]]
                ]
            instruments.append(VerifiedInstrument.model_validate(updated).model_dump(mode="json"))
        result["provider_qualifications"] = [
            qualified.model_dump(mode="json") if item["provider"] == "companies-house" else item
            for item in result["provider_qualifications"]
        ]
        result["filing_document_storage_hosts"] = [item.model_dump(mode="json") for item in hosts]
        result["instruments"] = instruments
    except Exception:
        ctx.block(
            "CH_FINANCIAL_DOCUMENT_QUALIFICATION_FAILED",
            f"Correct {path}, reviewed storage hosts, live document access or the pinned XBRL parser; financial coverage is not admitted.",
        )


def _additional_sources(
    ctx: QualificationContext,
    result: dict[str, Any],
    refs: set[tuple[str, str]],
) -> None:
    """Admit only reviewed observations needed by the selected instrument bytes.

    A declared dataset list is intentionally not an input: coverage is derived
    from actual, matching records with attached source and rights evidence.
    """
    index_path = "inputs/supplemental-sources.json"
    ctx.template(
        index_path,
        {
            "source_review_files": [],
            "instructions": "Add one independently reviewed source file for each non-EODHD/non-Companies-House spread, supplemental or original-publication archive source. Each observed record must exactly match instrument evidence and link original source bytes plus actual licence/permission bytes. Original-publication claims additionally need per-record historical publication evidence; today's retrieval cannot establish it.",
            "source_review_template": {
                "review": _stamp_template(),
                "provider": None,
                "usage_purpose": None,
                "storage_policy": None,
                "redistribution": None,
                "attribution": None,
                "source_documentation": None,
                "rights_evidence_file": None,
                "publication_times": None,
                "maximum_age_seconds": None,
                "observations": [],
            },
            "observation_template": {
                "ticker": None,
                "evidence": None,
                "source_evidence_file": None,
                "publication_evidence_file": None,
            },
        },
    )
    declared = ctx.read_json(index_path)
    paths = declared.get("source_review_files", []) if isinstance(declared, dict) else []
    if (
        not isinstance(paths, list)
        or len(paths) > 20
        or any(not isinstance(path, str) for path in paths)
        or len(set(paths)) != len(paths)
    ):
        ctx.block("SUPPLEMENTAL_SOURCE_REVIEW_INDEX_INVALID", f"Correct {index_path}.")
        return
    instruments = tuple(VerifiedInstrument.model_validate(item) for item in result["instruments"])
    expected = {
        (item.metadata.ticker, evidence.hash): (item, evidence)
        for item in instruments
        for evidence in (
            item.spread_evidence,
            *item.supplemental_evidence,
            *item.archived_market_evidence,
        )
    }
    admissions = []
    observed_coverage: list[tuple[str, str, str]] = []
    admitted_providers: set[str] = set()
    for path in paths:
        try:
            if not isinstance(path, str) or not path.startswith("inputs/"):
                raise ValueError("SOURCE_REVIEW_PATH_INVALID")
            admission = SupplementalSourceAdmission.model_validate(ctx.read_json(path))
            checked_at = utc_now()
            admission.review.require_current(checked_at)
            if (
                admission.provider
                in {"eodhd", "companies-house", "trading212", "yfinance", "money-demo"}
                or admission.provider in admitted_providers
                or not admission.review.reviewed_at
                <= checked_at
                < admission.review.reviewed_at + timedelta(seconds=admission.maximum_age_seconds)
                or any(
                    not getattr(admission, field).strip()
                    for field in (
                        "usage_purpose",
                        "storage_policy",
                        "attribution",
                        "source_documentation",
                    )
                )
            ):
                raise ValueError("SUPPLEMENTAL_SOURCE_REVIEW_INVALID")
            source_refs = []
            currencies: set[str] = set()
            datasets: set[str] = set()
            record_hashes: set[str] = set()
            records = []
            for observation in admission.observations:
                record = observation.evidence
                item, selected = expected[(observation.ticker, record.hash)]
                if (
                    record != selected
                    or record.hash in record_hashes
                    or record.provider != admission.provider
                    or record.retrieval_time > admission.review.reviewed_at
                    or record.fresh_until <= checked_at
                    or not record.available_at(checked_at)
                    or record.conflicting
                    or not record.source.strip()
                    or not record.source_id.strip()
                    or not record.canonical_source_id.strip()
                ):
                    raise ValueError("SUPPLEMENTAL_SOURCE_OBSERVATION_INVALID")
                item.identifiers.require_current(checked_at)
                original = _attach(ctx, observation.source_evidence_file, refs)
                publication = None
                if admission.publication_times == "ORIGINAL_PUBLICATION_VERIFIED":
                    if not observation.publication_evidence_file:
                        raise ValueError("ORIGINAL_PUBLICATION_EVIDENCE_REQUIRED")
                    publication = _attach(ctx, observation.publication_evidence_file, refs)
                elif record.publication_time != record.retrieval_time:
                    raise ValueError("AS_RETRIEVED_PUBLICATION_TIME_INVALID")
                if (
                    record in item.archived_market_evidence
                    and admission.publication_times != "ORIGINAL_PUBLICATION_VERIFIED"
                ):
                    raise ValueError("ARCHIVE_ORIGINAL_PUBLICATION_REQUIRED")
                source_refs.append(
                    {
                        "ticker": observation.ticker,
                        "record_hash": record.hash,
                        "source": original,
                        "publication": publication,
                    }
                )
                record_hashes.add(record.hash)
                currencies.add(item.metadata.quote_currency)
                datasets.add(record.payload.kind)
                records.append(record)
            rights_ref = _attach(ctx, admission.rights_evidence_file, refs)
            review_ref = _attach(ctx, path, refs)
            proof = ctx.artifact(
                {
                    "kind": "money-reviewed-supplemental-source-v1",
                    "review": review_ref,
                    "rights": rights_ref,
                    "observations": source_refs,
                    "provider": admission.provider,
                    "publication_times": admission.publication_times,
                }
            )
            refs.add(proof)
            qualified = ProviderQualification(
                provider=admission.provider,
                datasets=tuple(sorted(datasets)),
                currencies=tuple(sorted(currencies)),
                earliest_observation=min(record.observation_time for record in records),
                publication_times=admission.publication_times,
                maximum_age_seconds=admission.maximum_age_seconds,
                production_qualified=True,
                qualified_by=admission.review.reviewed_by,
                qualification_report_hash=proof[0],
                verified_at=admission.review.reviewed_at,
                valid_until=min(
                    admission.review.valid_until,
                    admission.review.reviewed_at + timedelta(seconds=admission.maximum_age_seconds),
                    *(record.fresh_until for record in records),
                ),
                usage_purpose=admission.usage_purpose,
                storage_policy=admission.storage_policy,
                redistribution=admission.redistribution,
                attribution=admission.attribution,
                source_documentation=admission.source_documentation,
            )
            for dataset in datasets:
                qualified.require(dataset, utc_now())
            admissions.append(qualified.model_dump(mode="json"))
            observed_coverage.extend(
                (admission.provider, item.ticker, item.evidence.hash)
                for item in admission.observations
            )
            admitted_providers.add(admission.provider)
        except Exception:
            result.setdefault("source_review_exclusions", []).append(
                {
                    "code": "SUPPLEMENTAL_SOURCE_REVIEW_REJECTED",
                    "action": "Correct source observation, rights, publication or freshness evidence in supplemental-source reviews.",
                }
            )
    result["additional_provider_qualifications"] = admissions
    result["supplemental_record_coverage"] = observed_coverage
    result["provider_qualifications"].extend(admissions)


def _filter_source_coverage(ctx: QualificationContext, result: dict[str, Any]) -> None:
    qualifications = {
        item["provider"]: ProviderQualification.model_validate(item)
        for item in result["provider_qualifications"]
    }
    verified = []
    observed = {tuple(item) for item in result.get("supplemental_record_coverage", [])}
    for value in result["instruments"]:
        try:
            instrument = VerifiedInstrument.model_validate(value)
            checked_at = utc_now()
            for record in (
                instrument.spread_evidence,
                *instrument.supplemental_evidence,
                *instrument.archived_market_evidence,
            ):
                if (record.provider, instrument.metadata.ticker, record.hash) not in observed:
                    raise ValueError("EXACT_SUPPLEMENTAL_SOURCE_OBSERVATION_REQUIRED")
                qualification = qualifications[record.provider]
                qualification.require(
                    record.payload.kind,
                    checked_at,
                    historical=record in instrument.archived_market_evidence,
                )
                if (
                    record.fresh_until <= checked_at
                    or not record.available_at(checked_at)
                    or record.retrieval_time > checked_at
                    or record.conflicting
                    or instrument.metadata.quote_currency not in qualification.currencies
                ):
                    raise ValueError("SUPPLEMENTAL_SOURCE_COVERAGE_INVALID")
            # Filing-only access cannot provide a spread; neither can an accounts
            # parser falsely be relabelled as a live market-spread provider.
            if instrument.spread_evidence.provider in {"companies-house", "eodhd"}:
                raise ValueError("MARKET_SPREAD_SOURCE_REVIEW_REQUIRED")
            spread = instrument.spread_evidence.payload
            if (
                not isinstance(spread, FinancialFact)
                or spread.metric != "spread_bps"
                or spread.unit != "bps"
                or spread.value != instrument.spread_bps
            ):
                raise ValueError("SPREAD_EVIDENCE_MISMATCH")
            verified.append(value)
        except (ValueError, KeyError):
            result.setdefault("candidate_exclusions", []).append(
                {
                    "code": "SUPPLEMENTAL_SOURCE_COVERAGE_REQUIRED",
                    "action": "Review actual spread/supplemental/archive source records and permission in inputs/supplemental-sources.json.",
                    "ticker": value["metadata"]["ticker"],
                }
            )
    result["instruments"] = verified


def run_provider_stages(ctx: QualificationContext) -> dict[str, Any]:
    """Observe current metadata, resume reviews, and probe only reviewed mappings."""
    # Presence selects the bulk workflow; its finalizer validates/migrates the
    # policy before consuming projections. A corrupt/missing marker must not
    # route an existing bulk universe back to the legacy GBP/GBX workflow.
    if any(
        ctx._path(path).exists()
        for path in (
            "state/bulk-universe-mode.json",
            "outputs/trading212-gbx-stock-universe.json",
            "outputs/uk-isa-stock-universe.json",
            "outputs/universe-provenance.json",
            "state/universe-rebuild-source.json",
        )
    ):
        from money.qualification.universe import run_bulk_provider_stages

        return run_bulk_provider_stages(ctx)
    refs: set[tuple[str, str]] = set()
    result: dict[str, Any] = {
        "provider_qualifications": [],
        "instruments": [],
        "eligibility_reviews": [],
        "candidate_counts": {"GBP": 0, "GBX": 0},
        "eligible_counts": {"GBP": 0, "GBX": 0},
        "candidate_exclusions": [],
    }
    # Rights and document requirements are visible even before instrument review.
    provider_names: tuple[Literal["eodhd", "companies-house"], ...] = ("eodhd", "companies-house")
    for name in provider_names:
        ctx.template(f"inputs/provider-rights/{name}.json", _rights_template(name))
    ctx.template(
        "inputs/financial-documents.json",
        {
            "review": _stamp_template(),
            "instructions": "Filing history alone cannot qualify financial. Supply reviewed original GBP accounting units, exact machine-readable document identity, approved storage hosts and independently qualified document-conversion evidence.",
            "financial_currency_proof_schema": "money.data.uk.filing_documents.FinancialCurrencyProof",
            "storage_host_schema": "money.data.uk.filing_documents.ReviewedStorageHost",
            "rights_cover_financial_documents": None,
            "storage_hosts": [],
            "documents": [],
            "document_entry_template": {
                "ticker": None,
                "filing_id": None,
                "accounting_currency": None,
                "document_content_hash": None,
                "currency_evidence_file": None,
            },
            "storage_host_entry_template": {
                "host": None,
                "review": _stamp_template(),
                "evidence_file": None,
            },
        },
    )
    key, secret = ctx.environ.get("TRADING212_API_KEY"), ctx.environ.get("TRADING212_API_SECRET")
    candidates: tuple[dict[str, Any], ...] = ()
    if not key or not secret:
        ctx.block(
            "TRADING212_METADATA_CREDENTIAL_MISSING",
            "Configure both Trading 212 credentials on Money.",
        )
    else:
        try:
            rows = Trading212MetadataProvider(key, secret).instruments()
            if any(not isinstance(row.get("ticker"), str) or not row["ticker"] for row in rows):
                raise ValueError("BROKER_IDENTITY_INVALID")
            if len({row["ticker"] for row in rows}) != len(rows):
                raise ValueError("BROKER_IDENTITY_AMBIGUOUS")
            candidates = tuple(
                sorted(
                    (
                        row
                        for row in rows
                        if row.get("type") == "STOCK" and row.get("currencyCode") == "GBX"
                    ),
                    key=lambda row: row["ticker"],
                )
            )
            ctx.write_json(
                "discovery/trading212-candidates.json",
                {
                    "status": "CANDIDATES_ONLY_NOT_ELIGIBILITY",
                    "observed_at": ctx.now.isoformat(),
                    "candidates": candidates,
                },
            )
        except Exception:
            ctx.block(
                "TRADING212_METADATA_RETRIEVAL_FAILED",
                "Resolve metadata API connectivity/access and resume; no stale discovery is admitted.",
            )
            candidates = ()
    reviews: list[EligibilityReview] = []
    for row in candidates:
        result["candidate_counts"][row["currencyCode"]] += 1
        stem = _candidate_key(row)
        path = f"inputs/instruments/{stem}.json"
        ctx.template(path, _candidate_template(row))
        try:
            reviewed = _eligibility(ctx, row, path, refs)
        except Exception:
            result["candidate_exclusions"].append(
                {
                    "code": "INSTRUMENT_REVIEW_REJECTED",
                    "action": f"Correct independent review, freshness, identity or attached evidence in {path}.",
                }
            )
            continue
        if reviewed is None:
            continue
        reviews.append(reviewed)
        result["eligible_counts"][reviewed.metadata.quote_currency] += 1
        result["eligibility_reviews"].append(reviewed.model_dump(mode="json"))
        supplemental_path = f"inputs/instrument-evidence/{stem}.json"
        ctx.template(supplemental_path, _supplemental_template())
        try:
            verified = _verified_instrument(ctx, reviewed, supplemental_path, refs)
            if verified is None:
                result["candidate_exclusions"].append(
                    {
                        "code": "INSTRUMENT_SUPPLEMENTAL_REVIEW_REQUIRED",
                        "action": f"Review spread, costs, corporate actions and archive evidence in {supplemental_path}.",
                    }
                )
            else:
                result["instruments"].append(verified.model_dump(mode="json"))
        except Exception:
            result["candidate_exclusions"].append(
                {
                    "code": "INSTRUMENT_SUPPLEMENTAL_REVIEW_REJECTED",
                    "action": f"Correct typed supplemental evidence and proof files in {supplemental_path}.",
                }
            )
    identifiers = tuple(item.identifiers for item in reviews)
    if len({item.ticker for item in identifiers}) != len(identifiers):
        duplicated = {
            item.ticker
            for item in identifiers
            if sum(other.ticker == item.ticker for other in identifiers) > 1
        }
        result["candidate_exclusions"].append(
            {
                "code": "INSTRUMENT_REVIEW_DUPLICATE_IDENTITY",
                "action": "Resolve duplicate canonical ticker mappings in inputs/instruments/.",
            }
        )
        identifiers = tuple(item for item in identifiers if item.ticker not in duplicated)
        result["instruments"] = [
            item for item in result["instruments"] if item["metadata"]["ticker"] not in duplicated
        ]
        result["eligibility_reviews"] = [
            item
            for item in result["eligibility_reviews"]
            if item["metadata"]["ticker"] not in duplicated
        ]
        result["eligible_counts"] = {
            currency: sum(item.quote_currency == currency for item in identifiers)
            for currency in ("GBP", "GBX")
        }
    if not identifiers:
        ctx.block(
            "LIVE_METADATA_AND_FRESH_REVIEW_JOIN_REQUIRED",
            "Independently review at least one inputs/instruments/ candidate; unresolved instruments remain excluded.",
        )
    # Bound external work to ten reviewed samples, covering both currencies first.
    samples = tuple(sorted(identifiers, key=lambda item: (item.quote_currency, item.ticker)))
    chosen: list[InstrumentIdentifiers] = []
    for currency in ("GBP", "GBX"):
        first = next((item for item in samples if item.quote_currency == currency), None)
        if first is not None:
            chosen.append(first)
    chosen.extend(item for item in samples if item not in chosen)
    artifacts = _Artifacts(ctx, refs)
    for name in provider_names:
        usable = tuple(
            item
            for item in chosen
            if (
                dict(item.provider_symbols).get("eodhd")
                if name == "eodhd"
                else item.companies_house_number
            )
        )[:10]
        qualification = _provider(ctx, name, usable, artifacts)
        if qualification is not None:
            result["provider_qualifications"].append(qualification)
    _financial_documents(ctx, result, refs)
    _additional_sources(ctx, result, refs)
    _filter_source_coverage(ctx, result)
    if not result["instruments"]:
        for exclusion in result["candidate_exclusions"]:
            ctx.block(exclusion["code"], exclusion["action"])
        ctx.block(
            "QUALIFIED_INSTRUMENT_EVIDENCE_REQUIRED",
            "Complete one candidate's eligibility, spread, costs, corporate-action and supplemental source reviews; all other unresolved candidates stay excluded.",
        )
    result["complete"] = bool(
        result["instruments"]
        and {"eodhd", "companies-house"}
        <= {item["provider"] for item in result["provider_qualifications"]}
        and any(
            item["provider"] == "companies-house" and "financial" in item["datasets"]
            for item in result["provider_qualifications"]
        )
        and any(item["filing_documents"] for item in result["instruments"])
    )
    result["artifact_refs"] = sorted(refs)
    result["unresolved_candidate_count"] = len(candidates) - len(result["eligibility_reviews"])
    ctx.write_json("state/provider-stage.json", result)
    return result
