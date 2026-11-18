#!/usr/bin/env bash
# HIP build of euler-miniapp for Tuolumne's MI300A GPUs. Needs the CPU build first
# (scripts/tuolumne/build.sh runs both) for CGNS and the spacedim.
#
# HIP support lives on HashBrick's automated_hip branch, which lacks the CGNS restart commit
# from the libMLEF branch the submodule pins, so that commit is applied on top. Its configure
# rewrites the CUDA sources into .hip files in place (cudaToHip.pl), and euler-miniapp's
# sources need the same conversion. Both are done on copies under $BUILD_DIR/hip so that
# external/ stays the CUDA source the other machines build.
#
# Usage: build-hip.sh [-j N] [--clean]
# Environment: BUILD_DIR (required), HIP_HASHBRICK_REF (default origin/automated_hip),
#   HIP_RESTART_COMMIT (default 8bdea8e, empty to skip), ROCM_PATH (default: from hipcc),
#   HIP_CXX (host compiler; default ROCm's amdclang++), HIP_ARCH (default gfx942, MI300A),
#   SAGEST_HIP_MODULES (default rocmcc/7.14-magic; see env.sh)
# Result: $BUILD_DIR/hip/euler-miniapp/main_gpu and $BUILD_DIR/env-hip.sh.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${BUILD_DIR:?set BUILD_DIR, or use scripts/tuolumne/build.sh}"
BUILD_DIR=$(cd "$BUILD_DIR" && pwd)
HIP_DIR=$BUILD_DIR/hip
HASHBRICK_SRC=$ROOT/external/hashbrick
EULER_SRC=$ROOT/external/euler-miniapp
REF=${HIP_HASHBRICK_REF:-origin/automated_hip}
RESTART=${HIP_RESTART_COMMIT-8bdea8e}
JOBS=$(nproc 2>/dev/null || echo 4)
CLEAN=0

while (( $# )); do
    case $1 in
        -j) JOBS=$2; shift 2 ;;
        -j*) JOBS=${1#-j}; shift ;;
        --clean) CLEAN=1; shift ;;
        -h|--help) sed -n 2,14p "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

step() { echo; echo "==> $*"; }

if [[ ! -f $BUILD_DIR/env.sh ]]; then
    echo "error: no $BUILD_DIR/env.sh; run the CPU build first" >&2
    exit 1
fi
source "$BUILD_DIR/env.sh"   # CGNS_DIR, EULER_SPACEDIM, MAKE_OBS_EXE
source "$ROOT/scripts/tuolumne/env.sh"
load_rocm

if ! command -v hipcc >/dev/null; then
    echo "error: no hipcc in PATH; load a rocm module or set SAGEST_HIP_MODULES" >&2
    exit 1
fi
# HashBrick's HIP code uses libhipcxx (cuda/atomic, hip::atomic). On Tuolumne only the rocmcc
# 7.14 packages ship it, not the /opt/rocm installs, so use whichever candidate has it.
hipcc=$(command -v hipcc)
rocm=
for dir in ${ROCM_PATH:-} "$(dirname "$(dirname "$hipcc")")" \
           "$(dirname "$(dirname "$(readlink -f "$hipcc")")")"; do
    if [[ -x $dir/bin/hipcc && -f $dir/include/cuda/atomic ]]; then rocm=$dir; break; fi
done
if [[ -z $rocm ]]; then
    echo "error: no include/cuda/atomic (libhipcxx) next to $hipcc; HashBrick's HIP code needs it." >&2
    echo "       On Tuolumne it comes with the rocmcc/7.14-magic module (set SAGEST_HIP_MODULES)." >&2
    exit 1
fi
ROCM_PATH=$rocm
HIP_ARCH=${HIP_ARCH:-gfx942}
# HashBrick's .cpp files include hip_runtime.h too, which expects ROCm's clang.
HIP_CXX=${HIP_CXX:-$(command -v amdclang++ || ls "$ROCM_PATH"/llvm/bin/amdclang++ 2>/dev/null || echo "${CXX:-g++}")}

