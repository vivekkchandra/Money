"""Failure diagnostics locate Money code without exposing exception content."""

import json
import logging
from pathlib import Path

from money.api.observability import SafeJsonFormatter, failure_context
from money.data.identifiers import InstrumentIdentifiers


def test_failure_diagnostics_exclude_messages_locals_and_external_paths():
    secret = "private-password-do-not-log"
    try:
        raise ValueError(secret)
    except ValueError as error:
        context = failure_context(error)
        record = logging.LogRecord("money.worker", logging.ERROR, __file__, 1,
                                   "research_failure", (), (type(error), error, error.__traceback__))
        for name, value in context.items():
            setattr(record, name, value)
        output = SafeJsonFormatter().format(record)
    assert context == {"exception_type": "ValueError"}
    assert secret not in output
    assert str(Path(__file__).parent) not in output
    assert "Traceback" not in output
    assert json.loads(output)["exception_type"] == "ValueError"


def test_failure_diagnostics_locate_only_money_owned_source():
    # An unbound call is sufficient to reach the production timestamp guard,
    # without constructing or fabricating a qualified instrument.
    from datetime import datetime

    try:
        InstrumentIdentifiers.require_current(None, datetime(2026, 1, 1))  # type: ignore[arg-type]
    except ValueError as error:
        context = failure_context(error)
    assert context["exception_type"] == "ValueError"
    assert context["error_location"].startswith("money/data/identifiers.py:")
    assert "IDENTIFIER_TIMESTAMP_INVALID" not in str(context)


def test_untrusted_exception_type_cannot_inject_log_metadata():
    exception = type("bad\nname:credential", (Exception,), {})
    assert failure_context(exception("secret")) == {"exception_type": "ExternalError"}
