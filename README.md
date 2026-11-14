# MiniFEW

**Full documentation: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)**, covering every command, config
setting, file and behavior.

`campaign.py` runs N ensemble members (one Flux job each), then a post-analysis job once all
N have succeeded, and repeats for a set number of cycles. Flux does all the scheduling and
launching. This adds what Flux has no feature for: retries, reacting to failure reasons, and
waiting for all members across retries.

Flux's own dependencies can't span retries: an `afterok` job is rejected as soon as attempt 1
fails, and `afterany` runs even if every attempt failed.

## Two ways to run it

`run` picks its mode from where it starts:

| | Login mode | Allocation mode |
|---|---|---|
| Start it with | `./campaign.py run CONFIG` on a login node, e.g. in `tmux` | `./campaign.py batch CONFIG`, or `run` inside `flux alloc` / `flux batch` |
| Where jobs go | Each job queues on its own in `queue`, charged to `bank` | Into the allocation, which shares its nodes between jobs |
| Members overlap? | Not guaranteed; each job and retry queues separately | Yes, up to the allocation's nodes |
| Time | Each job's limit only | Also checks each job fits the time left, and pauses if not |
| If `campaign.py` stops | Jobs keep running; rerun `run` to pick them up | The allocation ends; rerun `batch` and its jobs are resubmitted |

`run` refuses to start inside a single `flux submit` job. You can switch modes when resuming,
except that allocation mode refuses to take over jobs still running from the login node.

```bash
./campaign.py run configs/tuolumne.json --dry-run   # both modes' commands and the allocation plan
./campaign.py run configs/tuolumne.json             # login mode
./campaign.py batch configs/tuolumne.json           # allocation mode
./campaign.py status configs/tuolumne.json          # from any shell
```

Exit codes: 0 completed, 1 failed, 2 invalid config, 3 paused. Rerunning continues a paused campaign, and retries
a failed one's failed tasks with fresh budgets. Finished members are kept.

Put `run_dir` on a file system that compute nodes can also see.

## Config

See `configs/tuolumne.json`.

- `queue` and `bank` go on each job in login mode, and on the allocation in allocation mode.
  Jobs inside an allocation never get them, because the allocation's scheduler rejects `-q`.
- Each component's `nodes` becomes `-N`, and its `flux_args` go straight to `flux submit`.
- `allocation.nodes` defaults to enough for all members at once. `allocation.time` sets the
  allocation's length; otherwise it's calculated, and `allocation.max_time` caps it.

## Time

```
job limit  = estimate + buffer
allocation = cycles × (waves × member limit + analysis limit) + reserve
waves      = ceil(size × member nodes / allocation nodes)
```

`reserve` is the only allocation time set aside for retries. In login mode only the job limit
applies, and time waiting in the queue isn't limited or predicted. `status` shows measured
runtimes next to your estimate.

## Failures

| How the job ended | Result |
|---|---|
| Flux result `CANCELED` | Not retried |
| Exit code declared under `failures` | Predicted: uses that reason's `retries` |
| Flux result `TIMEOUT`, with a `TIMEOUT` reason declared | Predicted |
| Exit 0 | Success |
| Anything else, such as a crash | Unpredicted: uses the component's `retries` |
| Job unknown to Flux | Resubmitted, no retry used |

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
scripts/ubuntu-runner/build.sh     # or scripts/perlmutter/ or scripts/tuolumne/
```

Each platform's `build.sh` builds the CPU versions of HashBrick and euler-miniapp
(`external/euler-miniapp/main_cpu`, 2D by default, `--spacedim 3` for 3D). CGNS and a serial
HDF5 are built from source into `build/<platform>/deps` unless `CGNS_DIR` points at an
install. The Perlmutter and Tuolumne scripts haven't been run on those machines yet.

`--cuda` builds the GPU versions instead (`main_gpu`) along with libMLEF's `mlef` and its
tests. It needs the CUDA toolkit but no GPU; `--cuda-arch 80` targets A100s, and the default
is 90. HashBrick's configure can't detect a GPU on a build machine, so it always uses its GH200
preset, and only the architecture changes.

`configs/ubuntu-runner.json` runs a small ShockTube ensemble with
`components/euler_member.py`. Cycle 0 perturbs each member's initial conditions, as libMLEF's
`run_ensemble.sh` does, and later cycles restart each member from the analysis. A member exits
20 if the solution goes non-finite, because euler-miniapp itself exits 0 then.

libMLEF's `mlef` needs CUDA, so the analysis is `components/mlef_stub.py` for now. It lays the
forecasts out the way `mlef` reads them and writes an `mlef.input`, then copies each forecast
unchanged to where `mlef` would write the analysis.

## Testing

```bash
scripts/ubuntu-runner/test.sh      # builds, then runs every test in Flux; fails on skips
flux start --test-size=2 python3 -m unittest discover -s tests
```

The euler-miniapp tests skip themselves until `main_cpu` is built. CI fetches the submodules
with the `SAGESTGIT_TOKEN` repository secret. Its CUDA job only compiles, since GitHub's
runners have no GPU.

On Tuolumne, set `bank` in `configs/smoke.json`, then from a login node:

```bash
flux getattr instance-level        # must print 0 for login mode
flux jobs -n -o '{id}' f111111     # must print: JobID f111111 unknown
./campaign.py run configs/smoke.json        # login mode
./campaign.py status configs/smoke.json
```

Use a fresh `run_dir` to try allocation mode with `batch`. Lost jobs are detected from the
"unknown" wording; if yours differs, a campaign with a lost job pauses instead of resubmitting,
so fix the pattern in `parse_jobs`.

`configs/smoke.json` uses `pbatch` because LC's Tuolumne page prohibits batch jobs and job chaining
in `pdebug`. Check LC's current rules on job counts and long-running login-node processes before
a large campaign.

## Limitations

- Exit 0 counts as success, so a member that exits 0 without its output only shows up when the
  analysis fails. Have members exit nonzero if their output is missing.
- In allocation mode, the time check covers one job at a time, so queued jobs can still run out
  of time. They're resubmitted without using a retry on resume.
- Don't run two copies on one `run_dir`.
- If `campaign.py` dies in the moment between a submit and recording the job ID, that job keeps
  running untracked, and its member is submitted again in a new directory.
