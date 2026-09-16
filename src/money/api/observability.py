"""Allowlisted JSON events; no exception text, credentials or provider payloads."""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

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
    "exception_type",
    "error_location",
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def failure_context(error: BaseException) -> dict[str, str]:
    """Locate failures without serializing exception messages, locals or source text.

    Only Money-owned filenames beneath this installed package are emitted. External
    traceback paths can include customer filenames, URLs or credentials, so they
    are omitted. Exception causes are not traversed or formatted.
    """
    name = type(error).__name__
    context = {
        "exception_type": name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name)
        else "ExternalError"
    }
    trace = error.__traceback__
    inspected = 0
    while trace is not None and inspected < 100:
        filename = Path(trace.tb_frame.f_code.co_filename)
        try:
            relative = filename.relative_to(_PACKAGE_ROOT)
        except ValueError:
            pass
        else:
            path = relative.as_posix()
            if (
                ".." not in relative.parts
                and re.fullmatch(r"[A-Za-z0-9_/]{1,180}\.py", path)
                and 0 < trace.tb_lineno < 1_000_000
            ):
                context["error_location"] = f"money/{path}:{trace.tb_lineno}"
        trace = trace.tb_next
        inspected += 1
    return context


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
