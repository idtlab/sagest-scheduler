#!/usr/bin/env python3
"""A stand-in for sbatch, sacct, squeue and srun, so tests can drive campaign.py's Slurm code
without a cluster. Run through the symlinks in bin/; the command name picks the behavior.

Jobs run as local processes. State lives in $FAKE_SLURM_DIR: one JSON file per job, and a
calls.log with every command line. $FAKE_SLURM_MINUTE sets how many real seconds a Slurm minute
lasts, so tests can hit time limits quickly (Slurm limits have minute resolution).

Only the options campaign.py uses are understood; it mimics the output formats it parses.
"""
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time

STATE = os.environ.get("FAKE_SLURM_DIR", "")
MINUTE = float(os.environ.get("FAKE_SLURM_MINUTE", "60"))
# Options that take a separate value; everything else starting with - is a flag.
VALUED = {"-N", "-n", "-c", "-C", "-G", "-J", "-D", "-o", "-e", "-t", "-q", "-A", "-u", "-j"}


def limit_seconds(text):
    """Slurm time limit ([D-]H:MM:SS, M:SS or minutes) in fake seconds."""
    match = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d+):(\d+)", text)
    if match:
        days, hours, minutes, secs = (int(g or 0) for g in match.groups())
        return ((days * 24 + hours) * 60 + minutes + secs / 60) * MINUTE
    if re.fullmatch(r"\d+:\d+", text):
        minutes, secs = text.split(":")
        return (int(minutes) + int(secs) / 60) * MINUTE
    return int(text) * MINUTE


def parse(argv):
    """Split argv into ({option: value}, positional args)."""
    opts, rest, i = {}, [], 0
    while i < len(argv):
        arg = argv[i]
        if rest or not arg.startswith("-"):
            rest.append(arg)
        elif arg.startswith("--") and "=" in arg:
            key, value = arg.split("=", 1)
            opts[key] = value
        elif arg in VALUED or arg in ("--wrap",):
            opts[arg] = argv[i + 1]
            i += 1
        elif len(arg) > 2 and arg[:2] in VALUED and not arg.startswith("--"):
            opts[arg[:2]] = arg[2:]
        else:
            opts[arg] = True
        i += 1
    return opts, rest


def job_path(job_id):
    return os.path.join(STATE, "jobs", f"{job_id}.json")


def read_job(job_id):
    with open(job_path(job_id)) as handle:
        return json.load(handle)


def write_job(job):
    temp = job_path(job["id"]) + ".tmp"
    with open(temp, "w") as handle:
        json.dump(job, handle)
    os.replace(temp, job_path(job["id"]))


def next_id():
    os.makedirs(os.path.join(STATE, "jobs"), exist_ok=True)
    with open(os.path.join(STATE, "counter"), "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        value = int(handle.read() or 1000) + 1
        handle.seek(0)
        handle.truncate()
        handle.write(str(value))
    return str(value)


def run_limited(command, cwd, stdout, stderr, limit, env):
    """Run command with a time limit; return (exit code, signal, timed out)."""
    proc = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr, env=env,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        rc = proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait()
        return 0, signal.SIGTERM, True
    return (0, -rc, False) if rc < 0 else (rc, 0, False)


def sbatch(argv):
    opts, _ = parse(argv)
    job_id = next_id()
    limit = limit_seconds(opts.get("--time", "60"))
    output = opts.get("--output", "slurm-%j.out").replace("%j", job_id)
    job = {"id": job_id, "name": opts.get("--job-name", "wrap"), "state": "PENDING",
           "exit": "0:0", "submit": time.time(), "start": None, "end": None, "limit": limit,
           "cwd": opts.get("--chdir", os.getcwd()), "wrap": opts["--wrap"], "output": output,
           "error": opts.get("--error", output).replace("%j", job_id), "argv": argv}
    write_job(job)
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "_run", job_id],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(job_id if "--parsable" in opts else f"Submitted batch job {job_id}")


