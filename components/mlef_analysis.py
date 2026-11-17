#!/usr/bin/env python3
"""Analysis: libMLEF's mlef on the forecast ensemble. Needs a GPU.

Observations come from obs_dir if given, as observation_<step>.cgns files. Otherwise this is
a twin experiment: a truth run uses the members' settings with unperturbed initial
conditions, and the observations sample it at the analysis step. The truth restarts each
cycle from where the last analysis left it, so it advances with the ensemble.

Params:
  mlef_exe      default $MLEF_EXE, then external/libMLEF/mlef
  obs_exe       tools/make_obs.cpp's binary; default $MAKE_OBS_EXE
  observations  [[x, y, z, variable_id, variance], ...] sampled from the truth; default four
                density probes on the shock tube's plateaus, as libMLEF's
                scripts/make_shocktube_obs.py uses
  obs_dir       directory of observation files to use instead of a truth run

Writes truth/, observation_<step>.cgns, mlef.input, mlef.log and analysis/mem_XXX into
$ORCH_DIR, and passes analysis/ (and truth/plot/) on to the next cycle.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from euler_member import ROOT, forecast  # noqa: E402
from mlef_stub import gather, write_input  # noqa: E402

DEFAULT_OBSERVATIONS = [[0.10, 0.50, 0.50, 0, 1.0e-4], [0.20, 0.50, 0.50, 0, 1.0e-4],
                        [0.80, 0.50, 0.50, 0, 1.0e-4], [0.90, 0.50, 0.50, 0, 1.0e-4]]


def fail(message):
    sys.exit(f"mlef_analysis: {message}")


def executable(path, what):
    if not path or not os.access(path, os.X_OK):
        fail(f"{what} {path or '(unset)'} is not built; run scripts/<platform>/build.sh")
    return path


def observe(params, manifest, attempt_dir, step):
    """Return the observation file for step, generating it from the truth if needed."""
    name = f"observation_{step:06d}.cgns"
    if params.get("obs_dir"):
        path = os.path.join(params["obs_dir"], name)
        if not os.path.exists(path):
            fail(f"{path} is missing")
        return path, {}

    member_params = manifest["members"][0]["params"]
    truth_dir = os.path.join(attempt_dir, "truth")
    os.makedirs(truth_dir, exist_ok=True)
    restart = member_params.get("truth_dir") if manifest["cycle"] > 0 else None
    truth = forecast(member_params, truth_dir, manifest["cycle"], restart)
    if truth["step"] != step:
        fail(f"truth reached step {truth['step']}, the members {step}")

    obs_exe = executable(params.get("obs_exe") or os.environ.get("MAKE_OBS_EXE"), "make_obs")
    spec = "".join(" ".join(str(v) for v in point) + "\n"
                   for point in params.get("observations", DEFAULT_OBSERVATIONS))
    path = os.path.join(attempt_dir, name)
    done = subprocess.run([obs_exe, truth["snapshot"], path], input=spec, encoding="utf-8",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(done.stdout, end="")
    if done.returncode != 0:
        fail(f"make_obs exited {done.returncode}")
    return path, {"truth_dir": os.path.dirname(truth["snapshot"])}


def main():
    with open(os.environ["ORCH_PARAMS"]) as handle:
        params = json.load(handle)
    with open(os.environ["ORCH_MANIFEST"]) as handle:
        manifest = json.load(handle)
    attempt_dir = os.environ["ORCH_DIR"]
    mlef = executable(params.get("mlef_exe") or os.environ.get("MLEF_EXE")
                      or os.path.join(ROOT, "external", "libMLEF", "mlef"), "mlef")

    results, step, prefix = gather(manifest, attempt_dir)
    observations, carry = observe(params, manifest, attempt_dir, step)
    input_file = write_input(attempt_dir, prefix, step, observations)

    print(f"running: {mlef} {input_file}", flush=True)
    with open(os.path.join(attempt_dir, "mlef.log"), "w") as log:
        done = subprocess.run([mlef, input_file], cwd=attempt_dir, stdout=log,
                              stderr=subprocess.STDOUT)
    with open(os.path.join(attempt_dir, "mlef.log")) as log:
        output = log.read()
    print(output[-4000:], end="", file=sys.stderr if done.returncode else sys.stdout)
    if done.returncode != 0:
        fail(f"mlef exited {done.returncode}")

    analysis_root = os.path.join(attempt_dir, "analysis")
    for index, result in enumerate(results):
        written = os.path.join(analysis_root, f"mem_{index:03d}",
                               os.path.basename(result["snapshot"]))
        if not os.path.exists(written):
            fail(f"mlef did not write {written}")
    print(f"cycle {manifest['cycle']}: MLEF analysis of {len(results)} members at step {step} "
          f"-> {analysis_root}")
    with open(os.path.join(attempt_dir, "next_cycle.json"), "w") as handle:
        json.dump({"continue": True, "params": dict(carry, restart_dir=analysis_root)}, handle)


if __name__ == "__main__":
    main()
