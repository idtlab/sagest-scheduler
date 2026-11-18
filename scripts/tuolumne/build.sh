#!/usr/bin/env bash
# Tuolumne build, from a login node (login and compute nodes are both MI300A nodes, so
# HashBrick's -march=native is safe). First the CPU build from external/ (CPU members, the
# unit tests and make_obs), then the HIP build of euler-miniapp for GPU members
# (scripts/tuolumne/build-hip.sh). libMLEF's mlef needs CUDA, so the analysis here is the stub.
#
# Usage: build.sh [--cpu-only] [-j N] [--spacedim 2|3] [--clean]
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/scripts/tuolumne/env.sh"
export BUILD_DIR=${BUILD_DIR:-$ROOT/build/tuolumne}

hip=1
common=()
hip_args=()
while (( $# )); do
    case $1 in
        --cpu-only) hip=0; shift ;;
        -j) common+=("$1" "$2"); hip_args+=("$1" "$2"); shift 2 ;;
        -j*|--clean) common+=("$1"); hip_args+=("$1"); shift ;;
        --spacedim) common+=("$1" "$2"); shift 2 ;;
        -h|--help) sed -n 2,8p "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

"$ROOT/scripts/common/build.sh" "${common[@]}"
if (( hip )); then
    "$ROOT/scripts/tuolumne/build-hip.sh" "${hip_args[@]}"
fi
