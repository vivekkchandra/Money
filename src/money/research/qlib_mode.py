"""Explicit Qlib selection; unavailable enabled Qlib never silently opts out."""

from collections.abc import Mapping


def qlib_enabled(environ: Mapping[str, str]) -> bool:
    """Read the strict true/false flag, preserving enabled legacy behavior."""
    selected = environ.get("MONEY_QLIB_ENABLED", "true")
    if selected not in {"true", "false"}:
        # Do not echo an accidentally pasted secret or unsupported value.
        raise ValueError("MONEY_QLIB_ENABLED_REQUIRES_TRUE_OR_FALSE")
    return selected == "true"
