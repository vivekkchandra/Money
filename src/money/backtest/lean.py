"""Digest-pinned OCI LEAN runner, with no arbitrary algorithm/command input."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from money.adapters.native import NativeDeadline
from money.adapters.upstream import (
    InvalidUpstreamReport,
    UnsupportedSnapshotData,
    UpstreamUnavailable,
)
from money.schemas.contracts import (
    Contract,
    EvidenceRecord,
    FirmReport,
    LeanValidationReport,
    PriceBar,
    ResearchSnapshot,
    content_hash,
)
from money.signals.generation import SignalPolicy

LEAN_SHA = "f9107abdf26121c5ce159f561bd27fead01d30e1"


def historical_dataset_hash(records: Sequence[EvidenceRecord]) -> str:
    """Hash economic history/provenance, not its fresh job-specific envelope.

    An independently approved archive can be rebound to a new snapshot without
    invalidating its attestation. Prices, raw currency, source identity, original
    observation/publication availability and PIT/conflict flags remain sealed.
    A new retrieval timestamp is explicitly not proof of earlier availability.
    """
    if not records or any(not isinstance(record.payload, PriceBar) for record in records):
        raise UnsupportedSnapshotData("historical dataset requires only price-bar evidence")
    ordered = sorted(
        records,
        key=lambda record: (
            record.observation_time,
            record.provider,
            record.canonical_source_id,
            record.source_id,
        ),
    )
    return content_hash(
        {
            "version": "money-historical-dataset-v1",
            "records": [
                {
                    "source": record.source,
                    "provider": record.provider,
                    "source_id": record.source_id,
                    "canonical_source_id": record.canonical_source_id,
                    "observation_time": record.observation_time.astimezone(UTC).isoformat(),
                    "publication_time": (
                        record.publication_time.astimezone(UTC).isoformat()
                        if record.publication_time
                        else None
                    ),
                    "pit_safe": record.pit_safe,
                    "conflicting": record.conflicting,
                    "payload": record.payload.model_dump(mode="json"),
                }
                for record in ordered
            ],
        }
    )


class LeanCostAssumptions(Contract):
    version: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=2000)
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    round_trip_cost_bps: float = Field(ge=0, lt=10000)
    spread_bps: float = Field(ge=0, lt=10000)
    slippage_bps: float = Field(ge=0, lt=10000)
    applicability_reasons: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.effective_to <= self.effective_from:
            raise ValueError("cost effective interval is reversed")
        if self.round_trip_cost_bps < self.spread_bps + self.slippage_bps:
            raise ValueError("total cost cannot omit spread or slippage")
        return self


class LeanStudyParameters(Contract):
    version: Literal["money-momentum-study-v1"] = "money-momentum-study-v1"
    horizon_days: int = Field(default=5, ge=1, le=30)
    target_fraction: float = Field(default=0.1, gt=0, le=1)
    invalidation_fraction: float = Field(default=0.05, gt=0, lt=1)
    momentum_threshold: float = Field(default=0, ge=-1, le=1)
    minimum_oos_observations: int = Field(default=30, ge=30)
    maximum_drawdown: float = Field(default=0.25, gt=0, lt=1)
    scenario_policy: SignalPolicy | None = None

    @model_validator(mode="after")
    def scenario_consistency(self) -> Self:
        if self.scenario_policy and self.scenario_policy.horizon_days != self.horizon_days:
            raise ValueError("LEAN study horizon differs from the signal scenario policy")
        return self


class LeanStudyQualification(Contract):
    """Administrative attestation of the independent historical study controls.

    Hashes must reference archived audit artifacts; merely constructing this
    object is not production qualification. Runtime stays insufficient until the
    host has verified these references and passes an approved attestation.
    """

    parameter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    historical_eligibility_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    survivorship_audit_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    corporate_action_audit_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    adjustment_policy: Literal["unadjusted_no_actions", "split_adjusted_total_return"]
    approved_by: str = Field(min_length=1, max_length=100)
    approved_at: AwareDatetime


class LeanContainerSettings(Contract):
    image: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9./_:-]*@sha256:[0-9a-f]{64}$")
    timeout_seconds: int = Field(default=180, ge=1, le=1800)
    memory_mb: int = Field(default=2048, ge=256, le=16384)
    cpus: float = Field(default=1, gt=0, le=8)
    maximum_bars: int = Field(default=20000, ge=100, le=100000)


class LeanFold(Contract):
    observations: int = Field(ge=0)
    mean_return: float | None


class LeanSensitivity(LeanFold):
    changes: dict[str, float]
    maximum_drawdown: float = Field(ge=0, le=1)


class LeanCase(Contract):
    time: str = Field(max_length=100)
    # JSON uses the descriptive return key; Python reserves that keyword.
    measured_return: float = Field(alias="return")
    mae: float
    mfe: float
    target_day: int | None = Field(ge=1, le=30)
    invalidation_day: int | None = Field(ge=1, le=30)
    regime: Literal["positive_slow_trend", "nonpositive_slow_trend"]


class LeanEngineResult(Contract):
    run_id: str = Field(pattern=r"^money-lean-[0-9a-f]{32}$")
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations: int = Field(ge=0)
    bars_received: int = Field(ge=0)
    mean_return: float | None
    maximum_drawdown: float = Field(ge=0, le=1)
    mae: float | None
    mfe: float | None
    target_occurrences: int = Field(ge=0)
    invalidation_occurrences: int = Field(ge=0)
    walk_forward_folds: tuple[LeanFold, ...]
    regimes_observed: tuple[Literal["positive_slow_trend", "nonpositive_slow_trend"], ...]
    cost_stress_mean_return: float | None
    cases: tuple[LeanCase, ...]
    parameter_sensitivity: tuple[LeanSensitivity, ...]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.observations != len(self.cases):
            raise ValueError("LEAN observation count differs from measured cases")
        if sum(fold.observations for fold in self.walk_forward_folds) != self.observations:
            raise ValueError("LEAN fold coverage differs from measured cases")
        if self.observations and (self.mean_return is None or self.cost_stress_mean_return is None):
            raise ValueError("LEAN measured returns are missing")
        return self


def _engine_config() -> dict[str, object]:
    return {
        "environment": "backtesting",
        "live-mode": False,
        "algorithm-type-name": "MoneyEvidenceValidation",
        "algorithm-language": "Python",
        "algorithm-location": "/money/code/lean_algorithm.py",
        "data-folder": "/Lean/Data",
        "results-destination-folder": "/money/output",
        "object-store-root": "/tmp/store",
        "debugging": False,
        "show-missing-data-logs": False,
        "log-handler": "QuantConnect.Logging.ConsoleLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
        "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
        "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
        "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
        "history-provider": [
            "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"
        ],
        "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
        "python-additional-paths": ["/money/code"],
        "maximum-data-points-per-chart-series": 1,
    }


class LeanContainerRunner:
    def __init__(
        self,
        settings: LeanContainerSettings,
        costs: LeanCostAssumptions,
        parameters: LeanStudyParameters | None = None,
        qualification: LeanStudyQualification | None = None,
    ) -> None:
        self.settings, self.costs = settings, costs
        self.parameters = parameters or LeanStudyParameters()
        self.qualification = qualification

    def __call__(
        self, snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...]
    ) -> LeanValidationReport:
        records = sorted(
            (e for e in snapshot.evidence if isinstance(e.payload, PriceBar)),
            key=lambda e: e.observation_time,
        )
        if not records or len(records) > self.settings.maximum_bars:
            raise UnsupportedSnapshotData("LEAN requires a bounded historical bar dataset")
        if any(not e.available_at(snapshot.price_cutoff) or e.conflicting for e in records):
            raise UnsupportedSnapshotData("LEAN input is not point-in-time safe")
        dates = [e.observation_time.astimezone(UTC).date() for e in records]
        if dates != sorted(set(dates)):
            raise UnsupportedSnapshotData("LEAN daily bars have duplicate or unordered sessions")
        if (
            not self.costs.effective_from
            <= records[0].observation_time
            <= snapshot.price_cutoff
            < self.costs.effective_to
        ):
            raise UnsupportedSnapshotData(
                "cost policy does not cover the complete historical interval"
            )
        dataset_hash = historical_dataset_hash(records)
        parameter_hash = content_hash(self.parameters)
        run_id = f"money-lean-{uuid4().hex}"
        executable = shutil.which("docker")
        if executable is None:
            raise UpstreamUnavailable("isolated OCI LEAN runtime is unavailable")
        try:
            inspection = subprocess.run(
                [
                    executable,
                    "image",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "org.money.lean.sha"}}',
                    self.settings.image,
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpstreamUnavailable("LEAN image qualification could not be checked") from exc
        if inspection.returncode or inspection.stdout.decode().strip() != LEAN_SHA:
            raise UpstreamUnavailable(
                "LEAN image is unavailable or its pinned upstream label differs"
            )
        with tempfile.TemporaryDirectory(prefix="money-lean-") as temporary:
            root = Path(temporary)
            incoming, outgoing, code = root / "input", root / "output", root / "code"
            for folder in (incoming, outgoing, code):
                folder.mkdir()
            outgoing.chmod(0o777)  # private 0700 temporary parent; container non-root output mount
            with (incoming / "bars.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("time", "open", "high", "low", "close", "volume", "available_at"))
                for record in records:
                    bar = record.payload
                    assert isinstance(bar, PriceBar)
                    divisor = 100 if bar.currency == "GBX" else 1
                    assert record.publication_time is not None
                    writer.writerow(
                        (
                            record.observation_time.astimezone(UTC).isoformat(),
                            bar.open / divisor,
                            bar.high / divisor,
                            bar.low / divisor,
                            bar.close / divisor,
                            bar.volume,
                            record.publication_time.astimezone(UTC).isoformat(),
                        )
                    )
            inputs = {
                "run_id": run_id,
                "dataset_hash": dataset_hash,
                "snapshot_hash": snapshot.hash,
                "parameter_hash": parameter_hash,
                "start_date": dates[0].isoformat(),
                "end_date": dates[-1].isoformat(),
                "parameters": {
                    **self.parameters.model_dump(mode="json"),
                    "round_trip_cost_bps": self.costs.round_trip_cost_bps,
                },
            }
            (incoming / "study.json").write_text(json.dumps(inputs))
            (incoming / "config.json").write_text(json.dumps(_engine_config()))
            for source, target in (
                ("lean_algorithm.py", "lean_algorithm.py"),
                ("study.py", "money_study.py"),
            ):
                shutil.copyfile(Path(__file__).with_name(source), code / target)
            command = [
                executable,
                "run",
                "--rm",
                "--name",
                run_id,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                f"{os.getuid() or 65532}:{os.getgid() or 65532}",
                "--pids-limit",
                "128",
                "--memory",
                f"{self.settings.memory_mb}m",
                "--memory-swap",
                f"{self.settings.memory_mb}m",
                "--cpus",
                str(self.settings.cpus),
                "--tmpfs",
                "/tmp:rw,nosuid,size=64m",
                "--log-driver",
                "none",
                "--workdir",
                "/Lean/Launcher/bin/Release",
                "--mount",
                f"type=bind,src={incoming},dst=/money/input,readonly",
                "--mount",
                f"type=bind,src={code},dst=/money/code,readonly",
                "--mount",
                f"type=bind,src={outgoing},dst=/money/output",
                "--entrypoint",
                "dotnet",
                self.settings.image,
                "QuantConnect.Lean.Launcher.dll",
                "--config",
                "/money/input/config.json",
            ]
            try:
                execution = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.settings.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise NativeDeadline("LEAN execution deadline exhausted") from exc
            finally:
                # Only this exact randomly-named child is targeted, including after timeout.
                subprocess.run(
                    [executable, "rm", "--force", run_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            if execution.returncode:
                raise UpstreamUnavailable("LEAN engine execution failed")
            result_path = outgoing / "result.json"
            if (
                result_path.is_symlink()
                or not result_path.is_file()
                or result_path.stat().st_size > 2_000_000
            ):
                raise InvalidUpstreamReport("LEAN did not produce bounded result data")
            result = LeanEngineResult.model_validate_json(result_path.read_bytes()).model_dump(
                mode="json", by_alias=True
            )
        for key in ("run_id", "dataset_hash", "snapshot_hash", "parameter_hash"):
            if result.get(key) != inputs[key]:
                raise InvalidUpstreamReport("LEAN result identity or reproducibility hash differs")
        if result.get("bars_received") != len(records):
            raise InvalidUpstreamReport("LEAN did not consume the complete historical dataset")
        qualification = self.qualification
        qualified = bool(
            qualification
            and qualification.dataset_hash == dataset_hash
            and qualification.parameter_hash == parameter_hash
            and qualification.approved_at <= snapshot.created_at
        )
        sample = result["observations"] >= self.parameters.minimum_oos_observations
        folds = result["walk_forward_folds"]
        covered_folds = len(folds) == 3 and all(fold["observations"] >= 10 for fold in folds)
        sensitivity = result["parameter_sensitivity"]
        sensitivity_covered = len(sensitivity) == 6 and all(
            scenario["observations"] >= self.parameters.minimum_oos_observations
            for scenario in sensitivity
        )
        regimes_covered = len(result["regimes_observed"]) >= 2
        findings = [
            json.dumps(
                {
                    "run_id": run_id,
                    "dataset_hash": dataset_hash,
                    "parameter_hash": parameter_hash,
                    "image": self.settings.image,
                    "upstream_sha": LEAN_SHA,
                    "parameters": self.parameters.model_dump(mode="json"),
                    "costs": self.costs.model_dump(mode="json"),
                    "statistics": {key: value for key, value in result.items() if key != "cases"},
                },
                allow_nan=False,
            )
        ]
        state: Literal["PASS", "FAIL", "INSUFFICIENT_EVIDENCE"] = "INSUFFICIENT_EVIDENCE"
        if not qualified:
            findings.append(
                "Historical eligibility, survivorship, corporate actions and the exact "
                "predeclared study require independently verified qualification artifacts."
            )
        elif sample and covered_folds and sensitivity_covered and regimes_covered:
            state = (
                "FAIL"
                if (
                    result["mean_return"] <= 0
                    or result["cost_stress_mean_return"] <= 0
                    or result["maximum_drawdown"] > self.parameters.maximum_drawdown
                    or any(scenario["mean_return"] <= 0 for scenario in sensitivity)
                )
                else "PASS"
            )
        if not sample or not covered_folds:
            findings.append(
                "Insufficient non-overlapping out-of-sample cases in chronological folds."
            )
        if not sensitivity_covered or not regimes_covered:
            findings.append(
                "Parameter sensitivity and at least two slow-trend regimes require adequate observations."
            )
        return LeanValidationReport(
            snapshot_id=snapshot.snapshot_id,
            runner_version="money-lean-oci-v1",
            state=state,
            observations=result["observations"],
            walk_forward=covered_folds,
            out_of_sample=sample,
            pit_safe=True,
            survivorship_checked=qualified,
            costs_included=True,
            sensitivity_checked=sensitivity_covered,
            spread_bps=self.costs.spread_bps,
            slippage_bps=self.costs.slippage_bps,
            maximum_drawdown=result["maximum_drawdown"],
            mae=result["mae"],
            mfe=result["mfe"],
            findings=tuple(findings),
            scenario_policy_hash=content_hash(self.parameters.scenario_policy)
            if self.parameters.scenario_policy is not None
            else None,
        )
