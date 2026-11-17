# MiniFEW

**Full documentation: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)**, covering every command, config
setting, file and behavior.

`campaign.py` runs N ensemble members (one job each), then a post-analysis job once all N have
succeeded, and repeats for a set number of cycles. Flux or Slurm does all the scheduling and
launching. This adds what neither has a feature for: retries, reacting to failure reasons, and
waiting for all members across retries.

The schedulers' own dependencies can't span retries: an `afterok` job is rejected or never runs
once attempt 1 fails, and `afterany` runs even if every attempt failed.

## Machines

| | Scheduler | Scripts | Configs |
|---|---|---|---|
| Local (Ubuntu workstations and GitHub runners) | Flux | `scripts/local/` | `configs/local/` |
| Perlmutter | Slurm | `scripts/perlmutter/` | `configs/perlmutter/` |
| Tuolumne | Flux | `scripts/tuolumne/` | `configs/tuolumne/` |

`scripts/common/` holds what they share. A config picks its scheduler with
`"scheduler": "flux"` (the default) or `"slurm"`.

## Two ways to run it

`run` picks its mode from where it starts:

| | Login mode | Allocation mode |
|---|---|---|
| Start it with | `./campaign.py run CONFIG` on a login node, e.g. in `tmux` | `./campaign.py batch CONFIG`, or `run` inside `flux alloc`/`flux batch` or `salloc`/`sbatch` |
| Where jobs go | Each job queues on its own in `queue`, charged to `bank` (`flux submit -q -B`, `sbatch -q -A`) | Into the allocation, which shares its nodes between jobs (`flux submit`, `srun --exact` steps) |
| Members overlap? | Not guaranteed; each job and retry queues separately | Yes, up to the allocation's nodes |
| Time | Each job's limit only | Also checks each job fits the time left, and pauses if not |
| If `campaign.py` stops | Jobs keep running; rerun `run` to pick them up | The allocation ends; rerun `batch` and its jobs are resubmitted |

`run` refuses to start inside a single job (a `flux submit` job or a numbered Slurm step). You
can switch modes when resuming, except that allocation mode refuses to take over jobs still
running from the login node.

```bash
./campaign.py run configs/tuolumne/sagest.json --dry-run   # both modes' commands and the allocation plan
./campaign.py run configs/tuolumne/sagest.json             # login mode
./campaign.py batch configs/tuolumne/sagest.json           # allocation mode
./campaign.py status configs/tuolumne/sagest.json          # from any shell
```

Exit codes: 0 completed, 1 failed, 2 invalid config, 3 paused. Rerunning continues a paused campaign, and retries
a failed one's failed tasks with fresh budgets. Finished members are kept.

Put `run_dir` on a file system that compute nodes can also see.

## Config

See `configs/tuolumne/sagest.json` (Flux) and `configs/perlmutter/euler-login.json` (Slurm).

- `queue` and `bank` go on each job in login mode, and on the allocation in allocation mode.
  Jobs inside an allocation never get them, because the allocation's scheduler rejects `-q`.
  With Slurm, `queue` is the QOS and `bank` the account. `ensemble` and `analysis` can set
  their own, such as a GPU account for the analysis.
- Each component's `nodes` becomes `-N`. Its `flux_args` go straight to `flux submit`, and its
  `slurm_args` to `sbatch` (login mode) or `srun` (allocation mode). Only the current
  scheduler's are used.
- `allocation.nodes` defaults to enough for all members at once. `allocation.time` sets the
  allocation's length; otherwise it's calculated, and `allocation.max_time` caps it.
  `allocation.flux_args` or `allocation.slurm_args` go to `flux batch` or `sbatch`.

## Time

```
job limit  = estimate + buffer
allocation = cycles × (waves × member limit + analysis limit) + reserve
waves      = ceil(size × member nodes / allocation nodes)
```

`reserve` is the only allocation time set aside for retries. In login mode only the job limit
applies, and time waiting in the queue isn't limited or predicted. `status` shows measured
runtimes next to your estimate. Slurm limits are rounded up to whole minutes.

## Failures

| How the job ended | Result |
|---|---|
| Canceled (Flux `CANCELED`, Slurm `CANCELLED`) | Not retried |
| Exit code declared under `failures` | Predicted: uses that reason's `retries` |
| `TIMEOUT`, with a `TIMEOUT` reason declared | Predicted |
| Exit 0 | Success |
| Anything else, such as a crash | Unpredicted: uses the component's `retries` |
| Job unknown to the scheduler | Resubmitted, no retry used |

Set `retries` on `ensemble` and on `analysis` for unpredicted failures. It defaults to 0.

```json
"NUMERICAL_INSTABILITY": {"exit_code": 20, "retries": 3, "set": {"solver": "safe"}},
"BAD_INPUT":             {"exit_code": 22, "retries": 0},
"TIMEOUT":               {"retries": 1, "time_scale": 1.5}
```

- `retries: 0` means fatal.
- `set` changes parameters for the retry, and `time_scale` multiplies the time limit.
- Before exiting, a job can write `$ORCH_DIR/retry_params.json` with new parameter values.
- Exit codes must be in 3..125.

With several MPI ranks, Flux reports the highest exit status of any rank, and a rank killed by a
signal counts as 128 + the signal. So a crash on another rank can hide a declared code. Test
`MPI_Abort` on Tuolumne before relying on it to carry a code.

With Slurm in login mode, job states come from `sacct`, with `squeue` covering jobs accounting
hasn't caught up with. In allocation mode `campaign.py` starts each `srun` itself and reads
timeouts and cancellations from srun's messages in the attempt's `srun.log`.

## Your components