hb_commit=$(git -C "$HASHBRICK_SRC" rev-parse --verify -q "$REF^{commit}" || true)
if [[ -z $hb_commit ]]; then
    echo "error: $REF is not in external/hashbrick; run: git -C external/hashbrick fetch origin" >&2
    exit 1
fi
euler_commit=$(git -C "$EULER_SRC" rev-parse HEAD)

# Reconfiguring HashBrick rebuilds everything, so only start over when an input changed.
config="script=$(md5sum < "${BASH_SOURCE[0]}" | cut -c1-8) hashbrick=$hb_commit restart=$RESTART euler=$euler_commit spacedim=$EULER_SPACEDIM cgns=$CGNS_DIR rocm=$ROCM_PATH cxx=$HIP_CXX arch=$HIP_ARCH"
stamp=$HIP_DIR/sagest-config
HASHBRICK=$HIP_DIR/hashbrick
EULER=$HIP_DIR/euler-miniapp
if (( CLEAN )) || [[ "$(cat "$stamp" 2>/dev/null)" != "$config" ]]; then
    step "preparing HashBrick $REF (${hb_commit:0:7}) and euler-miniapp (${euler_commit:0:7}) in $HIP_DIR"
    rm -rf "$HIP_DIR"
    mkdir -p "$HASHBRICK" "$EULER"
    git -C "$HASHBRICK_SRC" archive "$hb_commit" | tar x -C "$HASHBRICK"
    if [[ -n $RESTART ]] && ! git -C "$HASHBRICK_SRC" merge-base --is-ancestor "$RESTART" "$hb_commit"; then
        echo "    applying ${RESTART} ($(git -C "$HASHBRICK_SRC" log -1 --format=%s "$RESTART"))"
        # The test makefile's list of tests conflicts and only adds the restart test.
        git -C "$HASHBRICK_SRC" diff "$RESTART^" "$RESTART" -- . ':!lib/test/HashBrick/GNUmakefile' \
            | patch -d "$HASHBRICK" -p1 --forward --no-backup-if-mismatch
    fi
    git -C "$EULER_SRC" archive HEAD | tar x -C "$EULER"

    step "configuring HashBrick for HIP"
    (cd "$HASHBRICK" && ./configure --with-hip="$ROCM_PATH" --enable-release \
        --with-spacedim="$EULER_SPACEDIM" --with-cgns="$CGNS_DIR" CXX="$HIP_CXX") \
        > "$HIP_DIR/hashbrick-configure.log"
    sed -n '/Configuration summary/,$p' "$HIP_DIR/hashbrick-configure.log"
    # configure picks the architecture from rocminfo or the host name; MI300A is gfx942.
    if ! grep -q "Architecture *: *$HIP_ARCH\$" "$HIP_DIR/hashbrick-configure.log"; then
        sed -i "s/--offload-arch=gfx[0-9a-z]*/--offload-arch=$HIP_ARCH/" "$HASHBRICK/Common/mk/Make.defs"
        echo "    using --offload-arch=$HIP_ARCH instead"
    fi
    perl "$HASHBRICK/cudaToHip.pl" -p "$EULER" > "$HIP_DIR/euler-cudaToHip.log"
    # cudaToHip.pl turns __CUDA_ARCH__ into __HIP_DEVICE_COMPILE__ but leaves CUDA's __trap().
    grep -rlZ --include='*.hpp' --include='*.hip' '__trap()' "$HASHBRICK/lib" "$EULER" \
        | xargs -0r sed -i 's/__trap()/__builtin_trap()/g'
    echo "$config" > "$stamp"
fi

step "building HashBrick (HIP)"
make -C "$HASHBRICK/lib" --no-print-directory -j "$JOBS"

step "building euler-miniapp (HIP)"
make -C "$EULER" --no-print-directory -j "$JOBS" HASHBRICK_HOME="$HASHBRICK" all

cat > "$BUILD_DIR/env-hip.sh" <<ENV
export CGNS_DIR=$CGNS_DIR
export EULER_EXE=$EULER/main_gpu
export EULER_SPACEDIM=$EULER_SPACEDIM
export MAKE_OBS_EXE=$MAKE_OBS_EXE
ENV
step "done: $EULER/main_gpu (spacedim $EULER_SPACEDIM); paths in $BUILD_DIR/env-hip.sh"
