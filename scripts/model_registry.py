"""Local operator tooling; no remote code execution or automatic model promotion."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from money.adapters.native_qlib import load_qualified_model
from money.api.settings import OperatorSettings
from money.models.registry import ModelRegistry
from money.performance.outcomes import OutcomeBar
from money.research.outcomes import evaluate_signal_outcome
from money.research.replay import replay_decision
from money.storage import ResearchStore


def bounded_bytes(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Input must be a bounded regular file")
    return path.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Money administrator-only model and replay operations"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--model", type=Path, required=True)
    register.add_argument("--artifact-hash", required=True)
    register.add_argument("--report", action="append", default=[], metavar="HASH=PATH")
    promote = commands.add_parser("promote")
    promote.add_argument("--model-id", required=True)
    promote.add_argument("--reviewer", required=True)
    promote.add_argument("--manual", required=True, action="store_true")
    promote.add_argument("--rollback-target")
    withdraw = commands.add_parser("withdraw")
    withdraw.add_argument("--model-id", required=True)
    withdraw.add_argument("--reviewer", required=True)
    withdraw.add_argument("--reason", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--research-id", required=True)
    outcomes = commands.add_parser("outcomes")
    outcomes.add_argument("--research-id", required=True)
    outcomes.add_argument("--bars", type=Path, required=True)
    outcomes.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    outcomes.add_argument("--dataset-version", required=True)
    outcomes.add_argument("--adjustment-basis", choices=["UNADJUSTED_NO_ACTIONS"], required=True)
    args = parser.parse_args()
    settings = OperatorSettings()  # type: ignore[call-arg]
    store = ResearchStore(
        settings.database_url.get_secret_value(),
        allow_sqlite=settings.money_env in {"development", "test"},
        workspace_id=settings.money_workspace_id,
    )
    registry = ModelRegistry(store)
    try:
        if args.command == "register":
            model = load_qualified_model(args.model, args.artifact_hash)
            reports = {}
            for item in args.report:
                report_hash, filename = item.split("=", 1)
                reports[report_hash] = bounded_bytes(Path(filename), 2_000_000)
            result = {"registered": registry.register(model, reports), "activated": False}
        elif args.command == "promote":
            result = {
                "promotion_event": registry.promote(
                    args.model_id,
                    reviewer_id=args.reviewer,
                    manual=args.manual,
                    rollback_target=args.rollback_target,
                )
            }
        elif args.command == "withdraw":
            registry.withdraw(args.model_id, reviewer_id=args.reviewer, reason=args.reason)
            result = {"withdrawn": args.model_id}
        elif args.command == "replay":
            result = replay_decision(
                store,
                args.research_id,
                money_version=settings.money_version,
                git_sha=settings.money_git_sha,
            )
        else:
            bars = TypeAdapter(tuple[OutcomeBar, ...]).validate_json(
                bounded_bytes(args.bars, 2_000_000)
            )
            result = evaluate_signal_outcome(
                store,
                args.research_id,
                bars,
                args.as_of,
                adjustment_basis=args.adjustment_basis,
                dataset_version=args.dataset_version,
            )
        print(json.dumps(result, separators=(",", ":")))
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    main()
