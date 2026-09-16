#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Indexing Money..."
cd "$ROOT"
graphify extract . --code-only

REPOS=(
    "upstreams/tradingagents"
    "upstreams/qlib"
    "upstreams/lean"
    "upstreams/crewai"
    "upstreams/rdagent"
    "upstreams/ta-lib-python"
    "upstreams/quantstats"
    "upstreams/stream-read-xbrl"
    "upstreams/yfinance"
)

for repo in "${REPOS[@]}"; do
    if [ -d "$ROOT/$repo" ]; then
        echo ""
        echo "Indexing $repo"
        (
            cd "$ROOT/$repo"
            graphify extract . --code-only
        )
    fi
done

echo ""
echo "Graphify code indexes refreshed."
