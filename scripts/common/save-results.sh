#!/usr/bin/env bash
# Packs a test's run directory, without the CGNS snapshots, into <MACHINE>-results/ at the top
# of the repo, so it can be committed and pushed for review elsewhere.
#
# Usage: scripts/common/save-results.sh MACHINE RUN_DIR
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
machine=${1:?usage: save-results.sh MACHINE RUN_DIR}
run_dir=$(cd "${2:?usage: save-results.sh MACHINE RUN_DIR}" && pwd)
mkdir -p "$ROOT/$machine-results"
out=$ROOT/$machine-results/$(basename "$run_dir").tgz
tar czf "$out" --exclude='*.cgns' -C "$(dirname "$run_dir")" "$(basename "$run_dir")"
echo "==> saved ${out#"$ROOT"/}"
