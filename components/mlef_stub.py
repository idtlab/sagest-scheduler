#!/usr/bin/env python3
"""Analysis stand-in for libMLEF's mlef, for machines without a GPU.

Lays out the forecast ensemble the way mlef reads it (forecast/mem_XXX, plus an mlef input
file) and checks every member reached the same step. Then it does an identity analysis:
each member's forecast, with its grid file, is copied to analysis/mem_XXX unchanged, which
is where mlef would write the analysis ensemble. Members of the next cycle restart from
there through restart_dir.
"""
import glob
import json
import os
import shutil
import sys


def gather(manifest, attempt_dir):
    """Link each member's plot directory as forecast/mem_XXX and return the members'
    results, the common step and the snapshot file prefix."""
    forecast_root = os.path.join(attempt_dir, "forecast")
    os.makedirs(forecast_root, exist_ok=True)
    results = []
    for index, member in enumerate(manifest["members"]):
        with open(os.path.join(member["dir"], "result.json")) as handle:
            results.append(json.load(handle))
        link = os.path.join(forecast_root, f"mem_{index:03d}")
        if not os.path.islink(link):
            os.symlink(os.path.dirname(results[-1]["snapshot"]), link)
    steps = {r["step"] for r in results}
    if len(steps) != 1:
        sys.exit(f"members ended at different steps {sorted(steps)}")
    return results, steps.pop(), f"{results[0]['problem']}_{results[0]['spacedim']}d_"


def write_input(attempt_dir, prefix, step, observation_file=None):
    path = os.path.join(attempt_dir, "mlef.input")
    with open(path, "w") as handle:
        handle.write(f"ensemble_root       = {os.path.join(attempt_dir, 'forecast')}\n"
                     f"member_prefix       = mem_\n"
                     f"filename_prefix     = {prefix}\n"
                     f"snapshot_step       = {step}\n")
        if observation_file:
            handle.write(f"observation_file    = {observation_file}\n")
        handle.write(f"analysis_output_dir = {os.path.join(attempt_dir, 'analysis')}\n")
    return path


def main():
    with open(os.environ["ORCH_MANIFEST"]) as handle:
        manifest = json.load(handle)
    attempt_dir = os.environ["ORCH_DIR"]
    analysis_root = os.path.join(attempt_dir, "analysis")
    results, step, prefix = gather(manifest, attempt_dir)
    write_input(attempt_dir, prefix, step)

    for index, result in enumerate(results):
        out = os.path.join(analysis_root, f"mem_{index:03d}")
        os.makedirs(out, exist_ok=True)
        shutil.copy2(result["snapshot"], out)
        # The snapshot links its coordinates and elements to this file by relative path.
        for grid in glob.glob(os.path.join(os.path.dirname(result["snapshot"]),
                                           f"{result['problem']}_grid_*.cgns")):
            shutil.copy2(grid, out)

    print(f"cycle {manifest['cycle']}: identity analysis of {len(results)} members "
          f"at step {step} -> {analysis_root}")
    with open(os.path.join(attempt_dir, "next_cycle.json"), "w") as handle:
        json.dump({"continue": True, "params": {"restart_dir": analysis_root}}, handle)


if __name__ == "__main__":
    main()
