#!/usr/bin/env bash
# Run every test, including the real euler-miniapp campaign, in a one-node allocation.
# Run scripts/tuolumne/build.sh on a login node first. Not yet tested on Tuolumne.
# Usage: scripts/tuolumne/test.sh [extra flux alloc args, e.g. -B mybank]
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "${BUILD_DIR:-$ROOT/build/tuolumne}/env.sh"
cd "$ROOT"
exec flux alloc -N1 -q pdebug -t 30m "$@" python3 -m unittest discover -s tests -v
