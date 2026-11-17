#!/usr/bin/env bash
# Build HashBrick and euler-miniapp from the submodules in external/, for CPU by default, and
# tools/make_obs. --mlef adds libMLEF's mlef for the GPU, keeping the CPU members. --cuda
# builds the GPU versions of all three instead. Neither needs a GPU, only the CUDA toolkit
# with cuBLAS and cuSOLVER (and NPP for --cuda). Called by scripts/<platform>/build.sh.
#
# CGNS, and the serial HDF5 it needs, are built from source into $BUILD_DIR/deps unless
# CGNS_DIR points at an existing serial install with shared libraries.
#
# Usage: build.sh [-j N] [--spacedim 2|3] [--mlef | --cuda] [--cuda-arch 80|90] [--clean]
# Environment: BUILD_DIR (required), CGNS_DIR, HDF5_DIR, CXX, CUDA_HOME, MATH_LIBS_HOME
# Result: external/euler-miniapp/main_cpu (main_gpu with --cuda), external/libMLEF/mlef with
# --mlef or --cuda, $BUILD_DIR/bin/make_obs, and $BUILD_DIR/env.sh (env-cuda.sh with --cuda)
# with the paths used.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${BUILD_DIR:?set BUILD_DIR, or use scripts/<platform>/build.sh}"
mkdir -p "$BUILD_DIR"
BUILD_DIR=$(cd "$BUILD_DIR" && pwd)
DEPS=$BUILD_DIR/deps
HASHBRICK=$ROOT/external/hashbrick
EULER=$ROOT/external/euler-miniapp
LIBMLEF=$ROOT/external/libMLEF

HDF5_VERSION=1.14.6
CGNS_VERSION=4.4.0
JOBS=$(nproc 2>/dev/null || echo 4)
SPACEDIM=2
CUDA=0
MLEF=0
CUDA_ARCH=90
CLEAN=0

while (( $# )); do
    case $1 in
        -j) JOBS=$2; shift 2 ;;
        -j*) JOBS=${1#-j}; shift ;;
        --spacedim) SPACEDIM=$2; shift 2 ;;
        --cuda) CUDA=1; MLEF=1; shift ;;
        --mlef) MLEF=1; shift ;;
        --cuda-arch) CUDA_ARCH=$2; shift 2 ;;
        --clean) CLEAN=1; shift ;;
        -h|--help) sed -n 2,14p "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

step() { echo; echo "==> $*"; }

sources=("$HASHBRICK" "$EULER")
(( MLEF )) && sources+=("$LIBMLEF")
for dir in "${sources[@]}"; do
    if [[ ! -f $dir/GNUmakefile ]]; then
        echo "error: $dir is empty; run: git submodule update --init" >&2
        exit 1
    fi
done

if (( MLEF )); then
    CUDA_HOME=${CUDA_HOME:-$(dirname "$(dirname "$(command -v nvcc || echo /usr/local/cuda/bin/nvcc)")")}
    if [[ ! -x $CUDA_HOME/bin/nvcc ]]; then
        echo "error: no nvcc in $CUDA_HOME/bin; set CUDA_HOME" >&2
        exit 1
    fi
    export PATH=$CUDA_HOME/bin:$PATH
    # The NVIDIA HPC SDK (Perlmutter's cudatoolkit) keeps cuSOLVER in math_libs/<version>.
    if [[ -z ${MATH_LIBS_HOME:-} ]]; then
        MATH_LIBS_HOME=$CUDA_HOME
        sdk_math=$(dirname "$(dirname "$CUDA_HOME")")/math_libs/$(basename "$CUDA_HOME")
        if ! ls "$CUDA_HOME"/lib64/libcusolver.so* >/dev/null 2>&1 && [[ -d $sdk_math ]]; then
            MATH_LIBS_HOME=$sdk_math
        fi
    fi
fi

fetch() {  # fetch URL FILE
    if [[ ! -f $2 ]]; then
        curl -fsSL --retry 3 -o "$2.part" "$1"
        mv -f "$2.part" "$2"
    fi
}

