#!/usr/bin/env python3
"""Minimal wrapper over Flux or Slurm: ensemble -> post-analysis -> repeat. See README.md.

Runs in one of two modes, chosen by where `run` starts:
  login       on a login node; each job goes to the queue on its own
              (flux submit -q -B, or sbatch -q -A)
  allocation  inside flux batch/alloc or sbatch/salloc; jobs share the allocation's nodes
              (flux submit, or srun --exact job steps)
`batch` sizes an allocation from the time estimates and starts `run` inside it.
The scheduler launches every job. This only adds what it has no feature for: retries,
reacting to failure reasons, waiting for all members across retries, and cycles.

Usage: campaign.py run|batch CONFIG [--dry-run]  |  campaign.py status CONFIG
Exit codes: 0 completed, 1 failed, 2 invalid config, 3 paused. Rerun the same command to resume; rerunning
a failed campaign retries its failed tasks with fresh retry budgets.
"""
import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

JOB_FORMAT = "{id.f58}|{result}|{returncode}|{runtime}|{exception.note}"
MISSING_STRIKES = 3      # sightings as "unknown" before believing a job is gone
MAX_POLL_FAILURES = 20   # failed status queries in a row before pausing
NO_TIME_LIMIT = 2 ** 31  # `flux job timeleft` prints UINT32_MAX when unlimited
LIFETIME_ERROR = "exceeds remaining instance lifetime"


EXIT_CONFIG = 2          # the config is invalid; nothing was started


def config_error(message):
    print(message, file=sys.stderr)
    sys.exit(EXIT_CONFIG)


class Pause(Exception):
    pass


class PollError(Exception):
    pass


def log(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)


def seconds(duration):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", str(duration))
    if not match:
        config_error(f"config error: bad duration {duration!r} (use e.g. 90s, 30m, 4h)")
    return float(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]


