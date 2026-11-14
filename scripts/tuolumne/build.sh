#!/usr/bin/env bash
# CPU build on Tuolumne, from a login node. Login and compute nodes share the CPU type,
# so HashBrick's -march=native is safe. Not yet tested on Tuolumne.
# Arguments go to scripts/common/build.sh, e.g. -j 16 or --spacedim 3.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
module load gcc cmake
export CXX=${CXX:-g++}
export BUILD_DIR=${BUILD_DIR:-$ROOT/build/tuolumne}
exec "$ROOT/scripts/common/build.sh" "$@"
