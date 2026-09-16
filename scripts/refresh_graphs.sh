#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Updating Money's local graph (upstream indexes are read-only)..."
cd "$ROOT"
graphify update .
