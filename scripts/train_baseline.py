"""Offline, unpromoted Money baseline experiment. Never contacts the production API."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from money.models.training import (
    MAXIMUM_DATASET_BYTES,
    TrainingConfiguration,
    TrainingDataset,
    load_training_json,
    train_baseline,
    write_training_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train an OFFLINE UNPROMOTED Money numeric baseline")
    parser.add_argument("--dataset", type=Path, required=True, help="Bounded archived TrainingDataset JSON")
    parser.add_argument("--config", type=Path, required=True, help="Predeclared TrainingConfiguration JSON")
    parser.add_argument("--output", type=Path, required=True, help="New experiment directory; must not already exist")
    arguments = parser.parse_args()
    try:
        dataset = load_training_json(arguments.dataset, TrainingDataset, maximum_bytes=MAXIMUM_DATASET_BYTES)
        config = load_training_json(arguments.config, TrainingConfiguration, maximum_bytes=32_000)
        result = train_baseline(dataset, config)
        write_training_result(result, arguments.output)
    except (OSError, ValueError):
        # No raw input/model rows or credentials in stdout, even on malformed JSON.
        print("TRAINING_FAILED: invalid, insufficient, unavailable or conflicting experiment inputs/output.", file=sys.stderr)
        return 2
    print(f"UNPROMOTED artifact_sha256={result.artifact_hash} validation_sha256={result.validation_report_hash}")
    print("Independent source/PIT review, regression qualification and manual promotion are still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
