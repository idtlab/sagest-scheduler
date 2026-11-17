#!/usr/bin/env python3
"""Check a finished euler-miniapp campaign: it completed every cycle, each member of a later
cycle restarted from the previous analysis, and every member wrote its final snapshot.
Prints one line per check and exits nonzero if any failed.

Usage: check_euler_run.py RUN_DIR
"""
import json
import os
import sys

run_dir = sys.argv[1]
with open(os.path.join(run_dir, "state.json")) as handle:
    state = json.load(handle)
failures = []


def check(ok, message):
    print(("ok    " if ok else "FAIL  ") + message)
    if not ok:
        failures.append(message)


check(state["status"] == "completed", f"campaign status is {state['status']}: {state['message']}")
for index, cycle in enumerate(state["cycles"]):
    members = cycle["members"]
    check(all(m["status"] == "DONE" for m in members),
          f"cycle {index}: {sum(m['status'] == 'DONE' for m in members)}/{len(members)} members done")
    check(bool(cycle["analysis"]) and cycle["analysis"]["status"] == "DONE",
          f"cycle {index}: analysis done")
    for member in members:
        if member["status"] != "DONE":
            continue
        with open(os.path.join(member["dir"], "result.json")) as handle:
            result = json.load(handle)
        check(os.path.exists(result["snapshot"]),
              f"cycle {index} {member['name']}: step {result['step']} snapshot written")
        if index > 0:
            with open(os.path.join(member["dir"], "euler.log")) as handle:
                restarted = "Resumed from CGNS restart" in handle.read()
            check(restarted, f"cycle {index} {member['name']}: restarted from the analysis")
    retried = [m["name"] for m in members if m["attempt"] > 1]
    if retried:
        print(f"note  cycle {index}: retried {', '.join(retried)}")

print(f"\n{'all checks passed' if not failures else f'{len(failures)} check(s) failed'}")
sys.exit(1 if failures else 0)