Each job runs in its own attempt directory and gets `ORCH_DIR`, `ORCH_PARAMS` (JSON file),
`ORCH_CYCLE`, `ORCH_ATTEMPT` and `ORCH_ENSEMBLE_SIZE`. Members also get `ORCH_MEMBER_ID`. The
analysis also gets `ORCH_MANIFEST`, which lists each member's successful directory.

The analysis can write `$ORCH_DIR/next_cycle.json`:
`{"continue": true, "params": {...}}`. Those values carry into later cycles, and
`"continue": false` ends the campaign early.

## euler-miniapp ensembles

`external/` holds euler-miniapp and HashBrick (both on their `libMLEF` branches) and libMLEF,
as submodules. They're private, so fetching them needs access to sagestgit.cs.virginia.edu:

```bash
git submodule update --init
scripts/local/build.sh     # or scripts/perlmutter/build.sh or scripts/tuolumne/build.sh
```

Each machine's `build.sh` builds the CPU versions of HashBrick and euler-miniapp
(`external/euler-miniapp/main_cpu`, 2D by default, `--spacedim 3` for 3D), and
`build/<machine>/bin/make_obs`. CGNS and a serial HDF5 are built from source into
`build/<machine>/deps` unless `CGNS_DIR` points at an install.

`--mlef` adds libMLEF's `mlef` and its tests, keeping the CPU members; Perlmutter's
`build.sh` always passes it, with `--cuda-arch 80` for the A100s. `--cuda` builds the GPU
versions of euler-miniapp and HashBrick too (`main_gpu`). Both need the CUDA toolkit but no
GPU, and the default architecture is 90. HashBrick's configure can't detect a GPU on a build
machine, so it always uses its GH200 preset, and only the architecture changes.

`components/euler_member.py` runs one member. Cycle 0 perturbs each member's initial
conditions, as libMLEF's `run_ensemble.sh` does, and later cycles restart each member from the
analysis. A member exits 20 if the solution goes non-finite, because euler-miniapp itself exits
0 then. euler-miniapp crashes on any input value longer than 199 characters, so the wrapper
passes paths relative to the attempt directory.

`components/mlef_analysis.py` runs `mlef` on the forecasts, which needs a GPU. Unless
`obs_dir` names a directory of observation files, it runs a twin experiment: a truth run with
the members' settings and unperturbed initial conditions, which `make_obs` samples at the
analysis step (four density probes by default). The truth restarts each cycle from the last,
like the members. Members of the next cycle restart from `mlef`'s analysis.

Without a GPU, `components/mlef_stub.py` stands in: it lays the forecasts out the same way and
copies each one unchanged to where `mlef` would write the analysis. The local configs use it.

## Testing

```bash
scripts/local/test.sh      # builds, then runs every test in Flux; fails on skips
flux start --test-size=2 python3 -m unittest discover -s tests
```

The euler-miniapp tests skip themselves until `main_cpu` is built. The Slurm tests run against
`tests/fake_slurm`, which stands in for `sbatch`, `sacct`, `squeue` and `srun` with local
processes, and the MLEF analysis tests run `tests/fake_mlef.py` in place of `mlef`. CI fetches the submodules with the `SAGESTGIT_TOKEN` repository secret. Its CUDA job
only compiles, since GitHub's runners have no GPU.

### Perlmutter

From a login node, each test builds, writes its config and runs under `$SCRATCH/sagest-tests`,
then checks every cycle completed, every later member restarted from the analysis, and `mlef`
moved the ensemble mean each cycle. Members run on CPUs, and `mlef` on one A100 charged to
`ACCOUNT_g`:

```bash
scripts/perlmutter/test-login.sh ACCOUNT        # in tmux: one sbatch job per member, shared QOS
scripts/perlmutter/test-allocation.sh ACCOUNT   # one debug GPU node, all jobs as srun steps
```

Pass a test's run directory as a second argument to resume it. When a test ends, its run
directory, without the CGNS snapshots, is saved to `perlmutter-results/` to commit and push.
Its `report.txt` includes `scripts/common/job_report.py`'s table of every job attempt: the
nodes, CPUs and GPUs it ran on (from the `job.json` each component writes), how long each
phase took, and `sacct`'s accounting for the same jobs.

### Tuolumne

Set `bank` in `configs/tuolumne/smoke.json`, then from a login node:

```bash
flux getattr instance-level        # must print 0 for login mode
flux jobs -n -o '{id}' f111111     # must print: JobID f111111 unknown
./campaign.py run configs/tuolumne/smoke.json        # login mode
./campaign.py status configs/tuolumne/smoke.json
```

Use a fresh `run_dir` to try allocation mode with `batch`. Lost jobs are detected from the
"unknown" wording; if yours differs, a campaign with a lost job pauses instead of resubmitting,
so fix the pattern in `parse_jobs`.

`configs/tuolumne/smoke.json` uses `pbatch` because LC's Tuolumne page prohibits batch jobs and
job chaining in `pdebug`. Check LC's current rules on job counts and long-running login-node
processes before a large campaign.

## Limitations

- Exit 0 counts as success, so a member that exits 0 without its output only shows up when the
  analysis fails. Have members exit nonzero if their output is missing.
- In allocation mode, the time check covers one job at a time, so queued jobs can still run out
  of time. They're resubmitted without using a retry on resume.
- Don't run two copies on one `run_dir`.
- If `campaign.py` dies in the moment between a submit and recording the job ID, that job keeps
  running untracked, and its member is submitted again in a new directory.
- Slurm login mode has run on Perlmutter, with both the stub and the real `mlef`
  (`perlmutter-results/`). Allocation mode has only run against `tests/fake_slurm`.
