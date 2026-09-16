#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: > "$ROOT/UPSTREAM_LOCK.txt"

for repo in "$ROOT"/upstreams/*; do
    if [ -d "$repo/.git" ]; then
        NAME="$(basename "$repo")"
        URL="$(git -C "$repo" remote get-url origin)"
        SHA="$(git -C "$repo" rev-parse HEAD)"
        printf "%-24s %-75s %s\n" "$NAME" "$URL" "$SHA" >> "$ROOT/UPSTREAM_LOCK.txt"
    fi
done

: > "$ROOT/REFERENCE_LOCK.txt"

for repo in "$ROOT"/optional_reference/*; do
    if [ -d "$repo/.git" ]; then
        NAME="$(basename "$repo")"
        URL="$(git -C "$repo" remote get-url origin)"
        SHA="$(git -C "$repo" rev-parse HEAD)"
        printf "%-24s %-75s %s\n" "$NAME" "$URL" "$SHA" >> "$ROOT/REFERENCE_LOCK.txt"
    fi
done

echo "Lock files refreshed."
