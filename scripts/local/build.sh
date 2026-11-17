#!/usr/bin/env bash
# Build on Ubuntu: workstations and GitHub runners. CPU by default; --cuda for the GPU
# build and libMLEF. Prerequisites: install-deps.sh.
# Arguments go to scripts/common/build.sh, e.g. -j 8 or --spacedim 3.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export BUILD_DIR=${BUILD_DIR:-$ROOT/build/local}
exec "$ROOT/scripts/common/build.sh" "$@"
