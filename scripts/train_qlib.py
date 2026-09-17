"""Run pinned native Qlib on an archived dataset; never auto-promote the output."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from money.adapters.upstream import UpstreamUnavailable
from money.models.qlib_training import train_qlib
from money.models.training import (
    MAXIMUM_DATASET_BYTES,
    TrainingConfiguration,
    TrainingDataset,
    load_training_json,
    write_training_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train an UNPROMOTED pinned native Qlib model")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        dataset = load_training_json(arguments.dataset, TrainingDataset, maximum_bytes=MAXIMUM_DATASET_BYTES)
        configuration = load_training_json(arguments.config, TrainingConfiguration, maximum_bytes=32_000)
        if dataset.origin != "ARCHIVED_EVIDENCE":
            raise ValueError("operator training requires archived evidence")
        result = train_qlib(dataset, configuration)
        write_training_result(result, arguments.output)
    except (OSError, ValueError, UpstreamUnavailable):
        print("QLIB_TRAINING_FAILED: native runtime or archived inputs are unavailable, invalid or insufficient.", file=sys.stderr)
        return 2
    print(f"UNPROMOTED artifact_sha256={result.artifact_hash} validation_sha256={result.validation_report_hash}")
    print("Independent source/PIT review, regression qualification and manual promotion remain required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
