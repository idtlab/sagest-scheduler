#!/usr/bin/env bash
# Packs a test's run directory, without the CGNS snapshots, into perlmutter-results/ at the top
# of the repo, so it can be committed and pushed for review off Perlmutter.
#
# Usage: scripts/perlmutter/save-results.sh RUN_DIR
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
run_dir=$(cd "${1:?usage: save-results.sh RUN_DIR}" && pwd)
mkdir -p "$ROOT/perlmutter-results"
out=$ROOT/perlmutter-results/$(basename "$run_dir").tgz
tar czf "$out" --exclude='*.cgns' -C "$(dirname "$run_dir")" "$(basename "$run_dir")"
echo "==> saved ${out#"$ROOT"/}"
