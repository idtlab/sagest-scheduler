#!/usr/bin/env bash
# Build, then run every unit test, including real euler-miniapp campaigns through Flux, in a
# one-node pdebug allocation. Fails if any test is skipped.
# Usage: scripts/tuolumne/test.sh BANK [extra flux alloc args]
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
bank=${1:?usage: test.sh BANK [flux alloc args]}
shift
"$ROOT/scripts/tuolumne/build.sh"
source "$ROOT/scripts/tuolumne/env.sh"
source "$ROOT/build/tuolumne/env.sh"
cd "$ROOT"
log=$(mktemp)
trap 'rm -f "$log"' EXIT
status=0
flux alloc -N1 -q pdebug -t 30m -B "$bank" "$@" \
    python3 -m unittest discover -s tests -v 2>&1 | tee "$log" || status=$?
if grep -q "skipped" "$log"; then
    echo "error: some tests were skipped" >&2
    exit 1
fi
exit "$status"
