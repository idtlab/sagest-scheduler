#!/usr/bin/env bash
# CPU build on Ubuntu (GitHub runners and local machines). Prerequisites: install-deps.sh.
# Arguments go to scripts/common/build.sh, e.g. -j 8 or --spacedim 3.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export BUILD_DIR=${BUILD_DIR:-$ROOT/build/ubuntu-runner}
exec "$ROOT/scripts/common/build.sh" "$@"
