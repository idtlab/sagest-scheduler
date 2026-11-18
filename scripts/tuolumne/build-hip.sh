#!/usr/bin/env bash
# HIP build of euler-miniapp and libMLEF's mlef for Tuolumne's MI300A GPUs. Needs the CPU
# build first (scripts/tuolumne/build.sh runs both) for CGNS.
#
# The commits the submodules pin use an older HashBrick API than any HashBrick branch with HIP
# support. So this builds HashBrick's key_cache branch (HIP support plus newer work) with
# euler-miniapp's key_cache branch, which uses the same API, and applies the CGNS restart
# commits from both libMLEF branches on top. key_cache's euler-miniapp only builds in 3D for
# now, so this build is 3D.
#
# HashBrick's configure rewrites the CUDA sources into .hip files in place (cudaToHip.pl), and
# euler-miniapp's sources need the same conversion. Both are done on copies under
# $BUILD_DIR/hip so that external/ stays the CUDA source the other machines build. Configure
# follows the "Using Tuolumne" page of HashBrick's wiki; the settings it gets wrong for this
# build are corrected in the generated Make.defs and Config.hpp afterwards.
#
# libMLEF is built from the commit its submodule pins, converted to HIP, hipBLAS and hipSOLVER
# by scripts/tuolumne/hipify-mlef.pl, also on a copy.
#
# Usage: build-hip.sh [-j N] [--clean]
# Environment: BUILD_DIR (required), HIP_HASHBRICK_REF and HIP_EULER_REF (default
#   origin/key_cache), HIP_RESTART_COMMIT and HIP_EULER_RESTART_COMMIT (default 8bdea8e and
#   66a2313, empty to skip), HIP_SPACEDIM (default 3), ROCM_PATH (default: from hipcc),
#   HIP_ARCH (default gfx942, MI300A),
#   SAGEST_HIP_MODULES (default rocmcc/7.14-magic; see env.sh)
# Result: $BUILD_DIR/hip/euler-miniapp/main_gpu, $BUILD_DIR/hip/libMLEF/mlef (and its tests)
# and $BUILD_DIR/env-hip.sh.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${BUILD_DIR:?set BUILD_DIR, or use scripts/tuolumne/build.sh}"
BUILD_DIR=$(cd "$BUILD_DIR" && pwd)
HIP_DIR=$BUILD_DIR/hip
HASHBRICK_SRC=$ROOT/external/hashbrick
EULER_SRC=$ROOT/external/euler-miniapp
LIBMLEF_SRC=$ROOT/external/libMLEF
REF=${HIP_HASHBRICK_REF:-origin/key_cache}
RESTART=${HIP_RESTART_COMMIT-8bdea8e}
EULER_REF=${HIP_EULER_REF:-origin/key_cache}
EULER_RESTART=${HIP_EULER_RESTART_COMMIT-66a2313}
SPACEDIM=${HIP_SPACEDIM:-3}
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
source "$BUILD_DIR/env.sh"   # CGNS_DIR, MAKE_OBS_EXE
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
# HashBrick's .cpp files include libhipcxx's atomics too, which only compile as HIP, so hipcc
# compiles everything. C++20 because HashBrick uses requires clauses (configure's HIPFLAGS say
# c++17, and its compiler detection doesn't recognize LC's wrappers, leaving CXXFLAGS empty).
HIP_CXX=$ROCM_PATH/bin/hipcc
HIP_CXXFLAGS="-O3 -march=native -std=c++20 -Wall -Wno-unknown-pragmas -x hip --offload-arch=$HIP_ARCH -fgpu-rdc -munsafe-fp-atomics"
# configure only detects these for CUDA. MI300A has 64-wide wavefronts. MULTIPROCESSOR_COUNT
# only sizes euler-miniapp's scratchpad, which is indexed by smid(). cudaToHip.pl maps that to
# __smid(), which on gfx942 packs XCC, SE, SH and CU ids into 12 bits rather than counting
# 0..227, so the scratchpad needs 4096 entries (about 600 MB) to avoid writing past its end.
GPU_ATTRS="WARP_SIZE=64 MULTIPROCESSOR_COUNT=4096"

commit_of() {  # commit_of REPO REF
    local commit
    commit=$(git -C "$1" rev-parse --verify -q "$2^{commit}" || true)
    if [[ -z $commit ]]; then
        echo "error: $2 is not in $1; run: git -C $1 fetch origin" >&2
        exit 1
    fi
    echo "$commit"
}
hb_commit=$(commit_of "$HASHBRICK_SRC" "$REF")
euler_commit=$(commit_of "$EULER_SRC" "$EULER_REF")

# extract REPO COMMIT PATCH_COMMIT DEST [EXCLUDE]: a copy of COMMIT with PATCH_COMMIT applied
extract() {
    git -C "$1" archive "$2" | tar x -C "$4"
    if [[ -n $3 ]] && ! git -C "$1" merge-base --is-ancestor "$3" "$2"; then
        echo "    applying $3 to $(basename "$1") ($(git -C "$1" log -1 --format=%s "$3"))"
        git -C "$1" diff "$3^" "$3" -- . ${5:+":!$5"} | patch -d "$4" -p1 --forward --no-backup-if-mismatch
    fi
}

