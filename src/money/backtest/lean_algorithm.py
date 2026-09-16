"""Fixed LEAN event consumer. Mounted read-only by Money's isolated runner."""

import csv
import importlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

native = importlib.import_module("AlgorithmImports")
study = importlib.import_module("money_study")


class MoneySnapshotBar(native.PythonData):  # type: ignore[name-defined]
    def get_source(self, config: Any, date: datetime, is_live_mode: bool) -> Any:
        if is_live_mode:
            raise ValueError("Money forbids live engine mode")
        return native.SubscriptionDataSource(
            "/money/input/bars.csv", native.SubscriptionTransportMedium.LOCAL_FILE
        )

    def reader(self, config: Any, line: str, date: datetime, is_live_mode: bool) -> Any:
        if is_live_mode:
            raise ValueError("Money forbids live engine mode")
        if not line or line.startswith("time,"):
            return None
        row = next(csv.reader([line]))
        item = MoneySnapshotBar()
        item.symbol = config.symbol
        item.time = datetime.fromisoformat(row[0]).replace(tzinfo=None)
        item.end_time = item.time + timedelta(days=1)
        item.value = float(row[4])
        for key, value in zip(("Open", "High", "Low", "Close", "Volume"), row[1:6], strict=True):
            item[key] = float(value)
        item["AvailableAt"] = row[6]
        return item


class MoneyEvidenceValidation(native.QCAlgorithm):  # type: ignore[name-defined]
    def initialize(self) -> None:
        if self.live_mode:
            raise ValueError("Money forbids live engine mode")
        self._input = json.loads(Path("/money/input/study.json").read_text())
        self._bars: list[dict[str, Any]] = []
        start = datetime.fromisoformat(self._input["start_date"])
        end = datetime.fromisoformat(self._input["end_date"]) + timedelta(days=2)
        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self._symbol = self.add_data(MoneySnapshotBar, "MONEY", native.Resolution.DAILY).symbol

    def on_data(self, data: Any) -> None:
        if not data.contains_key(self._symbol):
            return
        bar = data[self._symbol]
        self._bars.append({
            "time": bar.time.isoformat() + "+00:00", "available_at": str(bar["AvailableAt"]),
            "open": float(bar["Open"]), "high": float(bar["High"]),
            "low": float(bar["Low"]), "close": float(bar["Close"]),
            "volume": float(bar["Volume"]),
        })

    def on_end_of_algorithm(self) -> None:
        result = study.evaluate_study(self._bars, self._input["parameters"])
        result.update({"run_id": self._input["run_id"], "dataset_hash": self._input["dataset_hash"],
                       "snapshot_hash": self._input["snapshot_hash"],
                       "parameter_hash": self._input["parameter_hash"]})
        Path("/money/output/result.json").write_text(json.dumps(result, allow_nan=False))
