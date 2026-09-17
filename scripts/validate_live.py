"""Offline schema/qualification checks; never contacts providers or starts research."""

import argparse
import json
from pathlib import Path

from money.research.live import LiveManifest, load_manifest
from money.schemas.contracts import utc_now


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema", action="store_true", help="Print the exact manifest JSON Schema"
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--sha256")
    arguments = parser.parse_args()
    if arguments.schema:
        print(json.dumps(LiveManifest.model_json_schema(), indent=2))
        return
    if not arguments.manifest or not arguments.sha256:
        parser.error("provide --schema or both --manifest and --sha256")
    manifest = load_manifest(arguments.manifest, arguments.sha256)
    for provider in manifest.provider_qualifications:
        for dataset in provider.datasets:
            provider.require(dataset, utc_now())
    print(
        json.dumps(
            {
                "configuration": "VALIDATED_OFFLINE",
                "production_status": "PRODUCTION BLOCKED",
                "instruments": len(manifest.reviewed_instruments),
                "providers": len(manifest.provider_qualifications),
                "limitation": "No live credentials, native runtimes, models, egress or deployment were qualified.",
            }
        )
    )


if __name__ == "__main__":
    main()
