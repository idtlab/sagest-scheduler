#!/usr/bin/env bash
# Build on Perlmutter, from a login node: CPU euler-miniapp for the members, and libMLEF's
# mlef for the A100s. Login and compute nodes share the CPU type, so HashBrick's
# -march=native is safe.
# Arguments go to scripts/common/build.sh after these defaults, e.g. -j 16 or --spacedim 3.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/scripts/perlmutter/env.sh"
export BUILD_DIR=${BUILD_DIR:-$ROOT/build/perlmutter}
exec "$ROOT/scripts/common/build.sh" --mlef --cuda-arch 80 "$@"