def run_job(job_id):
    job = read_job(job_id)
    job.update(state="RUNNING", start=time.time())
    write_job(job)
    env = dict(os.environ, SLURM_JOB_ID=job_id, SLURM_JOB_NAME=job["name"])
    env.pop("SLURM_STEP_ID", None)
    os.makedirs(os.path.dirname(os.path.abspath(job["output"])), exist_ok=True)
    with open(job["output"], "w") as out, open(job["error"], "a") as err:
        rc, sig, timed_out = run_limited(["bash", "-c", job["wrap"]], job["cwd"], out, err,
                                         job["limit"], env)
    job = read_job(job_id)
    if job["state"] == "CANCELLED":
        return
    state = "TIMEOUT" if timed_out else "COMPLETED" if rc == 0 and not sig else "FAILED"
    job.update(state=state, exit=f"{rc}:{sig}", end=time.time())
    write_job(job)


def sacct(argv):
    opts, _ = parse(argv)
    for job_id in opts.get("-j", "").split(","):
        if not os.path.exists(job_path(job_id)):
            continue
        job = read_job(job_id)
        elapsed = int((job["end"] or time.time()) - job["start"]) if job["start"] else 0
        print(f"{job_id}|{job['state']}|{job['exit']}|{elapsed}")


def squeue(argv):
    opts, _ = parse(argv)
    if opts.get("-o") == "%L":
        if "FAKE_SLURM_TIMELEFT" in os.environ:
            print(os.environ["FAKE_SLURM_TIMELEFT"])
            return
        job = read_job(opts["-j"])
        left = max(0, int((job["start"] + job["limit"] - time.time()) * 60 / MINUTE))
        print(f"{left // 3600}:{left % 3600 // 60:02d}:{left % 60:02d}")
        return
    if "-j" in opts:
        if os.path.exists(job_path(opts["-j"])):
            job = read_job(opts["-j"])
            if job["state"] in ("PENDING", "RUNNING"):
                print(job["state"] if opts.get("-o") == "%T" else job["id"])
        return
    jobs_dir = os.path.join(STATE, "jobs")
    for name in sorted(os.listdir(jobs_dir)) if os.path.isdir(jobs_dir) else []:
        if name.endswith(".json"):
            job = read_job(name[:-5])
            if job["state"] in ("PENDING", "RUNNING"):
                print(job["id"])


def srun(argv):
    opts, command = parse(argv)
    if "SLURM_JOB_ID" not in os.environ:
        sys.exit("srun: error: no allocation (SLURM_JOB_ID unset)")
    env = dict(os.environ, SLURM_STEP_ID=str(int(time.time() * 1000) % 100000))
    cwd = opts.get("--chdir", os.getcwd())
    out = open(opts["--output"], "w") if "--output" in opts else None
    err = open(opts["--error"], "w") if "--error" in opts else None
    rc, sig, timed_out = run_limited(command, cwd, out, err,
                                     limit_seconds(opts.get("--time", "60")), env)
    if timed_out:
        print(f"slurmstepd: error: *** STEP {os.environ['SLURM_JOB_ID']}.0 ON fake CANCELLED "
              f"AT {time.strftime('%Y-%m-%dT%H:%M:%S')} DUE TO TIME LIMIT ***", file=sys.stderr)
        sys.exit(1)
    if sig:
        print(f"srun: error: fake: task 0: Killed by signal {sig}", file=sys.stderr)
        sys.exit(128 + sig)
    if rc:
        print(f"srun: error: fake: task 0: Exited with exit code {rc}", file=sys.stderr)
    sys.exit(rc)


def main():
    name = os.path.basename(sys.argv[0])
    if len(sys.argv) > 2 and sys.argv[1] == "_run":
        return run_job(sys.argv[2])
    if not STATE:
        sys.exit(f"{name}: set FAKE_SLURM_DIR")
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "calls.log"), "a") as log:
        log.write(shlex.join([name, *sys.argv[1:]]) + "\n")
    {"sbatch": sbatch, "sacct": sacct, "squeue": squeue, "srun": srun}[name](sys.argv[1:])


if __name__ == "__main__":
    main()
