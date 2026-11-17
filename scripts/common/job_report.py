#!/usr/bin/env python3
"""Print one line per job attempt in a campaign: how it ended, where it ran (from the job.json
the components write) and how long each phase took. Then print the scheduler's view of the
same jobs: sacct's nodes, allocated resources, memory high-water mark and elapsed time, or
flux jobs' nodes, cores and runtime. Jobs inside a Flux allocation ended with it, so
only the allocation itself is shown for those.

Usage: job_report.py RUN_DIR
"""
import glob
import json
import os
import re
import subprocess
import sys

FLUX_FORMAT = ("{id.f58:>12} {name:<36.36} {queue:<8} {nnodes:>6} {nodelist:<20} {ncores:>6} "
               "{runtime!H:>10} {status:<10} {returncode:>4}")
SACCT_FORMAT = ("JobID%-16,JobName%-36,QOS,Account,NNodes,NodeList%-16,AllocCPUS,AllocTRES%-44,"
                "MaxRSS,Elapsed,State,ExitCode")


def read_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def main():
    run_dir = sys.argv[1]
    state = read_json(os.path.join(run_dir, "state.json"))
    if not state:
        sys.exit(f"no state.json in {run_dir}")
    rows = [("task", "try", "job", "ended", "sched s", "where", "cpus", "gpus", "phases (s)")]
    job_ids = []
    for index, cycle in enumerate(state["cycles"]):
        tasks = cycle["members"] + ([cycle["analysis"]] if cycle["analysis"] else [])
        for task in tasks:
            for entry in task["history"]:
                job = str(entry.get("job") or "")
                if task.get("mode") == "login" or job.isdigit():
                    job_ids.append(job)
                attempt_dir = os.path.join(os.path.dirname(task["dir"]),
                                           f"attempt_{entry['attempt']}")
                info = read_json(os.path.join(attempt_dir, "job.json"))
                where = info.get("nodelist") or info.get("host", "?")
                if info.get("nodes"):
                    where = f"{info['nodes']}x {where}"
                phases = " ".join(f"{k}={v}" for k, v in info.get("seconds", {}).items())
                rows.append((f"c{index} {task['name']}", str(entry["attempt"]), job,
                             entry.get("reason") or entry.get("kind", ""),
                             f"{entry['runtime']:.1f}" if entry.get("runtime") is not None else "", where,
                             str(info.get("cpus_usable", "?")),
                             "; ".join(info.get("gpus", [])) or "-", phases))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)) + "  " + row[-1])

    infos = [read_json(path) for path in
             glob.glob(os.path.join(run_dir, "cycle_*", "*", "attempt_*", "job.json"))]
    accounts = {info["account"] for info in infos if info.get("account")}
    if accounts:
        print(f"\naccounts used: {', '.join(sorted(accounts))}")

    # Allocation-mode runs record ids the outer scheduler doesn't know (Slurm steps of their
    # own, or jobs of the allocation's Flux instance); the allocation's id is in the driver
    # log's name, and sacct lists its steps.
    job_ids += [re.fullmatch(r"driver-(.+)\.out", os.path.basename(path)).group(1)
                for path in glob.glob(os.path.join(run_dir, "driver-*.out"))]
    job_ids = [j for j in dict.fromkeys(job_ids) if j and not j.startswith("step-")]
    schedulers = {info.get("scheduler") for info in infos}
    if job_ids and "slurm" in schedulers and _has("sacct"):
        print("\nsacct:")
        sys.stdout.flush()
        subprocess.run(["sacct", "-j", ",".join(job_ids), "-o", SACCT_FORMAT])
    elif job_ids and "flux" in schedulers and _has("flux"):
        print("\nflux jobs:")
        sys.stdout.flush()
        subprocess.run(["flux", "jobs", "-o", FLUX_FORMAT, *job_ids])


def _has(name):
    return any(os.access(os.path.join(d, name), os.X_OK)
               for d in os.environ.get("PATH", "").split(os.pathsep))


if __name__ == "__main__":
    main()
