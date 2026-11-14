#!/usr/bin/env bash
# Build, then run every test inside a two-broker Flux instance. Fails if any test skipped,
# since the Flux and euler-miniapp tests skip themselves when Flux or the build is missing.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
"$ROOT/scripts/ubuntu-runner/build.sh"
source "${BUILD_DIR:-$ROOT/build/ubuntu-runner}/env.sh"
log=$(mktemp)
trap 'rm -f "$log"' EXIT
cd "$ROOT"
flux start --test-size=2 python3 -m unittest discover -s tests -v 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
if grep -q 'skipped' "$log"; then
    echo "error: some tests were skipped" >&2
    exit 1
fi
exit "$status"
