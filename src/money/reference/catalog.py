"""Validate immutable Git product assets before exposing any reference values.

Checksums are corruption/reproducibility checks, not signatures. Git review and
release provenance supply authority. No code here writes or repairs assets.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from money.schemas.contracts import EXCLUDED_ACTIVITIES

MAX_FILE_BYTES = 2_000_000
MAX_ROWS = 10_000
SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=2000)]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ProviderId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
PlanId = Literal["FREE", "PRO", "TEAM", "ENTERPRISE"]


class ReferenceTableError(ValueError):
    """A release asset is missing, incompatible or corrupt."""


class CommercialLicenceError(PermissionError):
    """Commercial-use permission has not been explicitly reviewed."""


class ReferenceAssetSettings(BaseSettings):
    """One explicit, server-owned release-data mount for non-editable installs."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)
    money_reference_data_dir: Path | None = None

    @model_validator(mode="after")
    def absolute_mount(self) -> Self:
        if (
            self.money_reference_data_dir is not None
            and not self.money_reference_data_dir.is_absolute()
        ):
            raise ValueError("reference data mount must be an absolute path")
        return self


class AssetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Plan(AssetModel):
    """Server-side allowances, never prices or a promise of live availability."""

    plan_id: PlanId
    research_jobs_per_period: int = Field(ge=0, le=100_000)
    seats: int = Field(ge=1, le=10_000)
    retention_days: int = Field(ge=1, le=3650)
    live_research: bool
    evidence_explorer: bool
    audit_export: bool
    monthly_tokens: int = Field(ge=0, le=100_000_000)
    max_job_tokens: int = Field(ge=0, le=1_000_000)
    max_job_seconds: int = Field(ge=30, le=14400)

    @model_validator(mode="after")
    def bounded_budget(self) -> Self:
        if self.max_job_tokens > self.monthly_tokens:
            raise ValueError("per-job tokens exceed monthly allowance")
        if self.live_research and not self.max_job_tokens:
            raise ValueError("live allowance requires a positive token ceiling")
        if self.plan_id == "FREE" and (self.live_research or self.monthly_tokens):
            raise ValueError("FREE is synthetic only and cannot incur inference tokens")
        return self


class ProviderLicence(AssetModel):
    provider: ProviderId
    licence: NonEmpty
    source: NonEmpty
    commercial_use: Literal["APPROVED", "UNKNOWN", "PROHIBITED"]
    redistribution: Literal["APPROVED", "UNKNOWN", "PROHIBITED"]
    storage_restrictions: NonEmpty
    retention_restrictions: NonEmpty
    attribution: NonEmpty
    credential_requirement: NonEmpty
    qualification_status: Literal[
        "READY",
        "MISSING_CREDENTIAL",
        "MISSING_RUNTIME",
        "MISSING_DATA",
        "MISSING_LICENCE",
        "MISSING_QUALIFICATION",
        "FAILED",
    ]
    review_reference: str = Field(max_length=2000)
    review_expires_on: str = Field(max_length=10)
    notes: NonEmpty

    @model_validator(mode="after")
    def reviewed_approval(self) -> Self:
        if self.redistribution == "APPROVED" and self.commercial_use != "APPROVED":
            raise ValueError("redistribution requires commercial-use approval")
        if self.commercial_use == "APPROVED":
            if not self.review_reference or not self.review_expires_on:
                raise ValueError("approval requires a review reference and review expiry")
        if self.review_expires_on:
            _iso_date(self.review_expires_on)
        return self


class ProviderDataset(AssetModel):
    mapping_id: Identifier
    provider: ProviderId
    dataset: Literal[
        "eligibility",
        "instrument",
        "ohlcv",
        "financial",
        "filing",
        "announcement",
        "news",
        "corporate_action",
        "spread",
        "ethics",
        "synthetic",
    ]
    purpose: NonEmpty


class Exclusion(AssetModel):
    activity: Identifier
    decision: Literal["REJECT"]
    reason: NonEmpty


class TableEntry(AssetModel):
    table_name: Identifier
    path: NonEmpty
    schema_path: NonEmpty
    metadata_path: NonEmpty


class Manifest(AssetModel):
    schema_version: int = Field(ge=1, le=1)
    tables: tuple[TableEntry, ...] = Field(min_length=1, max_length=64)


class Metadata(AssetModel):
    table_name: Identifier
    schema_version: int = Field(ge=1, le=1)
    data_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    source: NonEmpty
    source_date: str
    updated_at: str
    checksum: SHA256
    schema_checksum: SHA256
    owner: NonEmpty
    licence: NonEmpty
    provenance: NonEmpty

    @model_validator(mode="after")
    def valid_dates(self) -> Self:
        source_date = _iso_date(self.source_date)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", self.updated_at):
            raise ValueError("updated_at must be a UTC ISO timestamp")
        updated_at = datetime.fromisoformat(self.updated_at)
        if source_date > updated_at.date():
            raise ValueError("source date cannot follow updated_at")
        return self