if [[ -z ${CGNS_DIR:-} ]]; then
    CGNS_DIR=$DEPS/cgns-$CGNS_VERSION
    mkdir -p "$DEPS/src"

    # Distribution and Cray HDF5 builds are often MPI builds; serial CGNS needs a serial one.
    if [[ -z ${HDF5_DIR:-} ]]; then
        HDF5_DIR=$DEPS/hdf5-$HDF5_VERSION
        if [[ ! -f $HDF5_DIR/lib/libhdf5.so ]]; then
            step "building HDF5 $HDF5_VERSION into $HDF5_DIR"
            fetch "https://github.com/HDFGroup/hdf5/releases/download/hdf5_$HDF5_VERSION/hdf5-$HDF5_VERSION.tar.gz" \
                  "$DEPS/src/hdf5-$HDF5_VERSION.tar.gz"
            rm -rf "$DEPS/src/hdf5-$HDF5_VERSION" "$DEPS/src/hdf5-build"
            tar xzf "$DEPS/src/hdf5-$HDF5_VERSION.tar.gz" -C "$DEPS/src"
            cmake -S "$DEPS/src/hdf5-$HDF5_VERSION" -B "$DEPS/src/hdf5-build" -DCMAKE_BUILD_TYPE=Release \
                  -DCMAKE_INSTALL_PREFIX="$HDF5_DIR" -DCMAKE_INSTALL_LIBDIR=lib -DHDF5_ENABLE_PARALLEL=OFF \
                  -DBUILD_TESTING=OFF -DHDF5_BUILD_EXAMPLES=OFF -DHDF5_BUILD_TOOLS=OFF \
                  -DHDF5_BUILD_FORTRAN=OFF -DHDF5_BUILD_CPP_LIB=OFF -DHDF5_BUILD_HL_LIB=OFF \
                  -DHDF5_ENABLE_SZIP_SUPPORT=OFF -DHDF5_ENABLE_Z_LIB_SUPPORT=OFF >/dev/null
            cmake --build "$DEPS/src/hdf5-build" -j "$JOBS" >/dev/null
            cmake --install "$DEPS/src/hdf5-build" >/dev/null
        fi
    fi

    if [[ ! -f $CGNS_DIR/lib/libcgns.so ]]; then
        step "building CGNS $CGNS_VERSION into $CGNS_DIR"
        fetch "https://github.com/CGNS/CGNS/archive/refs/tags/v$CGNS_VERSION.tar.gz" \
              "$DEPS/src/cgns-$CGNS_VERSION.tar.gz"
        rm -rf "$DEPS/src/CGNS-$CGNS_VERSION" "$DEPS/src/cgns-build"
        tar xzf "$DEPS/src/cgns-$CGNS_VERSION.tar.gz" -C "$DEPS/src"
        cmake -S "$DEPS/src/CGNS-$CGNS_VERSION" -B "$DEPS/src/cgns-build" -DCMAKE_BUILD_TYPE=Release \
              -DCMAKE_INSTALL_PREFIX="$CGNS_DIR" -DCMAKE_INSTALL_LIBDIR=lib \
              -DCMAKE_INSTALL_RPATH="$HDF5_DIR/lib" \
              -DCGNS_ENABLE_HDF5=ON -DHDF5_ROOT="$HDF5_DIR" -DCGNS_ENABLE_PARALLEL=OFF \
              -DCGNS_ENABLE_64BIT=ON -DCGNS_ENABLE_FORTRAN=OFF -DCGNS_ENABLE_TESTS=OFF \
              -DCGNS_BUILD_CGNSTOOLS=OFF -DCGNS_BUILD_SHARED=ON -DCGNS_USE_SHARED=ON >/dev/null
        cmake --build "$DEPS/src/cgns-build" -j "$JOBS" >/dev/null
        cmake --install "$DEPS/src/cgns-build" >/dev/null
    fi
fi
if [[ ! -f $CGNS_DIR/include/cgnslib.h || ! -f $CGNS_DIR/lib/libcgns.so ]]; then
    echo "error: CGNS_DIR=$CGNS_DIR needs include/cgnslib.h and lib/libcgns.so" >&2
    exit 1
fi

if (( CUDA )); then
    # Configure can't query a device here, so use HashBrick's one filled-in preset (GH200,
    # sm_90) and override only the architecture for other targets. The preset's other
    # settings, such as host page table use, still describe a GH200.
    hb_configure=(--enable-gpu --with-gpu=GH200 --with-cudatk="$CUDA_HOME")
    hb_make=(GPU=TRUE)
    arch=sm_$CUDA_ARCH
    if [[ $CUDA_ARCH != 90 ]]; then
        hb_make+=("NVCCARCH=-gencode=arch=compute_$CUDA_ARCH,code=sm_$CUDA_ARCH")
    fi
    variant="GPU, sm_$CUDA_ARCH"
else
    hb_configure=(--enable-gpu=no)
    hb_make=()
    arch=
    variant=CPU
fi

# HashBrick's configure writes into its source tree, so only one configuration exists at a
# time. The stamp avoids reconfiguring, and so recompiling everything, when nothing changed.
config="spacedim=$SPACEDIM cgns=$CGNS_DIR cxx=$(command -v "${CXX:-g++}") ${hb_configure[*]} $arch"
# Kept in the submodule's git directory so it stays out of `git status`.
stamp=$(git -C "$HASHBRICK" rev-parse --absolute-git-dir 2>/dev/null || echo "$HASHBRICK/Common/mk")/sagest-config
if (( CLEAN )) || [[ ! -f $HASHBRICK/Common/mk/Make.defs || "$(cat "$stamp" 2>/dev/null)" != "$config" ]]; then
    step "configuring HashBrick ($config)"
    if [[ -f $HASHBRICK/Common/mk/Make.defs ]]; then
        for gpu in FALSE TRUE; do
            make -C "$HASHBRICK/lib" --no-print-directory GPU=$gpu clean >/dev/null 2>&1 || true
            make -C "$EULER" --no-print-directory GPU=$gpu HASHBRICK_HOME="$HASHBRICK" clean \
                >/dev/null 2>&1 || true
        done
    fi
    (cd "$HASHBRICK" && ./configure "${hb_configure[@]}" --enable-release --with-spacedim="$SPACEDIM" \
        --with-cgns="$CGNS_DIR" ${CXX:+CXX="$CXX"}) > "$BUILD_DIR/hashbrick-configure.log"
    sed -n '/Configuration summary/,$p' "$BUILD_DIR/hashbrick-configure.log"
    echo "$config" > "$stamp"
