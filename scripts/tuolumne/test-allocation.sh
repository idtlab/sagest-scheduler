#!/usr/bin/env bash
# Allocation-mode test: submits one pbatch allocation with flux batch (campaign.py batch).
# campaign.py runs inside it and submits each member and analysis to the allocation's own
# Flux instance. This script waits for the allocation to end, then checks the run. Uses
# configs/tuolumne/euler-allocation.json. LC doesn't allow batch jobs in pdebug.
#
# Usage: scripts/tuolumne/test-allocation.sh [--hip] BANK [RUN_DIR]
# --hip runs the members on the GPUs, one MI300A each, with the HIP build of euler-miniapp
# (configs/tuolumne/euler-allocation-hip.json). Without it they run on CPU cores.
# When the allocation ends, the run directory (minus snapshots) is saved to tuolumne-results/.
# POLL_SECONDS (default 30) sets how often it checks whether the allocation ended.
#
# To watch it interactively instead, get an allocation and run campaign.py in it yourself:
#   flux alloc -N 1 -q pdebug -t 30m -B BANK
#   python3 campaign.py run RUN_DIR/config.json
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
hip=
if [[ ${1:-} == --hip ]]; then hip=-hip; shift; fi
bank=${1:?usage: test-allocation.sh [--hip] BANK [RUN_DIR]}

"$ROOT/scripts/tuolumne/build.sh" $([[ -n $hip ]] || echo --cpu-only)
source "$ROOT/scripts/tuolumne/env.sh"
source "$ROOT/build/tuolumne/env${hip}.sh"
run_dir=${2:-$SAGEST_TEST_ROOT/allocation${hip}-$(date +%Y%m%d-%H%M%S)}
config=$run_dir/config.json
if [[ ! -f $config ]]; then
    python3 "$ROOT/scripts/common/test_config.py" "$ROOT/configs/tuolumne/euler-allocation${hip}.json" \
        "$config" --run-dir "$run_dir" --bank "$bank"
fi
echo "==> allocation-mode test in $run_dir"
job=$(python3 "$ROOT/campaign.py" batch "$config")
if ! flux jobs -n -o '{state}' "$job" >/dev/null 2>&1; then
    echo "error: flux batch failed: $job" >&2
    exit 1
fi
echo "==> submitted allocation $job; driver log: $run_dir/driver-$job.out"

last=
while state=$(flux jobs -n -o '{state}' "$job") && [[ $state != INACTIVE ]]; do
    [[ $state != "$last" ]] && echo "    $(date +%H:%M:%S) $state"
    last=$state
    sleep ${POLL_SECONDS:-30}
done

echo
result=0
{
    echo "==> allocation $job"
    flux jobs -o '{id.f58} {name} {nnodes} {nodelist} {runtime!H} {status} {returncode}' "$job" || true
    echo
    python3 "$ROOT/campaign.py" status "$config" || true
    echo
    python3 "$ROOT/scripts/common/job_report.py" "$run_dir" || true
    echo
    python3 "$ROOT/scripts/common/check_euler_run.py" "$run_dir"
} 2>&1 | tee "$run_dir/report.txt" || result=$?
"$ROOT/scripts/common/save-results.sh" tuolumne "$run_dir"
exit $result
