#!/bin/sh
# Local personal research with Railway-injected credentials; never deployment.
set -eu
if [ "$#" -ne 1 ]; then
    echo 'Usage: sh scripts/refresh_personal.sh ISIN' >&2
    exit 2
fi
case "$1" in
    *[!A-Z0-9]*|'') echo 'Invalid ISIN' >&2; exit 2 ;;
esac
if [ "${#1}" -ne 12 ]; then
    echo 'Invalid ISIN' >&2
    exit 2
fi
money_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$money_script_dir/.."
export MONEY_USAGE_MODE=personal_research
export MONEY_QLIB_ENABLED=false
export MONEY_ISSUER_SOURCE_POLICY=official_disclosures
export MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json
# Company capture is optional, not an admission prerequisite.
exec uv run python scripts/run_research_testing.py --isin "$1"
