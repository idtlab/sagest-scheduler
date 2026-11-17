#!/usr/bin/env bash
# Login-mode test: run from a login node (in tmux or screen, since it lasts until the jobs
# finish). campaign.py stays on the login node and submits every member and analysis as its
# own sbatch job in the shared QOS, using configs/perlmutter/euler-login.json.
#
# Usage: scripts/perlmutter/test-login.sh ACCOUNT [RUN_DIR]
# RUN_DIR defaults to a new directory under $SCRATCH/sagest-tests. Pass an existing one to
# resume a test that was interrupted; jobs it already submitted are picked up again.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
account=${1:?usage: test-login.sh ACCOUNT [RUN_DIR]}
run_dir=${2:-$SCRATCH/sagest-tests/login-$(date +%Y%m%d-%H%M%S)}
if [[ -n ${SLURM_JOB_ID:-} ]]; then
    echo "error: run this from a login node, not inside a Slurm job" >&2
    exit 1
fi

"$ROOT/scripts/perlmutter/build.sh"
source "$ROOT/scripts/perlmutter/env.sh"
source "$ROOT/build/perlmutter/env.sh"
config=$run_dir/config.json
if [[ ! -f $config ]]; then
    python3 "$ROOT/scripts/common/test_config.py" "$ROOT/configs/perlmutter/euler-login.json" \
        "$config" --run-dir "$run_dir" --bank "$account"
fi
echo "==> login-mode test in $run_dir"
echo "    resume with: $0 $account $run_dir"

code=0
python3 "$ROOT/campaign.py" run "$config" || code=$?
echo; echo "==> campaign.py exited $code"
python3 "$ROOT/campaign.py" status "$config"
echo
python3 "$ROOT/scripts/common/check_euler_run.py" "$run_dir"
