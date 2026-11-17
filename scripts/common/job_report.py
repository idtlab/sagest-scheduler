#!/usr/bin/env python3
"""Print one line per job attempt in a campaign: how it ended, where it ran (from the job.json
the components write) and how long each phase took. For Slurm runs, then print sacct's view
of the same jobs: nodes, allocated resources, memory high-water mark and elapsed time.

Usage: job_report.py RUN_DIR
"""
import glob
import json
import os
import re
import subprocess
import sys

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
                if job.isdigit():
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

    # Allocation-mode runs record step ids of their own; Slurm knows them under the
    # allocation, whose id is in the driver log's name.
    job_ids += [re.sub(r"\D", "", os.path.basename(path))
                for path in glob.glob(os.path.join(run_dir, "driver-*.out"))]
    job_ids = [j for j in dict.fromkeys(job_ids) if j]
    if job_ids and any(info.get("scheduler") == "slurm" for info in infos) and _has("sacct"):
        print("\nsacct:")
        sys.stdout.flush()
        subprocess.run(["sacct", "-j", ",".join(job_ids), "-o", SACCT_FORMAT])


def _has(name):
    return any(os.access(os.path.join(d, name), os.X_OK)
               for d in os.environ.get("PATH", "").split(os.pathsep))


if __name__ == "__main__":
    main()