ROW_MODELS: Mapping[str, type[AssetModel]] = MappingProxyType(
    {
        "plans": Plan,
        "provider_licences": ProviderLicence,
        "provider_datasets": ProviderDataset,
        "exclusions": Exclusion,
    }
)
PRIMARY_KEYS = MappingProxyType(
    {
        "plans": "plan_id",
        "provider_licences": "provider",
        "provider_datasets": "mapping_id",
        "exclusions": "activity",
    }
)


@dataclass(frozen=True)
class LoadedTable:
    entry: TableEntry
    metadata: Metadata
    rows: tuple[AssetModel, ...]


@dataclass(frozen=True)
class ReferenceCatalog:
    """A startup-validated, immutable snapshot of release-owned tables."""

    tables: Mapping[str, LoadedTable]

    def plan(self, plan_id: str) -> Plan:
        for row in self.tables["plans"].rows:
            if isinstance(row, Plan) and row.plan_id == plan_id:
                return row
        raise ReferenceTableError("UNKNOWN_PLAN")

    def diagnostics(self) -> list[dict[str, str | int]]:
        return [
            {
                "table_name": name,
                "path": table.entry.path,
                "schema_version": table.metadata.schema_version,
                "data_version": table.metadata.data_version,
                "checksum": table.metadata.checksum,
                "rows": len(table.rows),
            }
            for name, table in sorted(self.tables.items())
        ]

    def require_commercial_use(
        self,
        provider: str,
        *,
        redistribution: bool = False,
        as_of: date | None = None,
    ) -> ProviderLicence:
        """Deny unknown/expired rights; this does not confer data qualification."""
        at = as_of or datetime.now(UTC).date()
        for row in self.tables["provider_licences"].rows:
            if isinstance(row, ProviderLicence) and row.provider == provider:
                if (
                    row.commercial_use != "APPROVED"
                    or not row.review_expires_on
                    or at > _iso_date(row.review_expires_on)
                    or (redistribution and row.redistribution != "APPROVED")
                ):
                    raise CommercialLicenceError("MISSING_LICENCE")
                return row
        raise CommercialLicenceError("MISSING_LICENCE")


def default_data_root() -> Path:
    """Resolve the release's data directory independent of the working directory.

    Wheel/OCI installs use MONEY_REFERENCE_DATA_DIR. Editable source installs use
    the repository data folder. Neither case reads relative to process cwd.
    """
    try:
        configured = ReferenceAssetSettings().money_reference_data_dir
    except ValidationError as exc:
        raise ReferenceTableError("invalid reference data mount configuration") from exc
    if configured is not None:
        return configured
    location = Path(__file__).resolve()
    if location.parents[2].name != "src":
        raise ReferenceTableError("wheel installations require MONEY_REFERENCE_DATA_DIR")
    return location.parents[3] / "data"


def _iso_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _bounded_read(root: Path, relative: str) -> bytes:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or not path.parts:
        raise ReferenceTableError("unsafe reference asset path")
    full = root / path
    if any(part.is_symlink() for part in (full, *full.parents) if part != root.parent):
        raise ReferenceTableError("reference assets cannot be symlinks")
    if not full.resolve().is_relative_to(root):
        raise ReferenceTableError("reference asset escapes release directory")
    try:
        with full.open("rb") as handle:
            payload = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise ReferenceTableError(f"reference asset unreadable: {relative}") from exc
    if not payload or len(payload) > MAX_FILE_BYTES:
        raise ReferenceTableError(f"reference asset size invalid: {relative}")
    return payload


