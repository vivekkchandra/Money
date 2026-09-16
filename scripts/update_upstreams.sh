#!/usr/bin/env bash
set -e

for repo in upstreams/*; do
    if [ -d "$repo/.git" ]; then
        echo "Updating $repo"
        git -C "$repo" pull --ff-only
    fi
done

: > UPSTREAM_LOCK.txt

for repo in upstreams/*; do
    if [ -d "$repo/.git" ]; then
        NAME="$(basename "$repo")"
        URL="$(git -C "$repo" remote get-url origin)"
        SHA="$(git -C "$repo" rev-parse HEAD)"
        printf "%-22s %-70s %s\n" "$NAME" "$URL" "$SHA" >> UPSTREAM_LOCK.txt
    fi
done
