#!/usr/bin/env python3
"""Ensemble member: one euler-miniapp forecast segment.

Cycle c runs to step (c + 1) * steps_per_cycle. Cycle 0 starts from the input file's
initial conditions, each perturbed by (1 + sigma * z) with z ~ N(0, 1) seeded by
seed_base + member, as libMLEF's scripts/run_ensemble.sh does. Later cycles restart from
this member's file in restart_dir, which the analysis passes on through next_cycle.json.

Params (from the campaign config, plus restart_dir from the analysis):
  exe              euler-miniapp binary; default $EULER_EXE, then the CPU build in external/
  input            input file; default ShockTube.input from euler-miniapp
  spacedim         dimension HashBrick was built for; default $EULER_SPACEDIM, then 2
  steps_per_cycle  required
  overrides        {"key": value} ParmParse overrides, e.g. {"number_cells": "31 31"};
                   plotting_interval defaults to steps_per_cycle
  perturb          {"sigma": 0.05, "seed_base": 1, "keys": {"SHOCK.pressureLow": 10000, ...}}
  restart_dir      directory holding mem_XXX/<prefix><step>.cgns from the last analysis

Writes plot/ (the CGNS output) and result.json into $ORCH_DIR. Exits 20 if the solution
went non-finite, since euler-miniapp itself still exits 0 then.
"""
import json
import os
import random
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EULER_DIR = os.path.join(ROOT, "external", "euler-miniapp")
NUMERICAL_INSTABILITY = 20
# euler-miniapp's ParmParse copies each token into a 200-byte stack buffer without checking,
# so a longer value (an absolute path on scratch, say) crashes it. Paths are passed relative
# to the attempt directory to stay short.
MAX_VALUE = 199


def fail(message, code=1):
    print(f"euler_member: {message}", file=sys.stderr)
    sys.exit(code)


def problem_type(input_file):
    with open(input_file) as handle:
        match = re.search(r'^\s*problem_type\s*=\s*"?(\w+)"?', handle.read(), re.MULTILINE)
    if not match:
        fail(f"no problem_type in {input_file}")
    return match.group(1)


def snapshot_name(problem, spacedim, step):
    return f"{problem}_{spacedim}d_{step:06d}.cgns"


def perturbed(perturb, member):
    rng = random.Random(perturb.get("seed_base", 1) + member)
    sigma = perturb.get("sigma", 0.05)
    # Densities and pressures must stay positive.
    return {key: max(base * (1 + sigma * rng.gauss(0, 1)), 1e-12 * abs(base))
            for key, base in sorted(perturb.get("keys", {}).items())}


def main():
    with open(os.environ["ORCH_PARAMS"]) as handle:
        params = json.load(handle)
    attempt_dir = os.environ["ORCH_DIR"]
    member, cycle = int(os.environ["ORCH_MEMBER_ID"]), int(os.environ["ORCH_CYCLE"])

    exe = params.get("exe") or os.environ.get("EULER_EXE") or os.path.join(EULER_DIR, "main_cpu")
    input_file = params.get("input") or os.path.join(EULER_DIR, "inputFiles", "ShockTube.input")
    spacedim = int(params.get("spacedim") or os.environ.get("EULER_SPACEDIM") or 2)
    if "steps_per_cycle" not in params:
        fail("params.steps_per_cycle is required")
    if not os.access(exe, os.X_OK):
        fail(f"{exe} is not built; run scripts/<platform>/build.sh")
    problem = problem_type(input_file)
    step = (cycle + 1) * params["steps_per_cycle"]

    settings = {str(k): str(v) for k, v in params.get("overrides", {}).items()}
    # The final step is only written if the plotting interval lands on it.
    settings.setdefault("plotting_interval", str(params["steps_per_cycle"]))
    settings.update(plot_dir="plot", number_time_steps=str(step))
    if cycle == 0 or not params.get("restart_dir"):
        settings.update({k: f"{v:.10g}" for k, v in
                         perturbed(params.get("perturb", {}), member).items()})
    else:
        restart_dir = os.path.join(params["restart_dir"], f"mem_{member:03d}")
        name = snapshot_name(problem, spacedim, step - params["steps_per_cycle"])
        if not os.path.exists(os.path.join(restart_dir, name)):
            fail(f"restart file {os.path.join(restart_dir, name)} is missing")
        # A link to the directory, not the file: the snapshot finds its grid file relative
        # to the directory it was opened from.
        link = os.path.join(attempt_dir, "restart")
        if os.path.islink(link):
            os.remove(link)
        os.symlink(restart_dir, link)
        settings["cgns_restart"] = os.path.join("restart", name)

    for key, value in settings.items():
        if len(value) > MAX_VALUE:
            fail(f"{key} is {len(value)} characters; euler-miniapp crashes above {MAX_VALUE}")

    command = [exe, input_file]
    for key, value in settings.items():
        command += [key, "=", *value.split()]
    print("running:", " ".join(command), flush=True)
    with open(os.path.join(attempt_dir, "euler.log"), "w") as log:
        done = subprocess.run(command, cwd=attempt_dir, stdout=log, stderr=subprocess.STDOUT)
    with open(os.path.join(attempt_dir, "euler.log")) as log:
        output = log.read()
    if done.returncode != 0:
        print(output[-3000:], file=sys.stderr)
        fail(f"euler-miniapp exited {done.returncode}")
    # The conservation report at the end prints nan once the solution has blown up.
    if re.search(r"difference: -+ [-+]?(nan|inf)", output, re.IGNORECASE):
        fail("solution is not finite", NUMERICAL_INSTABILITY)

    snapshot = os.path.join(attempt_dir, "plot", snapshot_name(problem, spacedim, step))
    if not os.path.exists(snapshot):
        fail(f"expected output {snapshot} was not written")
    with open(os.path.join(attempt_dir, "result.json"), "w") as handle:
        json.dump({"step": step, "problem": problem, "spacedim": spacedim,
                   "snapshot": snapshot, "initial": settings}, handle, indent=1)
    print(f"member {member} cycle {cycle}: wrote {snapshot}")


if __name__ == "__main__":
    main()
