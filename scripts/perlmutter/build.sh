#!/usr/bin/env bash
# CPU build on Perlmutter, from a login node. Login and compute nodes share the CPU type,
# so HashBrick's -march=native is safe.
# Arguments go to scripts/common/build.sh, e.g. -j 16 or --spacedim 3.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/scripts/perlmutter/env.sh"
export BUILD_DIR=${BUILD_DIR:-$ROOT/build/perlmutter}
exec "$ROOT/scripts/common/build.sh" "$@"
