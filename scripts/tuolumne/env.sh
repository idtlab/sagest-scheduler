# Sourced by the other Tuolumne scripts. Loads newer GCC, CMake and Python than TOSS's when
# the modules exist. Module names vary between LC machines, so each one is optional and
# SAGEST_MODULES replaces the list. Also sets SAGEST_TEST_ROOT, where the tests run, and
# defines load_rocm for the HIP build (SAGEST_HIP_MODULES, default rocmcc/7.14-magic, the
# ROCm that ships the libhipcxx headers HashBrick needs).
set +u  # module scripts read unset variables
if ! type module >/dev/null 2>&1; then
    for init in /usr/share/lmod/lmod/init/bash /etc/profile.d/z00_lmod.sh; do
        if [[ -f $init ]]; then source "$init"; break; fi
    done
fi
load_modules() {
    set +u
    for name in "$@"; do
        if module -t avail "$name" 2>&1 | grep -q "^$name\b"; then
            module load "$name"
        fi
    done
    set -u
}
load_rocm() { load_modules ${SAGEST_HIP_MODULES:-rocmcc/7.14-magic}; }
load_modules ${SAGEST_MODULES:-gcc-native cmake python}
export CXX=${CXX:-g++}
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))'; then
    echo "error: campaign.py needs Python 3.8 or newer; load a python module" >&2
    exit 1
fi
# Run directories must be visible from compute nodes: Lustre if there is one, else home.
lustre=$(ls -d /p/lustre*/"$USER" 2>/dev/null | head -1 || true)
export SAGEST_TEST_ROOT=${SAGEST_TEST_ROOT:-${lustre:-$HOME}/sagest-tests}