# Reconfiguring HashBrick rebuilds everything, so only start over when an input changed.
config="script=$(md5sum < "${BASH_SOURCE[0]}" | cut -c1-8) hashbrick=$hb_commit restart=$RESTART euler=$euler_commit euler_restart=$EULER_RESTART spacedim=$SPACEDIM cgns=$CGNS_DIR rocm=$ROCM_PATH arch=$HIP_ARCH"
stamp=$HIP_DIR/sagest-config
HASHBRICK=$HIP_DIR/hashbrick
EULER=$HIP_DIR/euler-miniapp
if (( CLEAN )) || [[ "$(cat "$stamp" 2>/dev/null)" != "$config" ]]; then
    step "preparing HashBrick $REF (${hb_commit:0:7}) and euler-miniapp $EULER_REF (${euler_commit:0:7}) in $HIP_DIR"
    rm -rf "$HIP_DIR"
    mkdir -p "$HASHBRICK" "$EULER"
    # The restart commit's change to the test makefile conflicts; it only adds its own test.
    extract "$HASHBRICK_SRC" "$hb_commit" "$RESTART" "$HASHBRICK" lib/test/HashBrick/GNUmakefile
    extract "$EULER_SRC" "$euler_commit" "$EULER_RESTART" "$EULER"

    step "configuring HashBrick for HIP"
    (cd "$HASHBRICK" && ./configure --with-hip="$ROCM_PATH" --with-computer=tuolumne --enable-release \
        --with-spacedim="$SPACEDIM" --with-cgns="$CGNS_DIR" CXX="$HIP_CXX") \
        > "$HIP_DIR/hashbrick-configure.log"
    sed -n '/Configuration summary/,$p' "$HIP_DIR/hashbrick-configure.log"
    defs=$HASHBRICK/Common/mk/Make.defs
    sed -i -E -e "s|^(HIPARCH +:=).*|\1 --offload-arch=$HIP_ARCH|" \
              -e "s|^(HIPFLAGS +:=.*)--std=c\+\+17|\1--std=c++20|" \
              -e "s|^(CXXFLAGS +:=).*|\1 $HIP_CXXFLAGS|" "$defs"
    # cudaToHip.pl renames these to HIPATTR in some files and not others, so define both.
    for attr in $GPU_ATTRS; do
        name=${attr%%=*} value=${attr#*=}
        sed -i -E "s|^/\\* #undef HBR_SYSTEM_CUDAATTR_$name \\*/|#define HBR_SYSTEM_CUDAATTR_$name $value\\n#define HBR_SYSTEM_HIPATTR_$name $value|" \
            "$HASHBRICK/lib/src/HashBrick/Config.hpp"
    done
    # A typo HashBrick's CUDA builds never instantiate: the lambda's argument is a_iv.
    perl -0pi -e 's/(a_iv \+= baseIV;\s*\n\s*const iixvec_type vecIdxs = )iv;/${1}a_iv;/g' \
        "$HASHBRICK/lib/src/HashBrick/HashLayout.hpp"
    grep -E "^(CXX|CXXFLAGS|HIPFLAGS|HIPARCH) " "$defs" | sed 's/^/    /'
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

# libMLEF is quick to build, so it has its own stamp and is rebuilt from scratch when it, the
# conversion or the toolchain changes.
MLEF=$HIP_DIR/libMLEF
mlef_commit=$(git -C "$LIBMLEF_SRC" rev-parse HEAD)
mlef_config="libmlef=$mlef_commit hipify=$(md5sum < "$ROOT/scripts/tuolumne/hipify-mlef.pl" | cut -c1-8) cgns=$CGNS_DIR rocm=$ROCM_PATH arch=$HIP_ARCH"
if (( CLEAN )) || [[ ! -x $MLEF/mlef || "$(cat "$MLEF/sagest-config" 2>/dev/null)" != "$mlef_config" ]]; then
    step "converting libMLEF (${mlef_commit:0:7}) to HIP in $MLEF"
    rm -rf "$MLEF"
    mkdir -p "$MLEF"
    git -C "$LIBMLEF_SRC" archive "$mlef_commit" | tar x -C "$MLEF"
    (cd "$MLEF" && perl "$ROOT/scripts/tuolumne/hipify-mlef.pl" *.cu *.hpp *.cpp tests/*.cu tests/*.cpp)

    step "building libMLEF and its tests (HIP)"
    # hipcc compiles .cu files as HIP. No -x hip here: libMLEF's makefile links with the same
    # flags, and -x hip would make the linker read the objects as source.
    inc="-I$CGNS_DIR/include -I$ROCM_PATH/include"
    make -C "$MLEF" --no-print-directory -j "$JOBS" all tests CGNS_DIR="$CGNS_DIR" \
        CXX="$HIP_CXX" NVCC="$HIP_CXX" \
        CXXFLAGS="-std=c++17 -O2 -fPIE --offload-arch=$HIP_ARCH $inc" \
        NVCCFLAGS="-std=c++17 -O2 -fPIE --offload-arch=$HIP_ARCH $inc" \
        CUDA_LIB="-L$ROCM_PATH/lib -lhipblas -lamdhip64 -Wl,-rpath,$ROCM_PATH/lib:$CGNS_DIR/lib" \
        MATH_LIB="-lhipsolver"
    echo "$mlef_config" > "$MLEF/sagest-config"
else
    step "libMLEF (HIP) is up to date"
fi

cat > "$BUILD_DIR/env-hip.sh" <<ENV
export CGNS_DIR=$CGNS_DIR
export EULER_EXE=$EULER/main_gpu
export EULER_SPACEDIM=$SPACEDIM
export EULER_INPUT=$EULER/inputFiles/ShockTube.input
export MAKE_OBS_EXE=$MAKE_OBS_EXE
export MLEF_EXE=$MLEF/mlef
ENV
step "done: $EULER/main_gpu (spacedim $SPACEDIM) and $MLEF/mlef; paths in $BUILD_DIR/env-hip.sh"
