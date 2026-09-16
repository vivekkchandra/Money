"""Allowlisted JSON events; no exception text, credentials or provider payloads."""

import json
import logging
from datetime import UTC, datetime

FIELDS = (
    "request_id",
    "workspace_id",
    "environment",
    "service",
    "version",
    "git_sha",
    "research_id",
    "job_id",
    "worker_id",
    "stage",
    "status",
    "duration_seconds",
    "provider",
    "model",
    "retryable",
    "failure_class",
)


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, str | int | float | bool] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in FIELDS:
            value = getattr(record, field, None)
            if isinstance(value, (str, int, float, bool)):
                event[field] = value
        return json.dumps(event, separators=(",", ":"))


def configure_logging() -> None:
    logger = logging.getLogger("money")
    if any(isinstance(handler.formatter, SafeJsonFormatter) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