def _json(payload: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReferenceTableError("duplicate JSON property")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ReferenceTableError("non-finite JSON number")

    try:
        return json.loads(_text(payload), object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise ReferenceTableError("invalid reference JSON") from exc


def _text(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReferenceTableError("reference assets must be UTF-8") from exc
    if "\x00" in text or text.startswith("\ufeff"):
        raise ReferenceTableError("reference assets cannot contain NUL or BOM")
    return text


def _rows(payload: bytes, suffix: str, model: type[AssetModel]) -> tuple[AssetModel, ...]:
    if suffix == ".json":
        values = _json(payload)
        if not isinstance(values, list):
            raise ReferenceTableError("table JSON must be an array")
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(_text(payload), newline=""), strict=True)
        expected = list(model.model_fields)
        if reader.fieldnames != expected:
            raise ReferenceTableError("CSV columns must exactly match the versioned schema order")
        values = []
        properties = model.model_json_schema()["properties"]
        try:
            for row in reader:
                if len(values) >= MAX_ROWS or set(row) != set(expected):
                    raise ReferenceTableError("CSV row shape/count invalid")
                typed: dict[str, Any] = {}
                for field, value in row.items():
                    kind = properties[field].get("type")
                    if value is None:
                        raise ReferenceTableError("missing CSV field")
                    if kind == "integer":
                        if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value):
                            raise ReferenceTableError(
                                "CSV integer is not canonical nonnegative int"
                            )
                        typed[field] = int(value)
                    elif kind == "boolean":
                        if value not in ("true", "false"):
                            raise ReferenceTableError("CSV boolean must be true or false")
                        typed[field] = value == "true"
                    else:
                        typed[field] = value
                values.append(typed)
        except csv.Error as exc:
            raise ReferenceTableError("malformed CSV") from exc
    else:
        raise ReferenceTableError("unsupported reference table format")
    if not values or len(values) > MAX_ROWS:
        raise ReferenceTableError("reference row count invalid")
    return tuple(model.model_validate(value) for value in values)


def load_reference_catalog(data_root: Path | None = None) -> ReferenceCatalog:
    """Validate every required table atomically; no partial or fallback catalog."""
    root = (data_root or default_data_root()).resolve()
    try:
        raw_manifest = _json(_bounded_read(root, "manifest.json"))
        # JSON array -> immutable tuple conversion is explicit; all values remain strict.
        if not isinstance(raw_manifest, dict) or not isinstance(raw_manifest.get("tables"), list):
            raise ReferenceTableError("invalid reference manifest")
        raw_manifest["tables"] = tuple(raw_manifest["tables"])
        manifest = Manifest.model_validate(raw_manifest)
        names = [entry.table_name for entry in manifest.tables]
        if len(set(names)) != len(names) or set(names) != set(ROW_MODELS):
            raise ReferenceTableError("required reference table missing, duplicate or unsupported")
        tables: dict[str, LoadedTable] = {}
        paths: set[str] = set()
        for entry in sorted(manifest.tables, key=lambda item: item.table_name):
            for path in (entry.path, entry.schema_path, entry.metadata_path):
                if path in paths:
                    raise ReferenceTableError("reference asset reused across tables")
                paths.add(path)
            metadata = Metadata.model_validate(_json(_bounded_read(root, entry.metadata_path)))
            if metadata.table_name != entry.table_name:
                raise ReferenceTableError("reference metadata table mismatch")
            payload = _bounded_read(root, entry.path)
            schema_bytes = _bounded_read(root, entry.schema_path)
            if hashlib.sha256(payload).hexdigest() != metadata.checksum:
                raise ReferenceTableError(f"reference checksum mismatch: {entry.table_name}")
            if hashlib.sha256(schema_bytes).hexdigest() != metadata.schema_checksum:
                raise ReferenceTableError(f"schema checksum mismatch: {entry.table_name}")
            model = ROW_MODELS[entry.table_name]
            if _json(schema_bytes) != model.model_json_schema():
                raise ReferenceTableError("schema incompatible with application version")
            rows = _rows(payload, Path(entry.path).suffix, model)
            primary_key = PRIMARY_KEYS[entry.table_name]
            keys = [getattr(row, primary_key) for row in rows]
            if len(set(keys)) != len(keys):
                raise ReferenceTableError(f"duplicate primary key: {entry.table_name}")
            tables[entry.table_name] = LoadedTable(entry, metadata, rows)
        _relationships(tables)
        return ReferenceCatalog(MappingProxyType(tables))
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ReferenceTableError):
            raise
        # Do not echo raw rows or paths from an invalid asset into customer errors.
        raise ReferenceTableError("reference table validation failed") from exc


def _relationships(tables: dict[str, LoadedTable]) -> None:
    plan_ids = {row.plan_id for row in tables["plans"].rows if isinstance(row, Plan)}
    if plan_ids != {"FREE", "PRO", "TEAM", "ENTERPRISE"}:
        raise ReferenceTableError("all supported plans must have a versioned definition")
    providers = {
        row.provider for row in tables["provider_licences"].rows if isinstance(row, ProviderLicence)
    }
    pairs: set[tuple[str, str]] = set()
    for row in tables["provider_datasets"].rows:
        assert isinstance(row, ProviderDataset)
        if row.provider not in providers:
            raise ReferenceTableError("provider dataset refers to missing licence record")
        pair = (row.provider, row.dataset)
        if pair in pairs:
            raise ReferenceTableError("duplicate provider/dataset mapping")
        pairs.add(pair)
    exclusions = {row.activity for row in tables["exclusions"].rows if isinstance(row, Exclusion)}
    if not set(EXCLUDED_ACTIVITIES).issubset(exclusions):
        raise ReferenceTableError("a versioned table cannot remove mandate exclusions")


def compare_reference_releases(previous: ReferenceCatalog, current: ReferenceCatalog) -> None:
    """Reject silent content replacement or rollback labelled as the same version.

    A deployment rollback uses its complete earlier release and is deliberately
    distinct from releasing older data under a new product commit.
    """
    for name, table in previous.tables.items():
        if name not in current.tables:
            raise ReferenceTableError("a data release cannot silently drop a table")
        before = table.metadata
        after = current.tables[name].metadata
        before_version = tuple(int(part) for part in before.data_version.split("."))
        after_version = tuple(int(part) for part in after.data_version.split("."))
        changed = (
            before.checksum != after.checksum or before.schema_checksum != after.schema_checksum
        )
        if after_version < before_version or (changed and after_version == before_version):
            raise ReferenceTableError("changed data requires a strictly newer data version")
        if changed and after.updated_at <= before.updated_at:
            raise ReferenceTableError("changed data requires a later update timestamp")
