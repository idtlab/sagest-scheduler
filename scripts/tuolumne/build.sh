#!/usr/bin/env bash
# CPU build on Tuolumne, from a login node. Login and compute nodes are both MI300A nodes,
# so HashBrick's -march=native is safe. libMLEF's mlef needs CUDA, which Tuolumne's AMD GPUs
# can't run, so the analysis there is the stub.
# Arguments go to scripts/common/build.sh, e.g. -j 16 or --spacedim 3.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/scripts/tuolumne/env.sh"
export BUILD_DIR=${BUILD_DIR:-$ROOT/build/tuolumne}
exec "$ROOT/scripts/common/build.sh" "$@"
