#!/usr/bin/env bash
# Install or move the Hermes checkout to a given revision and sync its venv.
#   hermes-install.sh <rev> <prefix> [extras...]
# <rev> may be a commit, tag or branch. Prints the resolved commit on stdout.
set -euo pipefail

rev="$1"
prefix="$2"
shift 2
extras=("$@")
dir="$prefix/hermes"
url="https://github.com/NousResearch/hermes-agent.git"
python="${PYTHON:-3.14}"

log() { printf '%s\n' "$*" >&2; }

if ! command -v uv >/dev/null; then
    log "uv not found on PATH"
    exit 1
fi

if [ ! -d "$dir/.git" ]; then
    log "cloning hermes-agent into $dir"
    mkdir -p "$prefix"
    git clone --quiet --filter=blob:none --no-checkout "$url" "$dir"
fi

log "fetching $rev"
git -C "$dir" fetch --quiet --filter=blob:none origin "$rev"
commit="$(git -C "$dir" rev-parse FETCH_HEAD)"
git -C "$dir" -c advice.detachedHead=false checkout --quiet --detach "$commit"

extra_args=()
for e in "${extras[@]}"; do extra_args+=(--extra "$e"); done

# --frozen: the upstream lockfile decides every version. Its supported
# environments start at Python 3.14, and it uses relative exclude-newer
# windows, which old uv (0.8.x) cannot parse.
log "syncing venv (python $python)"
(cd "$dir" && uv sync --quiet --frozen --python "$python" "${extra_args[@]}" >&2)

echo "$commit"
