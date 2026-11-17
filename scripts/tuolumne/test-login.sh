#!/usr/bin/env bash
# Login-mode test: run from a login node (in tmux or screen, since it lasts until the jobs
# finish). campaign.py stays on the login node and submits every member and analysis as its
# own flux job in pbatch, using configs/tuolumne/euler-login.json. The analysis is the MLEF
# stub, since mlef needs CUDA.
#
# Usage: scripts/tuolumne/test-login.sh BANK [RUN_DIR]
# RUN_DIR defaults to a new directory under $SAGEST_TEST_ROOT (see env.sh). Pass an existing
# one to resume a test that was interrupted; jobs it already submitted are picked up again.
# When the campaign ends, the run directory (minus snapshots) is saved to tuolumne-results/.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
bank=${1:?usage: test-login.sh BANK [RUN_DIR]}
if [[ -n ${FLUX_JOB_ID:-} ]]; then
    echo "error: run this from a login node, not inside a Flux job" >&2
    exit 1
fi

"$ROOT/scripts/tuolumne/build.sh"
source "$ROOT/scripts/tuolumne/env.sh"
source "$ROOT/build/tuolumne/env.sh"
run_dir=${2:-$SAGEST_TEST_ROOT/login-$(date +%Y%m%d-%H%M%S)}
config=$run_dir/config.json
if [[ ! -f $config ]]; then
    python3 "$ROOT/scripts/common/test_config.py" "$ROOT/configs/tuolumne/euler-login.json" \
        "$config" --run-dir "$run_dir" --bank "$bank"
fi
echo "==> login-mode test in $run_dir"
echo "    resume with: $0 $bank $run_dir"

code=0
python3 "$ROOT/campaign.py" run "$config" || code=$?
result=0
{
    echo "==> campaign.py exited $code"
    python3 "$ROOT/campaign.py" status "$config" || true
    echo
    python3 "$ROOT/scripts/common/job_report.py" "$run_dir" || true
    echo
    python3 "$ROOT/scripts/common/check_euler_run.py" "$run_dir"
} 2>&1 | tee "$run_dir/report.txt" || result=$?
"$ROOT/scripts/common/save-results.sh" tuolumne "$run_dir"
exit $result