def command(args, env=None):
    """Run a scheduler command, returning (exit code, stdout, stderr)."""
    full_env = dict(os.environ, FLUX_F58_FORCE_ASCII="1", **(env or {}))
    try:
        done = subprocess.run([str(a) for a in args], env=full_env, timeout=120,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def flux(*args, env=None):
    return command(["flux", *args], env)


def scheduler(cfg):
    return cfg.get("scheduler", "flux")


def poll(cfg, job_ids):
    """Return {id: status}, with None for jobs the scheduler says it doesn't know."""
    if scheduler(cfg) == "slurm":
        steps = [j for j in job_ids if j.startswith(STEP_PREFIX)]
        jobs = {j: poll_step(j) for j in steps}
        batch = [j for j in job_ids if j not in jobs]
        if batch:
            jobs.update(poll_slurm(batch))
        return jobs
    code, out, err = flux("jobs", "-n", "-o", JOB_FORMAT, *job_ids)
    return parse_jobs(job_ids, code, out, err)


def parse_jobs(job_ids, code, out, err):
    jobs = {}
    for line in out.splitlines():
        fields = line.split("|", 4)
        if len(fields) == 5 and fields[0] in job_ids:
            rc, runtime = fields[2].strip(), fields[3].strip()
            jobs[fields[0]] = {"result": fields[1].strip() or None,
                               "rc": int(rc) if re.fullmatch(r"-?\d+", rc) else None,
                               "runtime": float(runtime) if runtime else None,
                               "note": fields[4].strip()}
    # Flux can exit 0 here if it found other ids, so read stderr instead of the exit code.
    unknown = set(re.findall(r"JobID (\S+) unknown", err))
    for job_id in job_ids:
        if job_id not in jobs:
            if job_id not in unknown:
                # A failed query doesn't mean the job is gone. Assuming so would
                # resubmit a running ensemble during a brief Flux hiccup.
                raise PollError(f"no status for {job_id} (exit {code}): {err.strip()}")
            jobs[job_id] = None
    return jobs


def timeleft(cfg):
    """Seconds left in this allocation, or None if it has no limit."""
    if scheduler(cfg) == "slurm":
        code, out, _ = command(["squeue", "-h", "-j", os.environ.get("SLURM_JOB_ID", ""),
                                "-o", "%L"])
        return parse_slurm_time(out.strip()) if code == 0 else None
    code, out, _ = flux("job", "timeleft")
    try:
        left = float(out) if code == 0 else NO_TIME_LIMIT
    except ValueError:
        return None
    return None if left >= NO_TIME_LIMIT else left


def detect_mode(cfg=None):
    """'allocation' inside an allocation, 'login' outside one, None inside a plain job."""
    if cfg and scheduler(cfg) == "slurm":
        if "SLURM_JOB_ID" not in os.environ:
            return "login"
        # A batch script or salloc shell is the allocation itself; a numbered step is a job.
        step = os.environ.get("SLURM_STEP_ID", "")
        return None if step.isdigit() and int(step) < SLURM_SPECIAL_STEPS else "allocation"
    code, out, _ = flux("getattr", "instance-level")
    if code == 0 and out.strip() not in ("", "0"):
        return "allocation"
    return None if "FLUX_JOB_ID" in os.environ else "login"


# ---- Slurm -------------------------------------------------------------------------------

SLURM_FORMAT = "JobID,State,ExitCode,ElapsedRaw"
SLURM_SPECIAL_STEPS = 4294967290  # ids from here up are batch, interactive and extern steps
STEP_PREFIX = "step-"             # job ids of srun steps launched from allocation mode
STEPS = {}                        # job id -> (Popen, start time, srun log) for this process
SLURM_RESULTS = {"COMPLETED": "COMPLETED", "TIMEOUT": "TIMEOUT", "CANCELLED": "CANCELED"}


def slurm_time(limit):
    """Seconds as a Slurm time limit, rounded up to whole minutes (Slurm's resolution)."""
    minutes = max(1, math.ceil(limit / 60))
    return f"{minutes // 60}:{minutes % 60:02d}:00"


def parse_slurm_time(text):
    """Seconds from squeue's %L ([days-]hours:minutes:seconds), or None if unlimited."""
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text)
    if not match:
        return None
    days, hours, minutes, secs = (int(g or 0) for g in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + secs


def parse_sacct(job_ids, out):
    """Statuses from `sacct -n -P -X -o JobID,State,ExitCode,ElapsedRaw` output."""
    jobs = {}
    for line in out.splitlines():
        fields = line.split("|")
        if len(fields) != 4 or fields[0] not in job_ids:
            continue
        state = fields[1].split()[0] if fields[1].strip() else ""
        code, _, signal = fields[2].partition(":")
        rc = 128 + int(signal) if signal.isdigit() and int(signal) else \
            int(code) if code.isdigit() else None
        finished = state not in ("", "PENDING", "RUNNING", "REQUEUED", "RESIZING", "SUSPENDED",
                                 "CONFIGURING", "COMPLETING")
        jobs[fields[0]] = {"result": SLURM_RESULTS.get(state, state) if finished else None,
                           "rc": rc if finished else None,
                           "runtime": float(fields[3]) if fields[3].isdigit() else None,
                           "note": "" if state in SLURM_RESULTS else fields[1].strip()}
    return jobs


def poll_slurm(job_ids):
    code, out, err = command(["sacct", "-n", "-P", "-X", "-o", SLURM_FORMAT,
                              "-j", ",".join(job_ids)])
    if code != 0:
        raise PollError(f"sacct failed (exit {code}): {err.strip()}")
    jobs = parse_sacct(job_ids, out)
    missing = [j for j in job_ids if j not in jobs]
    if missing:
        # Accounting can lag behind a fresh submit; the controller's live queue can't.
        code, out, err = command(["squeue", "-h", "-o", "%i", "-u",
                                  os.environ.get("USER", "")])
        if code != 0:
            raise PollError(f"squeue failed (exit {code}): {err.strip()}")
        queued = set(out.split())
        for job_id in missing:
            jobs[job_id] = {"result": None, "rc": None, "runtime": None, "note": ""} \
                if job_id in queued else None
    return jobs


def poll_step(job_id):
    """Status of an srun step this process launched, or None if it isn't one of ours."""
    if job_id not in STEPS:
        return None  # launched by an earlier campaign.py, so it ended along with that one
    proc, start, srun_log = STEPS[job_id]
    rc = proc.poll()
    runtime = time.time() - start
    if rc is None:
        return {"result": None, "rc": None, "runtime": runtime, "note": ""}
    with open(srun_log, errors="replace") as handle:
        messages = handle.read()
    if "DUE TO TIME LIMIT" in messages:
        result = "TIMEOUT"
    elif "CANCELLED" in messages:
        result = "CANCELED"
    else:
        result = "COMPLETED" if rc == 0 else "FAILED"
    note = "" if rc == 0 else " ".join(messages.strip().splitlines()[-1:])[:200]
    return {"result": result, "rc": rc if rc >= 0 else 128 - rc, "runtime": runtime,
            "note": note}


def sbatch_args(cfg, kind, job_name, attempt_dir, limit):
    comp = cfg[kind]
    nodes = ["-N", str(comp["nodes"])] if "nodes" in comp else []
    return ["sbatch", "--parsable", f"--job-name={job_name}", f"--chdir={attempt_dir}",
            f"--output={os.path.join(attempt_dir, 'stdout.log')}",
            f"--error={os.path.join(attempt_dir, 'stderr.log')}",
            f"--time={slurm_time(limit)}", *queue_args(cfg), *nodes,
            *comp.get("slurm_args", []), "--wrap", shlex.join(comp["command"])]


def srun_args(cfg, kind, job_name, attempt_dir, limit):
    comp = cfg[kind]
    # Without -N and --exact a step would take the whole allocation.
    return ["srun", "--exact", "-N", str(comp.get("nodes", 1)), f"--job-name={job_name}",
            f"--chdir={attempt_dir}", f"--output={os.path.join(attempt_dir, 'stdout.log')}",
            f"--error={os.path.join(attempt_dir, 'stderr.log')}",
            f"--time={slurm_time(limit)}", *comp.get("slurm_args", []), *comp["command"]]


def start_step(args, attempt_dir, env):
    srun_log = os.path.join(attempt_dir, "srun.log")
    with open(srun_log, "w") as log_handle:
        proc = subprocess.Popen(args, env=dict(os.environ, **env), stdout=log_handle,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    job_id = f"{STEP_PREFIX}{proc.pid}"
    STEPS[job_id] = (proc, time.time(), srun_log)
    return job_id


# ---- config -----------------------------------------------------------------------------

# Every key a config may contain: name -> (type check, required). Anything else is an error.
def _int(low, high=None):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= low and \
        (high is None or v <= high), f"an integer >= {low}" + (f" and <= {high}" if high else "")


_POSITIVE = (lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0,
             "a number > 0")
_TEXT = (lambda v: isinstance(v, str) and v != "", "a non-empty string")
_NAME = (lambda v: isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", v) is not None,
         "letters, digits, _ . or - only")
_OBJECT = (lambda v: isinstance(v, dict), "an object")
_ARGS = (lambda v: isinstance(v, list) and all(isinstance(a, str) for a in v), "a list of strings")
_COMMAND = (lambda v: isinstance(v, list) and v != [] and all(isinstance(a, str) for a in v),
            "a non-empty list of strings")
_DURATION = (lambda v: isinstance(v, str) and re.fullmatch(r"\d+(\.\d+)?[smhd]", v) is not None,
             "a duration like 90s, 30m, 1.5h or 2d")
_RUN_TIME = (lambda v: _DURATION[0](v) and seconds(v) > 0, "a duration greater than 0, like 30m")

_SCHEDULER = (lambda v: v in ("flux", "slurm"), '"flux" or "slurm"')

TOP = {"name": (_NAME, True), "run_dir": (_TEXT, True), "cycles": (_int(1), False),
       "scheduler": (_SCHEDULER, False),
       "poll_seconds": (_POSITIVE, False), "queue": (_TEXT, False), "bank": (_TEXT, False),
       "time": (_OBJECT, True), "allocation": (_OBJECT, False),
       "ensemble": (_OBJECT, True), "analysis": (_OBJECT, True)}
TIME = {"member": (_RUN_TIME, True), "analysis": (_RUN_TIME, True),
        "buffer": (_DURATION, False), "reserve": (_DURATION, False)}
ALLOCATION = {"nodes": (_int(1), False), "time": (_RUN_TIME, False),
              "max_time": (_RUN_TIME, False), "flux_args": (_ARGS, False),
              "slurm_args": (_ARGS, False)}
COMPONENT = {"command": (_COMMAND, True), "nodes": (_int(1), False), "flux_args": (_ARGS, False),
             "slurm_args": (_ARGS, False),
             "params": (_OBJECT, False), "retries": (_int(0), False),
             "failures": (_OBJECT, False)}
ENSEMBLE = dict(COMPONENT, size=(_int(1), True))
RULE = {"exit_code": (_int(3, 125), False), "retries": (_int(0), True), "set": (_OBJECT, False),
        "time_scale": (_POSITIVE, False)}

# Flags campaign.py sets itself, so flux_args and slurm_args must not repeat them.
MANAGED_FLAGS = ["--job-name", "--cwd", "--output", "--error", "-t", "--time-limit",
                 "-q", "--queue", "-B", "--bank"]
SLURM_MANAGED_FLAGS = ["-J", "--job-name", "-D", "--chdir", "-o", "--output", "-e", "--error",
                       "-t", "--time", "-q", "--qos", "-A", "--account", "--parsable", "--wrap",
                       "--exact"]


def _check(obj, schema, where, errors):
    """Append an error for each unknown, missing or wrongly typed key; return the valid keys."""
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be an object")
        return {}
    for key in obj:
        if key not in schema:
            errors.append(f"{where}.{key}: unknown key (allowed: {', '.join(sorted(schema))})")
    valid = {}
    for key, ((test, expected), required) in schema.items():
        if key not in obj:
            if required:
                errors.append(f"{where}.{key}: required")
        elif not test(obj[key]):
            errors.append(f"{where}.{key}: must be {expected}, got {json.dumps(obj[key])}")
        else:
            valid[key] = obj[key]
    return valid


def _check_flags(args, flags, where, errors):
    for arg in args:
        for flag in flags:
            short = not flag.startswith("--")
            if arg == flag or arg.startswith(flag + "=") or (short and arg.startswith(flag)):
                errors.append(f"{where}: {arg!r} is set by campaign.py; remove it")


def validate(cfg):
    """Return a list of every problem in a config; empty if it is valid."""
    errors = []
    top = _check(cfg, TOP, "config", errors)
    _check(top.get("time", {}), TIME, "time", errors)
    alloc = _check(top.get("allocation", {}), ALLOCATION, "allocation", errors)
    _check_flags(alloc.get("flux_args", []), MANAGED_FLAGS + ["-N", "--nodes", "--exclusive"],
                 "allocation.flux_args", errors)
    _check_flags(alloc.get("slurm_args", []), SLURM_MANAGED_FLAGS + ["-N", "--nodes"],
                 "allocation.slurm_args", errors)
    if "time" in alloc and "max_time" in alloc:
        errors.append("allocation: set time or max_time, not both")
    for name, schema in (("ensemble", ENSEMBLE), ("analysis", COMPONENT)):
        if name not in top:
            continue
        comp = _check(top[name], schema, name, errors)
        nodes = ["-N", "--nodes"] if "nodes" in comp else []
        _check_flags(comp.get("flux_args", []), MANAGED_FLAGS + nodes, f"{name}.flux_args", errors)
        _check_flags(comp.get("slurm_args", []), SLURM_MANAGED_FLAGS + nodes,
                     f"{name}.slurm_args", errors)
        codes = {}
        for reason, rule in comp.get("failures", {}).items():
            where = f"{name}.failures.{reason}"
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", reason):
                errors.append(f"{where}: reason names must be UPPER_CASE, like BAD_INPUT")
            given = rule
            rule = _check(rule, RULE, where, errors)
            if reason != "TIMEOUT" and isinstance(given, dict) and "exit_code" not in given:
                errors.append(f"{where}.exit_code: required (only TIMEOUT can omit it)")
            code = rule.get("exit_code")
            if code is not None and code in codes:
                errors.append(f"{where}.exit_code: {code} is already used by {codes[code]}")
            codes.setdefault(code, reason)
    return errors


def load_config(path):
    path = os.path.abspath(path)
    try:
        with open(path) as handle:
            cfg = json.load(handle)
    except (OSError, ValueError) as exc:
        config_error(f"config error: cannot read {path}: {exc}")
    errors = validate(cfg)
    if errors:
        config_error("\n".join(f"config error: {error}" for error in errors))
    base = os.path.dirname(path)
    cfg["config_path"] = path
    cfg["run_dir"] = os.path.normpath(os.path.join(base, cfg["run_dir"]))
    for name in ("ensemble", "analysis"):
        comp = cfg[name]
        comp["command"] = [c.replace("{config_dir}", base) for c in comp["command"]]
    return cfg


def plan(cfg):
    """Allocation size and time: cycles x (waves x member limit + analysis limit) + reserve."""
    alloc, size = cfg.get("allocation", {}), cfg["ensemble"]["size"]
    member_nodes = cfg["ensemble"].get("nodes", 1)
    nodes = alloc.get("nodes", size * member_nodes)
    for name in ("ensemble", "analysis"):
        if cfg[name].get("nodes", 1) > nodes:
            config_error(f"config error: {name} needs more nodes than allocation.nodes ({nodes})")
    waves = math.ceil(size * member_nodes / nodes)
    total = cfg.get("cycles", 1) * (waves * time_limit(cfg, "ensemble")
                                    + time_limit(cfg, "analysis"))
    total += seconds(cfg["time"].get("reserve", "0s"))
    if "time" in alloc:
        request = seconds(alloc["time"])
    else:
        request = min(total, seconds(alloc.get("max_time", "9999d")))
    return {"nodes": nodes, "waves": waves, "total": total, "request": request}


def time_limit(cfg, kind):
    """A job's limit is its estimated runtime plus the buffer."""
    times = cfg["time"]
    estimate = times["member" if kind == "ensemble" else "analysis"]
    return seconds(estimate) + seconds(times.get("buffer", "0s"))


# ---- state ------------------------------------------------------------------------------

def save(cfg, state):
    fd, temp = tempfile.mkstemp(dir=cfg["run_dir"], prefix=".state-")
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle, indent=1)
    os.replace(temp, os.path.join(cfg["run_dir"], "state.json"))


def new_task(cfg, kind, name, params):
    return {"kind": kind, "name": name, "status": "PENDING", "params": params,
            "time": time_limit(cfg, kind), "attempt": 0, "job": None, "dir": None, "missing": 0,
            "unpredicted": 0, "predicted": {}, "history": [], "next_cycle": None}


def member_params(cfg, state):
    return dict(cfg["ensemble"].get("params", {}), **state["carry"])


def new_cycle(cfg, state):
    log(f"starting cycle {len(state['cycles'])}")
    state["cycles"].append({"analysis": None, "members": [
        new_task(cfg, "ensemble", f"member_{i}", member_params(cfg, state))
        for i in range(cfg["ensemble"]["size"])]})


# ---- one job ----------------------------------------------------------------------------

def queue_args(cfg):
    bank = "-A" if scheduler(cfg) == "slurm" else "-B"
    return [arg for flag, key in (("-q", "queue"), (bank, "bank")) if cfg.get(key)
            for arg in (flag, cfg[key])]


def submit_args(cfg, mode, kind, job_name, attempt_dir, limit):
    comp = cfg[kind]
    nodes = ["-N", str(comp["nodes"])] if "nodes" in comp else []
    # An allocation has its own scheduler, which rejects -q and -B.
    queue = queue_args(cfg) if mode == "login" else []
    return ["submit", f"--job-name={job_name}", f"--cwd={attempt_dir}", "--output=stdout.log",
            "--error=stderr.log", "-t", f"{math.ceil(limit)}s", *queue, *nodes,
            *comp.get("flux_args", []), *comp["command"]]


def submit(cfg, mode, state, task):
    cycle = len(state["cycles"]) - 1
    attempt_dir = os.path.join(cfg["run_dir"], f"cycle_{cycle}", task["name"],
                               f"attempt_{task['attempt']}")
    os.makedirs(attempt_dir, exist_ok=True)
    task["dir"] = attempt_dir
    params_file = os.path.join(attempt_dir, "params.json")
    with open(params_file, "w") as handle:
        json.dump(task["params"], handle, indent=1)
    env = {"ORCH_DIR": attempt_dir, "ORCH_PARAMS": params_file, "ORCH_CYCLE": str(cycle),
           "ORCH_ATTEMPT": str(task["attempt"]),
           "ORCH_ENSEMBLE_SIZE": str(len(state["cycles"][-1]["members"]))}
    if task["kind"] == "ensemble":
        env["ORCH_MEMBER_ID"] = task["name"].split("_")[1]
    else:
        env["ORCH_MANIFEST"] = os.path.join(cfg["run_dir"], f"cycle_{cycle}", "manifest.json")
    job_name = f"{cfg['name']}-c{cycle}-{task['name']}-a{task['attempt']}"
    if scheduler(cfg) == "slurm" and mode == "allocation":
        try:
            job_id = start_step(srun_args(cfg, task["kind"], job_name, attempt_dir,
                                          task["time"]), attempt_dir, env)
        except OSError as exc:
            finish(cfg, task, {"result": "SUBMIT_FAILED", "rc": None, "runtime": None,
                               "note": str(exc)})
            return
        code, out, err = 0, job_id, ""
    elif scheduler(cfg) == "slurm":
        code, out, err = command(sbatch_args(cfg, task["kind"], job_name, attempt_dir,
                                             task["time"]), env=env)
        out = out.split(";")[0]  # --parsable prints "id;cluster" on multi-cluster systems
    else:
        code, out, err = flux(*submit_args(cfg, mode, task["kind"], job_name, attempt_dir,
                                           task["time"]), env=env)
    if code != 0 and LIFETIME_ERROR in err:
        # The allocation's time ran down since the check; not the job's fault, so no retry used.
        task["status"] = "PENDING"
        return
    if code != 0 or not out.strip():
        finish(cfg, task, {"result": "SUBMIT_FAILED", "rc": None, "runtime": None,
                           "note": err.strip()})
        return
    task["job"], task["status"], task["missing"] = out.split()[-1], "RUNNING", 0
    task["mode"] = mode
    log(f"{task['name']} attempt {task['attempt']} submitted as {task['job']}")


def read_json(path):
    """Return parsed JSON, or None if the file is absent. Raises ValueError if malformed."""
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def classify(comp, job):
    """Return (kind, reason). Flux's job result and exit code decide everything."""
    if job is None:
        return "lost", "unknown to the scheduler"
    if job["result"] == "CANCELED":
        return "canceled", "CANCELED"
    failures = comp.get("failures", {})
    for reason, rule in failures.items():
        if "exit_code" in rule and rule["exit_code"] == job["rc"]:
            return "predicted", reason
    if job["result"] == "TIMEOUT" and "TIMEOUT" in failures:
        return "predicted", "TIMEOUT"
    if job["result"] == "COMPLETED" and job["rc"] == 0:
        return "success", None
    return "unpredicted", f"{job['result']} rc={job['rc']} {job['note']}".strip()


def finish(cfg, task, job):
    """Record one ended attempt and decide what happens to the task next."""
    comp = cfg[task["kind"]]
    kind, reason = classify(comp, job)
    if kind == "success" and task["kind"] == "analysis":
        try:
            task["next_cycle"] = read_json(os.path.join(task["dir"], "next_cycle.json"))
        except ValueError as exc:
            kind, reason = "unpredicted", f"bad next_cycle.json: {exc}"
    if kind == "predicted":
        rule = comp["failures"][reason]
        used = task["predicted"].get(reason, 0) + 1
        task["predicted"][reason] = used
        if used > rule["retries"]:
            decision = f"gave up after {rule['retries']} retries"
        else:
            try:
                suggested = read_json(os.path.join(task["dir"], "retry_params.json")) or {}
                task["params"].update(rule.get("set", {}), **suggested)
                task["time"] *= rule.get("time_scale", 1)
                decision = f"retry {used}/{rule['retries']}"
            except ValueError as exc:
                decision = f"gave up: bad retry_params.json: {exc}"
    elif kind == "unpredicted":
        task["unpredicted"] += 1
        limit = comp.get("retries", 0)
        over = task["unpredicted"] > limit
        decision = f"gave up after {limit} retries" if over else \
            f"retry {task['unpredicted']}/{limit}"
    else:
        decision = {"success": "done", "lost": "resubmit (no retry used)",
                    "canceled": "not retried"}[kind]

    task["status"] = {"success": "DONE", "canceled": "CANCELED"}.get(
        kind, "FAILED" if decision.startswith("gave up") else "PENDING")
    task["history"].append({"attempt": task["attempt"], "job": task["job"], "kind": kind,
                            "reason": reason, "decision": decision,
                            "runtime": job["runtime"] if job else None})
    log(f"{task['name']} attempt {task['attempt']}: {kind}"
        f"{' (' + reason + ')' if reason else ''} -> {decision}")


# ---- the campaign -----------------------------------------------------------------------

def drive(cfg, mode, state, tasks):
    """Submit and poll until every task has finished, retrying as needed."""
    poll_failures = 0
    while True:
        pending = [t for t in tasks if t["status"] == "PENDING"]
        left = timeleft(cfg) if pending and mode == "allocation" else None
        ready = [t for t in pending if left is None or t["time"] <= left]
        for task in ready:
            task["attempt"] += 1  # saved first, so a crash mid-submit can't reuse a directory
            save(cfg, state)
            submit(cfg, mode, state, task)
            save(cfg, state)  # record the job id at once

        running = [t for t in tasks if t["status"] == "RUNNING"]
        if running:
            try:
                jobs = {}
                for start in range(0, len(running), 500):
                    jobs.update(poll(cfg, [t["job"] for t in running[start:start + 500]]))
                poll_failures = 0
            except PollError as exc:
                poll_failures += 1
                log(f"warning: {exc}")
                if poll_failures >= MAX_POLL_FAILURES:
                    raise Pause(f"cannot read job states: {exc}")
                jobs = {}
            for task in running:
                if task["job"] not in jobs:
                    continue
                job = jobs[task["job"]]
                if job is None:
                    task["missing"] += 1
                    if task["missing"] >= MISSING_STRIKES:
                        finish(cfg, task, None)
                elif job["result"]:
                    finish(cfg, task, job)
        save(cfg, state)

        if not any(t["status"] in ("PENDING", "RUNNING") for t in tasks):
            return
        if len(ready) < len(pending) and not any(t["status"] == "RUNNING" for t in tasks):
            raise Pause("the next job needs more time than this allocation has left")
        time.sleep(cfg.get("poll_seconds", 60))


def advance(cfg, mode, state):
    while True:
        cycle = state["cycles"][-1]
        drive(cfg, mode, state, cycle["members"])
        if any(t["status"] != "DONE" for t in cycle["members"]):
            return "failed", "ensemble did not fully succeed; analysis was not run"
        # The dependency: the analysis task only exists once every member is DONE.
        if cycle["analysis"] is None:
            with open(os.path.join(os.path.dirname(os.path.dirname(cycle["members"][0]["dir"])),
                                   "manifest.json"), "w") as handle:
                json.dump({"cycle": len(state["cycles"]) - 1,
                           "members": [{"name": t["name"], "dir": t["dir"], "params": t["params"]}
                                       for t in cycle["members"]]}, handle, indent=1)
            cycle["analysis"] = new_task(cfg, "analysis", "analysis",
                                         dict(cfg["analysis"].get("params", {})))
        drive(cfg, mode, state, [cycle["analysis"]])
        if cycle["analysis"]["status"] != "DONE":
            return "failed", "analysis did not succeed"
        directive = cycle["analysis"]["next_cycle"] or {}
        state["carry"].update(directive.get("params", {}))
        if directive.get("continue", True) is False:
            return "completed", "analysis ended the campaign"
        if len(state["cycles"]) >= cfg.get("cycles", 1):
            return "completed", f"finished {len(state['cycles'])} cycle(s)"
        new_cycle(cfg, state)
        save(cfg, state)


def run(cfg, mode="login"):
    os.makedirs(cfg["run_dir"], exist_ok=True)
    path = os.path.join(cfg["run_dir"], "state.json")
    if os.path.exists(path):
        with open(path) as handle:
            state = json.load(handle)
        if state["status"] == "completed":
            log("already completed")
            return 0
        cycle = state["cycles"][-1]
        tasks = cycle["members"] + ([cycle["analysis"]] if cycle["analysis"] else [])
        if mode == "allocation" and any(t["status"] == "RUNNING" and t.get("mode") == "login"
                                        for t in tasks):
            # An allocation can't see queue jobs, so it would wrongly resubmit them.
            sys.exit("error: jobs submitted from the login node are still tracked; "
                     "resume on the login node until they finish")
        for task in tasks:
            if task["status"] in ("FAILED", "CANCELED"):
                # Fresh budget and the current config's settings.
                params = member_params(cfg, state) if task["kind"] == "ensemble" \
                    else dict(cfg["analysis"].get("params", {}))
                task.update(status="PENDING", params=params, unpredicted=0, predicted={},
                            time=time_limit(cfg, task["kind"]))
        log(f"resuming at cycle {len(state['cycles']) - 1} in {mode} mode")
    else:
        state = {"status": "running", "message": "", "carry": {}, "cycles": []}
        log(f"running in {mode} mode")
        new_cycle(cfg, state)
    state.update(status="running", message="")
    save(cfg, state)
    try:
        status, message = advance(cfg, mode, state)
    except Pause as exc:
        status, message = "paused", str(exc)
    state.update(status=status, message=message)
    save(cfg, state)
    log(f"campaign {status}: {message}")
    return {"completed": 0, "failed": 1, "paused": 3}[status]


# ---- commands ---------------------------------------------------------------------------

def batch_args(cfg):
    """The flux batch (without the leading "flux") or sbatch command for `batch`."""
    times = plan(cfg)
    if scheduler(cfg) == "slurm":
        return ["sbatch", "--parsable", "-N", str(times["nodes"]),
                f"--time={slurm_time(times['request'])}", f"--job-name={cfg['name']}",
                *queue_args(cfg), "--output=" + os.path.join(cfg["run_dir"], "driver-%j.out"),
                *cfg.get("allocation", {}).get("slurm_args", []),
                "--wrap", shlex.join([sys.executable, os.path.abspath(__file__), "run",
                                      cfg["config_path"]])]
    return ["batch", "-N", str(times["nodes"]), "-t", f"{math.ceil(times['request'])}s",
            "--exclusive", f"--job-name={cfg['name']}", *queue_args(cfg),
            "--output=" + os.path.join(cfg["run_dir"], "driver-{{id}}.out"),
            *cfg.get("allocation", {}).get("flux_args", []),
            "--wrap", sys.executable, os.path.abspath(__file__), "run", cfg["config_path"]]


def dry_run(cfg):
    print(f"{cfg['ensemble']['size']} member job(s) per cycle, {cfg.get('cycles', 1)} cycle(s)")
    for mode in ("login", "allocation"):
        print(f"\n{mode} mode, example job commands:")
        for kind, name in (("ensemble", "member_0"), ("analysis", "analysis")):
            job = (cfg, kind, f"{cfg['name']}-c0-{name}-a1", "<attempt dir>",
                   time_limit(cfg, kind))
            if scheduler(cfg) == "slurm":
                args = srun_args(*job) if mode == "allocation" else sbatch_args(*job)
            else:
                args = ["flux", *submit_args(job[0], mode, *job[1:])]
            print("  " + shlex.join(args))
    times = plan(cfg)
    print(f"\nbatch: {times['waves']} wave(s) per cycle, campaign {times['total'] / 60:.1f}m, "
          f"requesting {times['nodes']} node(s) for {times['request'] / 60:.1f}m")
    if times["request"] < times["total"]:
        print("  note: shorter than the plan, so expect to resume in another allocation")
    args = batch_args(cfg)
    print("  " + shlex.join(args if scheduler(cfg) == "slurm" else ["flux", *args]))
    return 0


def status(cfg):
    path = os.path.join(cfg["run_dir"], "state.json")
    if not os.path.exists(path):
        print("not started")
        return 0
    with open(path) as handle:
        state = json.load(handle)
    print(f"{state['status']}: {state['message']}")
    runtimes = []
    for index, cycle in enumerate(state["cycles"]):
        tasks = cycle["members"] + ([cycle["analysis"]] if cycle["analysis"] else [])
        done = sum(t["status"] == "DONE" for t in cycle["members"])
        print(f"cycle {index}: {done}/{len(cycle['members'])} members done, analysis "
              f"{cycle['analysis']['status'] if cycle['analysis'] else 'not started'}")
        for task in tasks:
            for entry in task["history"]:
                if entry["kind"] == "success":
                    if task["kind"] == "ensemble" and entry["runtime"]:
                        runtimes.append(entry["runtime"])
                else:
                    print(f"  {task['name']} attempt {entry['attempt']}: {entry['kind']} "
                          f"({entry['reason']}) -> {entry['decision']}")
    if runtimes:
        print(f"member runtime: mean {sum(runtimes) / len(runtimes) / 60:.1f}m, "
              f"max {max(runtimes) / 60:.1f}m, estimate {seconds(cfg['time']['member']) / 60:.1f}m")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "batch", "status"])
    parser.add_argument("config")
    parser.add_argument("--dry-run", action="store_true", help="only print example commands")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.command == "status":
        return status(cfg)
    if args.dry_run:
        return dry_run(cfg)
    mode = detect_mode(cfg)
    if mode is None:
        sys.exit("error: start campaign.py on a login node or inside an allocation "
                 "(flux batch/alloc, sbatch/salloc), not inside a single job")
    if mode == "login" and cfg.get("bank") == "YOUR_BANK":
        config_error("config error: set bank in the config")
    if args.command == "batch":
        if mode != "login":
            sys.exit("error: run batch from a login node")
        os.makedirs(cfg["run_dir"], exist_ok=True)
        args = batch_args(cfg)
        code, out, err = command(args if scheduler(cfg) == "slurm" else ["flux", *args])
        print(out.strip() or err.strip())
        return code
    return run(cfg, mode)


if __name__ == "__main__":
    sys.exit(main())
