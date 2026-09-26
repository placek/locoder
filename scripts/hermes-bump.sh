#!/usr/bin/env bash
# Move Hermes to a new revision, keep it only if `make check` passes.
#   hermes-bump.sh <rev> <prefix> <repo> [extras...]
# On success the new commit is written to <repo>/hermes.rev (commit it).
# On failure the checkout and venv go back to the pinned commit.
set -euo pipefail

rev="$1"
prefix="$2"
repo="$3"
shift 3
here="$(cd "$(dirname "$0")" && pwd)"
pinned="$(cat "$repo/hermes.rev")"

new="$("$here/hermes-install.sh" "$rev" "$prefix" "$@")"
if [ "$new" = "$pinned" ]; then
    echo "already at $pinned"
    exit 0
fi

echo "checking $new (pinned: $pinned)"
if make -C "$repo" --no-print-directory check; then
    echo "$new" > "$repo/hermes.rev"
    echo "hermes.rev -> $new; commit it: git -C $repo commit -m 'Bump Hermes to ${new:0:10}' hermes.rev"
else
    echo "check failed on $new; rolling back to $pinned" >&2
    "$here/hermes-install.sh" "$pinned" "$prefix" "$@" >/dev/null
    exit 1
fi
