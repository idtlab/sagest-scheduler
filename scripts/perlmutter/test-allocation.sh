#!/usr/bin/env bash
# Allocation-mode test: submits one debug-QOS allocation with sbatch (campaign.py batch).
# campaign.py runs inside it and launches each member and analysis as an srun step sharing
# the allocation's node. This script waits for the allocation to end, then checks the run.
# Uses configs/perlmutter/euler-allocation.json.
#
# Usage: scripts/perlmutter/test-allocation.sh ACCOUNT [RUN_DIR]
# When the allocation ends, the run directory (minus snapshots) is saved to perlmutter-results/.
# POLL_SECONDS (default 30) sets how often it checks whether the allocation ended.
#
# To watch it interactively instead, get an allocation and run campaign.py in it yourself:
#   salloc -N 1 -q interactive -C cpu -t 30 -A ACCOUNT
#   python3 campaign.py run RUN_DIR/config.json
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
account=${1:?usage: test-allocation.sh ACCOUNT [RUN_DIR]}
run_dir=${2:-$SCRATCH/sagest-tests/allocation-$(date +%Y%m%d-%H%M%S)}

"$ROOT/scripts/perlmutter/build.sh"
source "$ROOT/scripts/perlmutter/env.sh"
source "$ROOT/build/perlmutter/env.sh"
config=$run_dir/config.json
if [[ ! -f $config ]]; then
    python3 "$ROOT/scripts/common/test_config.py" "$ROOT/configs/perlmutter/euler-allocation.json" \
        "$config" --run-dir "$run_dir" --bank "$account"
fi
echo "==> allocation-mode test in $run_dir"
job=$(python3 "$ROOT/campaign.py" batch "$config")
job=${job%%;*}
if [[ ! $job =~ ^[0-9]+$ ]]; then
    echo "error: sbatch failed: $job" >&2
    exit 1
fi
echo "==> submitted allocation $job; driver log: $run_dir/driver-$job.out"

last=
while state=$(squeue -h -j "$job" -o %T 2>/dev/null) && [[ -n $state ]]; do
    [[ $state != "$last" ]] && echo "    $(date +%H:%M:%S) $state"
    last=$state
    sleep ${POLL_SECONDS:-30}
done

echo
result=0
{
    python3 "$ROOT/campaign.py" status "$config" || true
    echo
    python3 "$ROOT/scripts/common/job_report.py" "$run_dir" || true
    echo
    python3 "$ROOT/scripts/common/check_euler_run.py" "$run_dir"
} 2>&1 | tee "$run_dir/report.txt" || result=$?
"$ROOT/scripts/perlmutter/save-results.sh" "$run_dir"
exit $result