fi

step "building HashBrick ($variant)"
make -C "$HASHBRICK/lib" --no-print-directory -j "$JOBS" "${hb_make[@]}"

step "building euler-miniapp ($variant)"
make -C "$EULER" --no-print-directory -j "$JOBS" HASHBRICK_HOME="$HASHBRICK" "${hb_make[@]}" all

step "building make_obs"
mkdir -p "$BUILD_DIR/bin"
make_obs=$BUILD_DIR/bin/make_obs
if [[ ! -x $make_obs || $ROOT/tools/make_obs.cpp -nt $make_obs || $CGNS_DIR/lib/libcgns.so -nt $make_obs ]]; then
    "${CXX:-g++}" -std=c++17 -O2 -Wall -Wextra "$ROOT/tools/make_obs.cpp" -I"$CGNS_DIR/include" \
        -L"$CGNS_DIR/lib" -lcgns -Wl,-rpath,"$CGNS_DIR/lib" -o "$make_obs"
fi

if (( MLEF )); then
    # libMLEF's makefile doesn't track header dependencies, so rebuild from clean whenever
    # its source or the settings change.
    mlef_make=(CXX="${CXX:-g++}" NVCC="$CUDA_HOME/bin/nvcc" NVCCARCH="sm_$CUDA_ARCH"
               CUDA_HOME="$CUDA_HOME" MATH_LIBS_HOME="$MATH_LIBS_HOME" CGNS_DIR="$CGNS_DIR")
    # Run without the toolkit's module loaded, as on compute nodes started from a bare shell.
    rpath="-Xlinker -rpath=$CUDA_HOME/lib64:$MATH_LIBS_HOME/lib64:$CGNS_DIR/lib"
    mlef_make+=(CUDA_LIB="-L$CUDA_HOME/lib64 -lcudart -lcublas $rpath")
    mlef_stamp=$(git -C "$LIBMLEF" rev-parse --absolute-git-dir 2>/dev/null || echo "$LIBMLEF/o")/sagest-build
    mlef_config="$(git -C "$LIBMLEF" rev-parse HEAD 2>/dev/null) $(git -C "$LIBMLEF" status --porcelain --untracked-files=no 2>/dev/null | md5sum) ${mlef_make[*]}"
    if (( CLEAN )) || [[ ! -x $LIBMLEF/mlef || "$(cat "$mlef_stamp" 2>/dev/null)" != "$mlef_config" ]]; then
        step "building libMLEF and its tests (sm_$CUDA_ARCH)"
        make -C "$LIBMLEF" --no-print-directory "${mlef_make[@]}" clean >/dev/null
        make -C "$LIBMLEF" --no-print-directory -j "$JOBS" "${mlef_make[@]}" all tests
        echo "$mlef_config" > "$mlef_stamp"
    else
        step "libMLEF is up to date"
    fi
fi

if (( ! CUDA )); then
    {
        echo "export CGNS_DIR=$CGNS_DIR"
        echo "export EULER_EXE=$EULER/main_cpu"
        echo "export EULER_SPACEDIM=$SPACEDIM"
        echo "export MAKE_OBS_EXE=$make_obs"
        if (( MLEF )); then echo "export MLEF_EXE=$LIBMLEF/mlef"; fi
    } > "$BUILD_DIR/env.sh"
    step "done: $EULER/main_cpu (spacedim $SPACEDIM)$( (( MLEF )) && echo " and $LIBMLEF/mlef (sm_$CUDA_ARCH)"); paths in $BUILD_DIR/env.sh"
    exit 0
fi

cat > "$BUILD_DIR/env-cuda.sh" <<ENV
export CGNS_DIR=$CGNS_DIR
export EULER_EXE=$EULER/main_gpu
export EULER_SPACEDIM=$SPACEDIM
export MLEF_EXE=$LIBMLEF/mlef
export MAKE_OBS_EXE=$make_obs
# So that \`make -C external/libMLEF run\` reuses this build.
export CXX=${CXX:-g++} NVCC=$CUDA_HOME/bin/nvcc NVCCARCH=sm_$CUDA_ARCH
export CUDA_HOME=$CUDA_HOME MATH_LIBS_HOME=$MATH_LIBS_HOME
ENV
step "done: $EULER/main_gpu and $LIBMLEF/mlef (spacedim $SPACEDIM, sm_$CUDA_ARCH); paths in $BUILD_DIR/env-cuda.sh"
